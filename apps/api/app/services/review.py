"""Human review queue (GET /review/queue).

Surfaces everything that needs a human ruling, so nothing the AI processed
silently waits to be found by scrolling the graph (design: GPT dialogue
workflow vol.7 "AI Review Queue"; boundary-rules §6 low-confidence badge).
Computed on read like the recommendation engine; items leave the queue once
the underlying state is fixed (assigned, merged, closed).

Four categories:
- low_confidence:    analysis confidence < 0.7
- dna_unassigned:    creatives without a DNA family
- merge_candidates:  market-pair split (same filename modulo market prefix
                     in different creatives) and borderline similarity (token
                     score in [0.20, CLUSTER_THRESHOLD) — just below the
                     auto-cluster bar, the human ruling zone)
- observation_pairs: open SIMILAR_TO pairs with both sides' spend/cpp
- pending_verdicts:  裂变边待人工判定有效/无效（轻量实验复核）
- derivation_reviews: 测量层/LLM 对裂变因子的复核建议（judge_suggestions
                     kind="derivation-factor"）：疑似误链待解链、因子建议待采纳
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Sequence

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import (
    AnalysisResult,
    Creative,
    CreativeAsset,
    CreativeVariant,
    JudgeSuggestion,
    Performance,
    SplitRuling,
)
from app.schemas.review import ReviewItem
from app.services import markets
from app.services import recommendation as rec
from app.services.clustering import TEXT_CLUSTER_THRESHOLD

if TYPE_CHECKING:
    from app.services.creative_score import ScoreBreakdown
    from app.services.recommendation import CreativeMetrics

LOW_CONFIDENCE_THRESHOLD = 0.7
BORDERLINE_LOW = 0.20
BORDERLINE_HIGH = TEXT_CLUSTER_THRESHOLD  # 0.34
BORDERLINE_LIMIT = 20

def _market_key(
    filename: str, prefixes: tuple[str, ...] | None = None
) -> str:
    """Filename identity ignoring the market prefix（前缀见 services/markets）。"""
    return markets.market_key(filename, prefixes or markets.DEFAULT_MARKET_PREFIXES)


def short_label(
    filename: str, prefixes: tuple[str, ...] | None = None
) -> str:
    """Display label for a variant: market tag + the distinguishing tail.

    Filenames share a long boilerplate and differ at the end (V1/V2/B版/
    前贴类型/画幅), so the readable label is the last dash-segments:
    "KS_EN-260611-...-屡次失败重开Ai片头V1-竖.mp4" -> "EN …屡次失败重开Ai片头V1-竖".
    """
    market, stem = markets.market_tag(
        filename, prefixes or markets.DEFAULT_MARKET_PREFIXES
    )
    segments = stem.split("-")
    tail = "-".join(segments[-2:]) if len(segments) >= 2 else stem
    if len(tail) < 6 and len(segments) >= 3:
        tail = "-".join(segments[-3:])
    return f"{market} …{tail}".strip()


def low_confidence_items(db: Session) -> list[ReviewItem]:
    stmt = (
        select(AnalysisResult, CreativeAsset)
        .join(CreativeAsset, CreativeAsset.id == AnalysisResult.asset_id)
        .where(AnalysisResult.confidence < LOW_CONFIDENCE_THRESHOLD)
        .order_by(AnalysisResult.confidence)
    )
    return [
        ReviewItem(
            kind="low_confidence",
            title=asset.filename,
            reason=(
                f"AI 置信度 {analysis.confidence:.2f} 低于 "
                f"{LOW_CONFIDENCE_THRESHOLD}，需人工复核"
            ),
            asset_id=asset.id,
        )
        for analysis, asset in db.execute(stmt).all()
    ]


def dna_unassigned_items(db: Session) -> list[ReviewItem]:
    creatives = list(
        db.scalars(select(Creative).where(Creative.dna_id.is_(None)).order_by(Creative.name))
    )
    suggestions = {
        row.left_id: row
        for row in db.scalars(
            select(JudgeSuggestion).where(JudgeSuggestion.kind == "dna_assign")
        ).all()
    }
    # 建议文本实时解析：right_id 指向的家族可能改名/合并过（D15 改名后
    # 建议里还留着旧名）——展示名以当前 creative_dnas 为准，写入时的
    # verdict 字符串只作存档
    from app.models import CreativeDNA

    dna_names = {
        dna.id: f"{dna.code} {dna.name}"
        for dna in db.scalars(
            select(CreativeDNA).where(CreativeDNA.status == "active")
        ).all()
    }
    items: list[ReviewItem] = []
    for creative in creatives:
        variants = [
            v for v in db.scalars(
                select(CreativeVariant).where(CreativeVariant.creative_id == creative.id)
            )
        ]
        suggestion = suggestions.get(creative.id)
        suggestion_text = suggestion.verdict if suggestion else None
        if suggestion and suggestion.right_id in dna_names:
            suggestion_text = dna_names[suggestion.right_id]
        items.append(
            ReviewItem(
                kind="dna_unassigned",
                title=creative.name,
                reason=f"未归族 DNA（{len(variants)} 个 Variant），需人工确认家族",
                creative_id=creative.id,
                creative_name=creative.name,
                suggestion=suggestion_text,
                suggestion_votes=suggestion.votes if suggestion else None,
                suggestion_reason=suggestion.reason if suggestion else None,
                suggested_dna_id=suggestion.right_id if suggestion else None,
            )
        )
    return items


def market_conflict_items(db: Session) -> list[ReviewItem]:
    """市场存疑：文件名前缀与 AI 分析识别的市场标签冲突（低优先级提示）。

    市场词缀是命名约定不是事实（与裂变因子的教训同类）；冲突的
    creative 不进遥测基准桶（宁可缺数据不污染基准），这里提示人工
    核对文件名。仅提示，无操作按钮。
    """
    names = {c.id: c.name for c in db.scalars(select(Creative)).all()}
    items: list[ReviewItem] = []
    for creative_id, (market, confidence) in sorted(
        markets.resolve_creative_markets(db).items(), key=lambda kv: names.get(kv[0], "")
    ):
        if confidence != "conflict":
            continue
        name = names.get(creative_id)
        if name is None:
            continue
        items.append(
            ReviewItem(
                kind="market_conflict",
                title=name,
                reason=(
                    f"文件名前缀市场（{market or '无'}）与 AI 分析识别的市场标签"
                    "不一致，请核对文件名；核对前该创意不计入市场基准"
                ),
                creative_id=creative_id,
                creative_name=name,
            )
        )
    return items


# 品类通用词：它们让名称相似度虚高（两条素材仅因共享 "-village-builder"
# 就压线 0.20），计算前过滤——默认值在 services/markets，可按项目配置。
def _signal_tokens(name: str, generic_tokens: set[str]) -> set[str]:
    from app.services.clustering import _tokens  # 复用 CJK bigram 逻辑

    return {token for token in _tokens(name) if token not in generic_tokens}


def _filtered_similarity(name_a: str, name_b: str, generic_tokens: set[str]) -> float:
    a, b = _signal_tokens(name_a, generic_tokens), _signal_tokens(name_b, generic_tokens)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _observation_pair_refs() -> set[frozenset[str]]:
    """Creative-id pairs already registered as observation pairs (维持拆分).

    Neo4j 不可用时返回空集——宁可让观察对短暂回到候选列表，也不阻塞队列。
    """
    try:
        from app.config import get_settings
        from app.services import graph_sync

        pairs = graph_sync.get_graph_repository(get_settings()).read_similar_pairs()
        return {frozenset(pair) for pair in pairs}
    except Exception:  # noqa: BLE001
        return set()


def merge_candidate_items(db: Session) -> list[ReviewItem]:
    items: list[ReviewItem] = []

    # 已登记为观察对（维持拆分）的组合不再出现在候选里——否则用户点完
    # "维持拆分"刷新后又看到同一对，等于没处理（收件箱必须只进不出）。
    observed = _observation_pair_refs()
    # 已结案（split_rulings）的组合永久排除——结案只删 SIMILAR_TO 边，
    # 不留裁决的话同一对会跑步机式回流候选（v0.11 教训）。
    ruled = {
        frozenset((row.name_a, row.name_b))
        for row in db.scalars(select(SplitRuling)).all()
    }
    prefixes = markets.resolve_market_prefixes(db)
    generic_tokens = markets.resolve_generic_tokens(db)

    # Heuristic 1: same filename modulo market prefix in different creatives.
    stmt = (
        select(CreativeAsset, CreativeVariant, Creative)
        .join(CreativeVariant, CreativeVariant.asset_id == CreativeAsset.id)
        .join(Creative, Creative.id == CreativeVariant.creative_id)
        .where(CreativeAsset.file_type != "excel")
        .where(Creative.lifecycle_state != "archived")
    )
    groups: dict[str, list[tuple[CreativeAsset, Creative]]] = {}
    for asset, _variant, creative in db.execute(stmt).all():
        groups.setdefault(_market_key(asset.filename, prefixes), []).append(
            (asset, creative)
        )
    for _key, members in sorted(groups.items()):
        creative_ids = {creative.id for _asset, creative in members}
        if len(creative_ids) < 2:
            continue
        first_asset, first_creative = members[0]
        others = sorted(
            {
                (creative.id, creative.name)
                for _asset, creative in members
                if creative.id != first_creative.id
            }
        )
        other_id, other_name = others[0]
        if frozenset({first_creative.id, other_id}) in observed:
            continue
        if frozenset((first_creative.name, other_name)) in ruled:
            continue
        items.append(
            ReviewItem(
                kind="merge_candidate",
                title=f"{first_creative.name} ↔ {other_name}",
                reason="多市场文件名相同但分属不同 Creative（语言对未合并）",
                creative_id=first_creative.id,
                creative_name=first_creative.name,
                related_creative_id=other_id,
                related_creative_name=other_name,
            )
        )

    # Heuristic 2: borderline name/text similarity just under the cluster bar.
    # Pairs with a prior split ruling (case-rulings.json) are already decided —
    # they must not resurface as candidates (the merge guard blocks them anyway).
    from app.services.merge_guard import find_prior_ruling

    # archived creative 不进合并候选（已人工归档，不再折腾）
    creatives = list(
        db.scalars(
            select(Creative)
            .where(Creative.lifecycle_state != "archived")
            .order_by(Creative.name)
        ).all()
    )
    scored: list[tuple[float, Creative, Creative]] = []
    for index, left in enumerate(creatives):
        for right in creatives[index + 1 :]:
            if frozenset({left.id, right.id}) in observed:
                continue
            if frozenset((left.name, right.name)) in ruled:
                continue
            if find_prior_ruling(left.name, right.name) is not None:
                continue
            score = 0.6 * _filtered_similarity(
                left.name, right.name, generic_tokens
            ) + 0.4 * (
                _filtered_similarity(
                    left.representative_text or left.name,
                    right.representative_text or right.name,
                    generic_tokens,
                )
            )
            if BORDERLINE_LOW <= score < BORDERLINE_HIGH:
                scored.append((score, left, right))
    scored.sort(key=lambda item: item[0], reverse=True)
    suggestions = {
        (row.left_id, row.right_id): row
        for row in db.scalars(
            select(JudgeSuggestion).where(JudgeSuggestion.kind == "merge_pair")
        ).all()
    }
    for score, left, right in scored[:BORDERLINE_LIMIT]:
        suggestion = suggestions.get((left.id, right.id)) or suggestions.get((right.id, left.id))
        items.append(
            ReviewItem(
                kind="merge_candidate",
                title=f"{left.name} ↔ {right.name}",
                reason=f"相似度 {score:.2f}，未达自动合并阈值 {BORDERLINE_HIGH}，疑似同创意",
                creative_id=left.id,
                creative_name=left.name,
                related_creative_id=right.id,
                related_creative_name=right.name,
                suggestion=suggestion.verdict if suggestion else None,
                suggestion_votes=suggestion.votes if suggestion else None,
                suggestion_reason=suggestion.reason if suggestion else None,
            )
        )

    # 漏网扫描器召回的对（视觉/分析通道，文本分可能低于带下限而从不在
    # 候选里）——有 merge 建议但不在上面候选里的必须并入，否则建议躺在
    # judge_suggestions 表里没人看见（猪三兄弟教训：slaughterhouse 对
    # 文本 0.10 从未浮出，扫描器捞出后收件箱仍不显示）
    shown = {frozenset((item.creative_id, item.related_creative_id)) for item in items}
    by_id = {creative.id: creative for creative in creatives}
    for (left_id, right_id), suggestion in suggestions.items():
        if suggestion.verdict != "merge" or right_id is None:
            continue
        pair = frozenset((left_id, right_id))
        left, right = by_id.get(left_id), by_id.get(right_id)
        if pair in shown or pair in observed or left is None or right is None:
            continue
        if frozenset((left.name, right.name)) in ruled:
            continue
        items.append(
            ReviewItem(
                kind="merge_candidate",
                title=f"{left.name} ↔ {right.name}",
                reason="扫描器召回（视觉/分析证据），LLM 建议合并",
                creative_id=left.id,
                creative_name=left.name,
                related_creative_id=right.id,
                related_creative_name=right.name,
                suggestion=suggestion.verdict,
                suggestion_votes=suggestion.votes,
                suggestion_reason=suggestion.reason,
            )
        )
    return items


def observation_pair_items(
    db: Session,
    *,
    max_date: date | None,
    all_performances: Sequence[Performance] | None = None,
) -> list[ReviewItem]:
    try:
        from app.config import get_settings
        from app.services import graph_sync

        pairs = graph_sync.get_graph_repository(get_settings()).read_similar_pairs()
    except Exception:  # noqa: BLE001 — Neo4j 不可用时观察对留空
        return []
    creatives = {c.id: c for c in db.scalars(select(Creative)).all()}
    suggestions = {
        (row.left_id, row.right_id): row
        for row in db.scalars(
            select(JudgeSuggestion).where(JudgeSuggestion.kind == "observation_pair")
        ).all()
    }
    items: list[ReviewItem] = []
    for source_ref, target_ref in pairs:
        source = creatives.get(source_ref)
        target = creatives.get(target_ref)
        if source is None or target is None:
            continue
        suggestion = suggestions.get((source.id, target.id)) or suggestions.get(
            (target.id, source.id)
        )

        def _brief(creative: Creative) -> str:
            rows = rec.collect_creative_performance(
                db, creative, all_performances=all_performances
            )
            metrics = rec.aggregate(
                creative, None, None, rows, max_date=max_date, variant_count=1
            )
            cpp = f"${metrics.cpp:,.2f}" if metrics.cpp else "-"
            return f"{creative.name}（消耗 ${metrics.spend:,.0f} / 成本 {cpp}）"

        items.append(
            ReviewItem(
                kind="observation_pair",
                title=f"{source.name} ↔ {target.name}",
                reason="观察对未结案：" + _brief(source) + " vs " + _brief(target),
                creative_id=source.id,
                creative_name=source.name,
                related_creative_id=target.id,
                related_creative_name=target.name,
                suggestion=suggestion.verdict if suggestion else None,
                suggestion_votes=suggestion.votes if suggestion else None,
                suggestion_reason=suggestion.reason if suggestion else None,
            )
        )
    return items


def pending_verdict_items(
    db: Session, *, all_performances: Sequence[Performance] | None = None
) -> list[ReviewItem]:
    """Derivations awaiting a human verdict (lightweight experiment review)."""
    from app.models import CreativeVariant, VariantDerivation
    from app.services.evolution import variant_brief

    stmt = (
        select(VariantDerivation, CreativeVariant, Creative)
        .join(
            CreativeVariant,
            CreativeVariant.id == VariantDerivation.source_variant_id,
        )
        .join(Creative, Creative.id == CreativeVariant.creative_id)
        .where(VariantDerivation.verdict == "pending")
        .order_by(Creative.name)
    )
    variants = {
        v.id: v
        for v in db.scalars(select(CreativeVariant)).all()
    }
    suggestions = {
        row.left_id: row
        for row in db.scalars(
            select(JudgeSuggestion).where(JudgeSuggestion.kind == "verdict")
        ).all()
    }
    items: list[ReviewItem] = []
    for derivation, _source_variant, creative in db.execute(stmt).all():
        source = variants.get(derivation.source_variant_id)
        target = variants.get(derivation.target_variant_id)
        if source is None or target is None:
            continue
        source_brief = variant_brief(db, source, all_performances=all_performances)
        target_brief = variant_brief(db, target, all_performances=all_performances)
        if source_brief.cpp is not None and target_brief.cpp is not None:
            delta = target_brief.cpp - source_brief.cpp
            delta_s = f"成本差 {delta:+.2f}"
        else:
            delta_s = "数据不足"
        title = (
            f"{creative.name}：{source.name[:18]} "
            f"-[{derivation.factor}]-> {target.name[:18]}"
        )
        suggestion = suggestions.get(derivation.id)
        items.append(
            ReviewItem(
                kind="pending_verdict",
                title=title,
                reason=f"裂变实验待判定（{delta_s}）",
                creative_id=creative.id,
                creative_name=creative.name,
                derivation_id=derivation.id,
                source_label=short_label(source_brief.filename),
                target_label=short_label(target_brief.filename),
                factor=derivation.factor,
                suggestion=suggestion.verdict if suggestion else None,
                suggestion_votes=suggestion.votes if suggestion else None,
                suggestion_reason=suggestion.reason if suggestion else None,
            )
        )
    return items


def derivation_review_items(db: Session) -> list[ReviewItem]:
    """裂变因子复核建议（review_derivations.py 测量层/LLM 产出，建议级）。

    已采纳（建议因子 == 当前因子）的跳过；边已删的孤儿建议顺手清掉
    （commit 持久化清理——收件箱只进不出，不能留幽灵条目）。
    """
    from app.models import CreativeVariant, VariantDerivation

    suggestions = list(
        db.scalars(
            select(JudgeSuggestion)
            .where(JudgeSuggestion.kind == "derivation-factor")
            .order_by(JudgeSuggestion.created_at)
        ).all()
    )
    if not suggestions:
        return []
    derivations = {d.id: d for d in db.scalars(select(VariantDerivation)).all()}
    variants = {v.id: v for v in db.scalars(select(CreativeVariant)).all()}
    assets = {a.id: a for a in db.scalars(select(CreativeAsset)).all()}
    creatives = {c.id: c for c in db.scalars(select(Creative)).all()}

    items: list[ReviewItem] = []
    orphans = 0
    for suggestion in suggestions:
        derivation = derivations.get(suggestion.left_id)
        if derivation is None:
            db.delete(suggestion)
            orphans += 1
            continue
        if suggestion.verdict == derivation.factor:
            continue  # 建议已被采纳（PUT 改了 factor），条目随状态消除
        source = variants.get(derivation.source_variant_id)
        target = variants.get(derivation.target_variant_id)
        if source is None or target is None:
            continue
        creative = creatives.get(source.creative_id)
        source_asset = assets.get(source.asset_id)
        target_asset = assets.get(target.asset_id)
        title = (
            f"{creative.name if creative else '?'}：{source.name[:18]} "
            f"-[{derivation.factor}]-> {target.name[:18]}"
        )
        if suggestion.verdict == "not-a-derivation":
            reason = f"疑似误链：{suggestion.reason}"
        else:
            reason = f"因子建议改为 {suggestion.verdict}：{suggestion.reason}"
        items.append(
            ReviewItem(
                kind="derivation_review",
                title=title,
                reason=reason,
                creative_id=creative.id if creative else None,
                creative_name=creative.name if creative else None,
                derivation_id=derivation.id,
                source_label=(
                    short_label(source_asset.filename) if source_asset else source.name
                ),
                target_label=(
                    short_label(target_asset.filename) if target_asset else target.name
                ),
                factor=derivation.factor,
                suggestion=suggestion.verdict,
                suggestion_votes=suggestion.votes,
                suggestion_reason=suggestion.reason,
            )
        )
    if orphans:
        db.commit()
    return items


def creative_scores(
    db: Session, report: "rec.ReportData | None" = None
) -> dict[str, tuple[ScoreBreakdown, CreativeMetrics]]:
    """全部 creative 的评分（creative_id → (breakdown, metrics)）。

    收件箱建议归档与自动流转共用同一份计算，避免两边分数口径漂移；
    调用方已有 report（recommendations 路由）时传入复用，不重复聚合。
    """
    from app.services.creative_score import score_creative
    from app.services.settings import ScoreConfig, resolve_score_config

    if report is None:
        report = rec.build_report(db, list(db.scalars(select(Creative)).all()))
    # 效果分锚点按 creative 主市场取阈值（分市场 CPP 量级不同），
    # 同市场共享一份 ScoreConfig，避免每 creative 重读 settings 表
    score_configs: dict[str, ScoreConfig] = {}

    def _config_for(market: str) -> ScoreConfig:
        if market not in score_configs:
            score_configs[market] = resolve_score_config(db, market or None)
        return score_configs[market]

    confidence_means = {
        creative_id: float(mean)
        for creative_id, mean in db.execute(
            text(
                "select v.creative_id, avg(a.confidence) "
                "from analysis_results a "
                "join creative_variants v on v.asset_id = a.asset_id "
                "group by v.creative_id"
            )
        ).fetchall()
    }
    return {
        metrics.creative_id: (
            score_creative(
                metrics,
                confidence_means.get(metrics.creative_id),
                _config_for(metrics.main_market),
            ),
            metrics,
        )
        for metrics, _action, _reasons in report.items
    }


def archive_suggestion_items(db: Session) -> list[ReviewItem]:
    """建议归档：score 低于阈值且长期无消耗、当前非 archived 的 creative。

    归档不静默——只进收件箱，人工确认才置 archived（Human > AI）。
    """
    from app.services import lifecycle
    from app.services.settings import resolve_score_config

    config = resolve_score_config(db)
    if not config.auto_enabled:
        return []
    states = {
        c.id: c.lifecycle_state for c in db.scalars(select(Creative)).all()
    }
    items: list[ReviewItem] = []
    for creative_id, (score, metrics) in creative_scores(db).items():
        if states.get(creative_id) == "archived":
            continue
        auto_state = lifecycle.compute_auto_state(
            score.total, metrics.days_idle, config
        )
        if auto_state != "archived":
            continue
        items.append(
            ReviewItem(
                kind="archive_suggestion",
                title=metrics.creative_name,
                reason=(
                    f"评分 {score.total:.0f}（效果 {score.performance:.0f} / "
                    f"新鲜 {score.freshness:.0f} / 演化 {score.evolution:.0f} / "
                    f"置信 {score.confidence:.0f}），"
                    f"已 {metrics.days_idle} 天无消耗，建议归档"
                ),
                creative_id=creative_id,
                creative_name=metrics.creative_name,
            )
        )
    items.sort(key=lambda item: item.reason)
    return items


def threshold_calibration_items(db: Session) -> list[ReviewItem]:
    """合并阈值校准建议（services/threshold_calibration 产出，只建议不改）。"""
    return [
        ReviewItem(
            kind="threshold_calibration",
            title="合并阈值校准",
            reason=row.reason,
        )
        for row in db.scalars(
            select(JudgeSuggestion).where(
                JudgeSuggestion.kind == "threshold_calibration"
            )
        ).all()
    ]


def market_detect_items(db: Session) -> list[ReviewItem]:
    """检测到的未配置市场前缀（services/market_detect 产出，仅引导建议）。"""
    return [
        ReviewItem(
            kind="market_detect",
            title=f"市场前缀 {row.verdict}",
            reason=row.reason,
        )
        for row in db.scalars(
            select(JudgeSuggestion).where(JudgeSuggestion.kind == "market_detect")
        ).all()
    ]
