"""Creative-level recommendation engine (rule-based, explainable).

Outputs one of KEEP / ITERATE / PAUSE / ARCHIVE per creative plus
human-readable reasons with the numbers behind them (design-principles
P05). No AI calls, no persistence — computed on read from the performance
rows matched to the creative's assets.

Thresholds follow the UA team's documented metric priorities
(AGENTS.md §6): payer cost red line $120, D1 ROAS green line 2%.
All thresholds are module constants so tuning stays a one-line change.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from typing import Literal, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Creative,
    CreativeAsset,
    CreativeDNA,
    CreativeVariant,
    Performance,
    VariantDerivation,
)
from app.repositories.assets import AssetRepository
from app.repositories.creatives import VariantRepository
from app.repositories.performance import PerformanceRepository
from app.services.excel import metrics_from_raw
from app.services.matching import (
    PerformanceIndex,
    index_performances,
    match_rows,
    matches,
    normalize,
)
from app.services.settings import (
    DEFAULT_JUDGE_METRICS,
    DEFAULT_THRESHOLDS,
    MetricConfig,
    resolve_metric_config,
)

RecommendationAction = Literal["KEEP", "ITERATE", "PAUSE", "ARCHIVE"]


@dataclass
class ReasonBit:
    """Context-only supplementary reason in i18n-ready form (code + params).

    The frontend renders ``brief.bit.<code>`` templates and falls back to the
    legacy Chinese rendering (``render_reason_zh``) when a template is missing
    (old data / future codes).
    """

    code: str
    params: dict[str, float | int | str]


@dataclass
class Verdict:
    """Structured recommendation: the rule that fired, in machine form.

    ``reason_code`` + ``params`` are the i18n-ready form (frontend renders the
    one-line decision from them); ``reasons`` is the legacy Chinese rendering,
    byte-identical to the pre-Verdict strings so existing consumers (node
    panel, tooltips, telemetry) keep working. ``supplementary`` carries the
    context-only reasons in ReasonBit form (its Chinese rendering is also
    appended to ``reasons``). ``priority_dollars`` / ``confidence`` are filled
    by build_report via services/priority.
    """

    action: RecommendationAction
    reason_code: str
    params: dict[str, float | int | str | None]
    reasons: list[str]
    supplementary: list[ReasonBit] = field(default_factory=list)
    priority_dollars: float = 0.0
    confidence: float = 0.0

# 默认值 = services/settings.DEFAULT_THRESHOLDS；用户在 Settings 页改阈值后，
# 引擎读配置（resolve_metric_config），这些模块常量仅作缺省与测试锚点。
CPP_RED_LINE = DEFAULT_THRESHOLDS["cpp_red_line"]  # 付费成本红线（≥120 红）
CPP_PAUSE_LINE = DEFAULT_THRESHOLDS["cpp_pause_line"]  # 红线 1.5 倍，直接暂停
CPP_EFFICIENT = DEFAULT_THRESHOLDS["cpp_efficient"]  # 效率领先线
ROAS_GREEN_LINE = DEFAULT_THRESHOLDS["roas_green_line"]  # D1 Roas 绿线（>2% 绿）
ROAS_WEAK_LINE = DEFAULT_THRESHOLDS["roas_weak_line"]
SPEND_MIN_JUDGE = 50.0  # 低于此消耗不做暂停/ROAS/留存判定
PAYERS_MIN_JUDGE = 3  # 付费样本 <3 时 CPP 波动是倍数级，不做 CPP 类方向性判定
SPEND_SIGNIFICANT = 1000.0  # 起量线
IDLE_DAYS_ARCHIVE = 14
RECENT_WINDOW_DAYS = 7
TREND_THRESHOLD = 0.30  # 近 7 天 cpp 偏离整体 ±30% 记为趋势

_DEFAULT_METRIC_CONFIG = MetricConfig(
    profile=[],
    judge_metrics=list(DEFAULT_JUDGE_METRICS),
    thresholds=dict(DEFAULT_THRESHOLDS),
)


@dataclass
class CreativeMetrics:
    creative_id: str
    creative_name: str
    dna_code: str | None
    dna_name: str | None
    spend: float
    payers: int
    installs: int
    cpp: float | None  # None 表示无付费
    roas: float | None  # 消耗加权 D1_Roas
    cpi: float | None
    ipm: float | None
    row_count: int
    days_idle: int | None  # 最近数据行距全库最大日期的天数；无数据为 None
    recent_spend: float  # 近 7 天窗口消耗
    recent_cpp: float | None
    variant_count: int
    # 总曝光（数据充分性闸门用；行级 impressions 列求和）
    impressions: int = 0
    observation_partners: list[str] = field(default_factory=list)
    # 主市场（消耗最高变体的市场标签；无投放数据退回文件名前缀，再无则 ""）
    main_market: str = ""
    # 演化维度（derivations）：裂变次数 / 已判定数 / 有效数
    derivation_count: int = 0
    judged_count: int = 0
    positive_count: int = 0
    # 维度级耗尽：(factor, 该维度已判定数)；某维度 judged≥2 且 positive==0 即耗尽。
    # factor="unknown" 只进 judged/positive 总数，不算维度（build_report 分组统计）
    exhausted_factors: tuple[tuple[str, int], ...] = ()
    # 可选判定指标（消耗加权；judge_metrics 开启后参与判定）
    d3_roas: float | None = None
    d1_retention: float | None = None


def collect_creative_performance(
    db: Session,
    creative: Creative,
    *,
    all_performances: Sequence[Performance] | None = None,
    performance_index: PerformanceIndex | None = None,
    variants: Sequence[CreativeVariant] | None = None,
    assets_by_id: dict[str, CreativeAsset] | None = None,
) -> list[Performance]:
    """All delivery rows matched to a creative's assets (deduplicated).

    Shared by the /creatives/{id}/performance route and this engine so the
    matching semantics (services/matching, MIN_PREFIX) stay in one place.
    Callers that aggregate many creatives in one request should preload once
    and pass ``performance_index`` (distinct-name inverted index) plus the
    ``variants`` / ``assets_by_id`` batches — otherwise each creative costs
    per-row matching and ORM lookups (N+1).
    """
    from pathlib import PurePosixPath

    if variants is None:
        variants = VariantRepository(db).list_by_creative(creative.id)

    if performance_index is not None:

        def matched(stem: str) -> list[Performance]:
            return match_rows(performance_index, stem)

    elif all_performances is None:
        performance_repo = PerformanceRepository(db)

        def matched(stem: str) -> list[Performance]:
            return performance_repo.list_for_creative_name(stem)

    else:
        rows_all = all_performances

        def matched(stem: str) -> list[Performance]:
            normalized = normalize(stem)
            return [
                row
                for row in rows_all
                if row.creative_name and matches(normalized, row.creative_name)
            ]

    asset_repo = None if assets_by_id is not None else AssetRepository(db)
    seen: set[str] = set()
    rows: list[Performance] = []
    for variant in variants:
        asset = (
            assets_by_id.get(variant.asset_id)
            if assets_by_id is not None
            else asset_repo.get(variant.asset_id)  # type: ignore[union-attr]
        )
        if asset is None:
            continue
        for row in matched(PurePosixPath(asset.filename).stem):
            if row.id in seen:
                continue
            seen.add(row.id)
            rows.append(row)
    rows.sort(key=lambda item: (item.date is None, item.date, item.creative_name))
    return rows


def _weighted_metric(rows: Sequence[Performance], key: str) -> float | None:
    """Spend-weighted average of a raw metric (d1_roas / cpi / ipm)."""
    total_spend = 0.0
    weighted = 0.0
    for row in rows:
        value = metrics_from_raw(row.raw or {}).get(key)
        if value is None:
            continue
        total_spend += row.spend
        weighted += row.spend * float(value)
    return weighted / total_spend if total_spend else None


def aggregate(
    creative: Creative,
    dna_code: str | None,
    dna_name: str | None,
    rows: Sequence[Performance],
    *,
    max_date: date | None,
    variant_count: int,
    observation_partners: Sequence[str] = (),
    derivation_count: int = 0,
    judged_count: int = 0,
    positive_count: int = 0,
    exhausted_factors: tuple[tuple[str, int], ...] = (),
    main_market: str = "",
) -> CreativeMetrics:
    spend = sum(row.spend for row in rows)
    installs = sum(row.installs for row in rows)
    impressions = sum(row.impressions for row in rows)
    payers = 0
    for row in rows:
        value = metrics_from_raw(row.raw or {})["payers"]
        payers += int(value or 0)
    cpp = spend / payers if payers else None

    last_date = max((row.date for row in rows if row.date is not None), default=None)
    days_idle = (max_date - last_date).days if (max_date and last_date) else None

    recent_spend = 0.0
    recent_payers = 0
    if max_date is not None:
        window_start = max_date - timedelta(days=RECENT_WINDOW_DAYS)
        for row in rows:
            if row.date is not None and row.date > window_start:
                recent_spend += row.spend
                value = metrics_from_raw(row.raw or {})["payers"]
                recent_payers += int(value or 0)
    recent_cpp = recent_spend / recent_payers if recent_payers else None

    return CreativeMetrics(
        creative_id=creative.id,
        creative_name=creative.name,
        dna_code=dna_code,
        dna_name=dna_name,
        spend=spend,
        payers=payers,
        installs=installs,
        impressions=impressions,
        cpp=cpp,
        roas=_weighted_metric(rows, "d1_roas"),
        cpi=spend / installs if installs else None,
        ipm=_weighted_metric(rows, "ipm"),
        d3_roas=_weighted_metric(rows, "d3_roas"),
        d1_retention=_weighted_metric(rows, "d1_retention"),
        row_count=len(rows),
        days_idle=days_idle,
        recent_spend=recent_spend,
        recent_cpp=recent_cpp,
        variant_count=variant_count,
        observation_partners=list(observation_partners),
        derivation_count=derivation_count,
        judged_count=judged_count,
        positive_count=positive_count,
        exhausted_factors=exhausted_factors,
        main_market=main_market,
    )


def recommend(
    metrics: CreativeMetrics,
    config: MetricConfig | None = None,
) -> Verdict:
    """Classify a creative; the first matching rule in the cascade wins.

    Cascade order: delivery check → R0 data-sufficiency gate → idle /
    factor-exhaustion strong signals → cost (CPP) rules → ROAS / retention
    rules → KEEP fallback. CPP rules additionally require ≥ PAYERS_MIN_JUDGE
    payers and ROAS / retention rules require ≥ SPEND_MIN_JUDGE spend — thin
    samples are not judgable and degrade to ``insufficient_payers`` / fall
    through instead.

    Thresholds and the participating metrics come from ``config`` (Settings
    页配置）；未传时用默认值——与未配置的旧行为完全一致。``judge_metrics``
    门控：不在列表里的指标跳过对应判定分支。

    Returns a structured ``Verdict``; ``verdict.reasons`` keeps the exact
    legacy Chinese sentences (byte-identical), while ``reason_code`` +
    ``params`` give the frontend an i18n-ready form.
    """
    cfg = config or _DEFAULT_METRIC_CONFIG
    t = cfg.thresholds
    judge = set(cfg.judge_metrics)
    use_cpp = "cpp" in judge
    use_d1 = "d1_roas" in judge
    m = metrics
    spend_s = f"${m.spend:,.0f}"
    cpp_s = f"${m.cpp:,.2f}" if m.cpp is not None else "-"
    red = t["cpp_red_line"]

    def v(
        action: RecommendationAction,
        code: str,
        params: dict[str, float | int | str | None],
        reason: str,
    ) -> Verdict:
        return Verdict(action=action, reason_code=code, params=params, reasons=[reason])

    if m.spend == 0:
        return v("ITERATE", "no_delivery", {}, "尚未投放或未匹配到投放数据，建议投放验证")
    # R0 数据充分性闸门：消耗与曝光双低 = 小样本，不下方向性结论
    # （任一信号达标即放行，进入正常规则链）
    if (
        m.spend < t["spend_min_signal"]
        and m.impressions < t["impressions_min_signal"]
    ):
        return v(
            "ITERATE",
            "insufficient_data",
            {
                "spend": m.spend,
                "spend_min": t["spend_min_signal"],
                "impressions": m.impressions,
                "impressions_min": t["impressions_min_signal"],
            },
            f"观察期：数据不足（消耗 {spend_s} 未达 ${t['spend_min_signal']:,.0f} "
            f"且曝光 {m.impressions:,} 未达 {int(t['impressions_min_signal']):,}），"
            "继续投放积累数据后再判定",
        )
    if (
        m.days_idle is not None
        and m.days_idle > IDLE_DAYS_ARCHIVE
        and (
            m.payers == 0
            or (use_cpp and m.cpp is not None and m.cpp >= red)
        )
    ):
        return v(
            "ARCHIVE",
            "idle_underperform",
            {"days_idle": m.days_idle, "cpp": m.cpp, "red": red, "payers": m.payers},
            f"已 {m.days_idle} 天无消耗，且历史表现不达标"
            + (f"（成本 {cpp_s} 超 ${red:.0f} 红线）" if m.payers else "（0 付费）"),
        )
    # R2.5 演化强信号：维度级耗尽（factor="unknown" 只进总数、不算维度）。
    # ≥2 个维度耗尽 = 方向耗尽（ARCHIVE）；恰好 1 个 = 换维度迭代（ITERATE）
    if len(m.exhausted_factors) >= 2:
        factors = [factor for factor, _judged in m.exhausted_factors]
        return v(
            "ARCHIVE",
            "derivations_exhausted",
            {"judged_count": m.judged_count, "factors": ",".join(factors)},
            f"裂变 {m.judged_count} 次全部无效，方向已耗尽",
        )
    if m.exhausted_factors:
        factor, factor_judged = m.exhausted_factors[0]
        return v(
            "ITERATE",
            "factor_exhausted",
            {"factor": factor, "judged_count": factor_judged},
            f"「{factor}」方向裂变 {factor_judged} 次全部无效，建议换维度迭代",
        )
    if m.payers == 0 and m.spend >= SPEND_MIN_JUDGE:
        return v(
            "PAUSE", "zero_payers", {"spend": m.spend},
            f"消耗 {spend_s} 仍 0 付费，建议暂停",
        )
    # 薄付费样本：消耗够判定线但付费 <PAYERS_MIN_JUDGE 时 CPP 波动是倍数级，
    # 不做方向性判定（防薄样本漏到 keep_healthy 显示"成本健康"）
    if m.spend >= SPEND_MIN_JUDGE and 0 < m.payers < PAYERS_MIN_JUDGE:
        return v(
            "ITERATE",
            "insufficient_payers",
            {"spend": m.spend, "payers": m.payers},
            f"消耗 {spend_s} 但仅 {m.payers} 个付费，样本太薄"
            f"（<{PAYERS_MIN_JUDGE}），继续投放积累数据后再判定",
        )
    if (
        use_cpp
        and m.payers >= PAYERS_MIN_JUDGE
        and m.cpp is not None
        and m.cpp >= t["cpp_pause_line"]
    ):
        return v(
            "PAUSE", "cpp_over_pause_line", {"cpp": m.cpp, "red": red},
            f"付费成本 {cpp_s} 远超 ${red:.0f} 红线",
        )
    if (
        use_cpp
        and use_d1
        and m.payers >= PAYERS_MIN_JUDGE
        and m.cpp is not None
        and m.cpp >= red
        and (m.roas is not None and m.roas < t["roas_weak_line"])
        and m.spend >= SPEND_SIGNIFICANT
    ):
        return v(
            "PAUSE",
            "cpp_over_red_weak_roas",
            {
                "cpp": m.cpp, "red": red, "roas": m.roas,
                "roas_weak": t["roas_weak_line"], "spend": m.spend,
            },
            f"成本 {cpp_s} 超红线且 D1 Roas {m.roas * 100:.2f}% "
            f"低于 {t['roas_weak_line'] * 100:.0f}%，消耗已 {spend_s}",
        )
    if m.days_idle is not None and m.days_idle > IDLE_DAYS_ARCHIVE:
        return v(
            "ITERATE",
            "idle_was_healthy",
            {"days_idle": m.days_idle, "cpp": m.cpp},
            f"已 {m.days_idle} 天无消耗，历史表现达标（成本 {cpp_s}），建议复盘后重启或迭代",
        )
    if (
        use_cpp
        and m.payers >= PAYERS_MIN_JUDGE
        and m.cpp is not None
        and m.cpp < t["cpp_efficient"]
        and m.spend < SPEND_SIGNIFICANT
    ):
        return v(
            "ITERATE",
            "efficient_not_scaled",
            {"cpp": m.cpp, "spend": m.spend},
            f"效率领先（成本 {cpp_s}）但消耗仅 {spend_s} 未起量，建议加注裂变",
        )
    if (
        use_cpp
        and m.payers >= PAYERS_MIN_JUDGE
        and m.cpp is not None
        and m.cpp >= red
    ):
        return v(
            "ITERATE", "cpp_over_red", {"cpp": m.cpp, "red": red},
            f"付费成本 {cpp_s} 超 ${red:.0f} 红线，建议优化变体降本",
        )
    if (
        use_d1
        and m.spend >= SPEND_MIN_JUDGE
        and m.roas is not None
        and m.roas < t["roas_green_line"]
    ):
        return v(
            "ITERATE",
            "d1_roas_below_green",
            {"cpp": m.cpp, "roas": m.roas, "roas_green": t["roas_green_line"]},
            f"成本 {cpp_s} 健康但 D1 Roas {m.roas * 100:.2f}% "
            f"低于 {t['roas_green_line'] * 100:.0f}% 绿线，建议迭代提升回报",
        )
    if (
        "d3_roas" in judge
        and m.spend >= SPEND_MIN_JUDGE
        and m.d3_roas is not None
        and m.d3_roas < t["d3_roas_weak_line"]
    ):
        return v(
            "ITERATE",
            "d3_roas_weak",
            {"d3_roas": m.d3_roas, "d3_roas_weak": t["d3_roas_weak_line"]},
            f"D3 Roas {m.d3_roas * 100:.2f}% 低于 "
            f"{t['d3_roas_weak_line'] * 100:.0f}% 弱线，后劲不足，建议迭代留存钩子",
        )
    if (
        "d1_retention" in judge
        and m.spend >= SPEND_MIN_JUDGE
        and m.d1_retention is not None
        and m.d1_retention < t["d1_retention_weak_line"]
    ):
        return v(
            "ITERATE",
            "d1_retention_weak",
            {"d1_retention": m.d1_retention, "d1_retention_weak": t["d1_retention_weak_line"]},
            f"次留 {m.d1_retention * 100:.1f}% 低于 "
            f"{t['d1_retention_weak_line'] * 100:.0f}% 弱线，建议迭代前期节奏",
        )
    return v(
        "KEEP", "keep_healthy", {"cpp": m.cpp},
        f"成本 {cpp_s} 健康、Roas 达标、仍在投放，保持当前节奏",
    )


def supplementary_reasons(metrics: CreativeMetrics) -> list[ReasonBit]:
    """Context-only reasons that never change the classification.

    Returns i18n-ready ReasonBits; ``render_reason_zh`` renders each into the
    legacy Chinese sentence for old consumers (node panel tooltips, telemetry).
    """
    bits: list[ReasonBit] = []
    m = metrics
    if (
        m.cpp is not None
        and m.recent_cpp is not None
        and m.cpp > 0
        and abs(m.recent_cpp - m.cpp) / m.cpp >= TREND_THRESHOLD
    ):
        bits.append(
            ReasonBit(
                code="trend_cpp_up" if m.recent_cpp > m.cpp else "trend_cpp_down",
                params={"recent_cpp": m.recent_cpp, "cpp": m.cpp},
            )
        )
    if m.variant_count >= 2:
        bits.append(
            ReasonBit(code="variants_compare", params={"variant_count": m.variant_count})
        )
    if m.judged_count >= 1 and m.positive_count > 0:
        bits.append(
            ReasonBit(
                code="derivation_healthy",
                params={
                    "judged_count": m.judged_count,
                    "positive_count": m.positive_count,
                },
            )
        )
    pending = m.derivation_count - m.judged_count
    if pending > 0:
        bits.append(ReasonBit(code="derivation_pending", params={"pending": pending}))
    for partner in m.observation_partners:
        bits.append(ReasonBit(code="observation_partner", params={"partner": partner}))
    return bits


def render_reason_zh(bit: ReasonBit) -> str:
    """Legacy Chinese rendering of a ReasonBit (kept for old consumers)."""
    p = bit.params
    if bit.code in ("trend_cpp_up", "trend_cpp_down"):
        direction = "上升" if bit.code == "trend_cpp_up" else "下降"
        return (
            f"近 {RECENT_WINDOW_DAYS} 天付费成本{direction}"
            f"（${float(p['recent_cpp']):,.2f} vs 整体 ${float(p['cpp']):,.2f}）"
        )
    if bit.code == "variants_compare":
        return f"{p['variant_count']} 个 Variant 数据可横向对比"
    if bit.code == "derivation_healthy":
        return f"裂变 {p['judged_count']} 次、{p['positive_count']} 次有效，演化历史健康"
    if bit.code == "derivation_pending":
        return f"{p['pending']} 次裂变待判定"
    if bit.code == "observation_partner":
        return f"与 {p['partner']} 互为观察对，建议对比数据后结案"
    return bit.code


def build_report(db: Session, creatives: Sequence[Creative]) -> "ReportData":
    """Aggregate metrics and classify every creative."""
    from app.services import market_stats
    from app.services.markets import resolve_market_prefixes
    from app.services.settings import resolve_thresholds

    dnas = {d.id: d for d in db.scalars(select(CreativeDNA)).all()}
    max_date = db.scalar(
        select(Performance.date).order_by(Performance.date.desc()).limit(1)
    )
    partners = _observation_partners(db)
    metric_config = resolve_metric_config(db)
    prefixes = resolve_market_prefixes(db)

    # 批量预载（替代逐 creative 的 variants/derivations/assets N+1 查询）
    creative_ids = [creative.id for creative in creatives]
    variants_by_creative: dict[str, list[CreativeVariant]] = {}
    if creative_ids:
        stmt = select(CreativeVariant).where(
            CreativeVariant.creative_id.in_(creative_ids)
        )
        for variant in db.scalars(stmt).all():
            variants_by_creative.setdefault(variant.creative_id, []).append(variant)
    asset_ids = {v.asset_id for vs in variants_by_creative.values() for v in vs}
    assets_by_id: dict[str, CreativeAsset] = {}
    if asset_ids:
        assets_by_id = {
            asset.id: asset
            for asset in db.scalars(
                select(CreativeAsset).where(CreativeAsset.id.in_(asset_ids))
            ).all()
        }
    derivations_by_creative: dict[str, list[VariantDerivation]] = {}
    if creative_ids:
        stmt = (
            select(VariantDerivation, CreativeVariant.creative_id)
            .join(
                CreativeVariant,
                CreativeVariant.id == VariantDerivation.source_variant_id,
            )
            .where(CreativeVariant.creative_id.in_(creative_ids))
        )
        for derivation, creative_id in db.execute(stmt).all():
            derivations_by_creative.setdefault(creative_id, []).append(derivation)

    # distinct-name 倒排索引：2k 个名字只 normalize 一次，
    # 替代"每个 variant × 全部行"的逐行匹配（profile 热点 72%）
    all_performances = list(db.scalars(select(Performance)).all())
    performance_index = index_performances(all_performances)
    threshold_cache: dict[str, dict[str, float]] = {}

    def _thresholds(market: str) -> dict[str, float]:
        if market not in threshold_cache:
            threshold_cache[market] = resolve_thresholds(db, market or None)
        return threshold_cache[market]

    # 第一遍：聚合 metrics + 规则判定（保留每条用的市场红线，priority 要用同口径）
    staged: list[tuple[CreativeMetrics, Verdict, float]] = []
    for creative in creatives:
        variants = variants_by_creative.get(creative.id, [])
        rows = collect_creative_performance(
            db,
            creative,
            performance_index=performance_index,
            variants=variants,
            assets_by_id=assets_by_id,
        )
        dna = dnas.get(creative.dna_id) if creative.dna_id else None
        derivations = derivations_by_creative.get(creative.id, [])
        judged = [d for d in derivations if d.verdict != "pending"]
        # 维度级耗尽统计：factor="unknown" 只进 judged/positive 总数，不算维度
        factor_stats: dict[str, list[int]] = {}  # factor → [judged, positive]
        for derivation in judged:
            if derivation.factor == "unknown":
                continue
            stats = factor_stats.setdefault(derivation.factor, [0, 0])
            stats[0] += 1
            if derivation.verdict == "positive":
                stats[1] += 1
        exhausted_factors = tuple(
            sorted(
                (factor, stats[0])
                for factor, stats in factor_stats.items()
                if stats[0] >= 2 and stats[1] == 0
            )
        )
        # 主市场 = 消耗最高变体的市场；无投放数据退回变体文件名前缀
        main_market = market_stats.main_market_for_rows(rows, prefixes)
        if not main_market:
            main_market = market_stats.main_market_for_filenames(
                [variant.name for variant in variants], prefixes
            )
        metrics = aggregate(
            creative,
            dna.code if dna else None,
            dna.name if dna else None,
            rows,
            max_date=max_date,
            variant_count=len(variants),
            observation_partners=partners.get(creative.id, ()),
            derivation_count=len(derivations),
            judged_count=len(judged),
            positive_count=sum(1 for d in judged if d.verdict == "positive"),
            exhausted_factors=exhausted_factors,
            main_market=main_market,
        )
        # 分市场阈值：市场覆盖 → 用户全局覆盖/品类档 → 全局默认（按市场 memo）
        red_line = _thresholds(metrics.main_market)["cpp_red_line"]
        market_config = replace(
            metric_config,
            thresholds=_thresholds(metrics.main_market),
        )
        verdict = recommend(metrics, market_config)
        staged.append((metrics, verdict, red_line))

    # 第二遍：货币化 priority。加注空间 proxy = min(起量线, 同家族头部消耗) − 自身消耗
    from app.services import priority as priority_service

    dna_head: dict[str, float] = {}
    for metrics, _verdict, _red in staged:
        if metrics.dna_code:
            dna_head[metrics.dna_code] = max(
                dna_head.get(metrics.dna_code, 0.0), metrics.spend
            )
    items: list[tuple[CreativeMetrics, Verdict]] = []
    for metrics, verdict, red_line in staged:
        headroom: float | None = None
        if metrics.dna_code:
            head = min(SPEND_SIGNIFICANT, dna_head.get(metrics.dna_code, 0.0))
            if head > metrics.spend:
                headroom = head - metrics.spend
        verdict.priority_dollars, verdict.confidence = priority_service.priority_score(
            metrics, verdict.action, red_line, scale_headroom=headroom
        )
        verdict.supplementary = supplementary_reasons(metrics)
        verdict.reasons = verdict.reasons + [
            render_reason_zh(bit) for bit in verdict.supplementary
        ]
        items.append((metrics, verdict))
    return ReportData(
        generated_at=datetime.now(timezone.utc),
        date_min=db.scalar(select(Performance.date).order_by(Performance.date).limit(1)),
        date_max=max_date,
        items=items,
    )


def _observation_partners(db: Session) -> dict[str, list[str]]:
    """Creative id → names of SIMILAR_TO partners (empty when Neo4j is down)."""
    try:
        from app.config import get_settings
        from app.services import graph_sync

        records = graph_sync.read_similar_pairs_cached(get_settings())
        id_to_name = {c.id: c.name for c in db.query(Creative).all()}
        partners: dict[str, list[str]] = {}
        for source_ref, target_ref in records:
            source_name = id_to_name.get(source_ref)
            target_name = id_to_name.get(target_ref)
            if source_name and target_name:
                partners.setdefault(source_ref, []).append(target_name)
                partners.setdefault(target_ref, []).append(source_name)
        return partners
    except Exception:  # noqa: BLE001 — Neo4j 不可用时观察对留空
        return {}


@dataclass
class ReportData:
    generated_at: datetime
    date_min: date | None
    date_max: date | None
    items: list[tuple[CreativeMetrics, Verdict]]
