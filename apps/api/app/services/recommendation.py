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

from app.models import Creative, CreativeDNA, Performance
from app.repositories.assets import AssetRepository
from app.repositories.creatives import VariantRepository
from app.repositories.derivations import DerivationRepository
from app.repositories.performance import PerformanceRepository
from app.services.excel import metrics_from_raw
from app.services.matching import matches, normalize
from app.services.settings import (
    DEFAULT_JUDGE_METRICS,
    DEFAULT_THRESHOLDS,
    MetricConfig,
    resolve_metric_config,
)

RecommendationAction = Literal["KEEP", "ITERATE", "PAUSE", "ARCHIVE"]

# 默认值 = services/settings.DEFAULT_THRESHOLDS；用户在 Settings 页改阈值后，
# 引擎读配置（resolve_metric_config），这些模块常量仅作缺省与测试锚点。
CPP_RED_LINE = DEFAULT_THRESHOLDS["cpp_red_line"]  # 付费成本红线（≥120 红）
CPP_PAUSE_LINE = DEFAULT_THRESHOLDS["cpp_pause_line"]  # 红线 1.5 倍，直接暂停
CPP_EFFICIENT = DEFAULT_THRESHOLDS["cpp_efficient"]  # 效率领先线
ROAS_GREEN_LINE = DEFAULT_THRESHOLDS["roas_green_line"]  # D1 Roas 绿线（>2% 绿）
ROAS_WEAK_LINE = DEFAULT_THRESHOLDS["roas_weak_line"]
SPEND_MIN_JUDGE = 50.0  # 低于此消耗不做暂停判定
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
    observation_partners: list[str] = field(default_factory=list)
    # 主市场（消耗最高变体的市场标签；无投放数据退回文件名前缀，再无则 ""）
    main_market: str = ""
    # 演化维度（derivations）：裂变次数 / 已判定数 / 有效数
    derivation_count: int = 0
    judged_count: int = 0
    positive_count: int = 0
    # 可选判定指标（消耗加权；judge_metrics 开启后参与判定）
    d3_roas: float | None = None
    d1_retention: float | None = None


def collect_creative_performance(
    db: Session,
    creative: Creative,
    *,
    all_performances: Sequence[Performance] | None = None,
) -> list[Performance]:
    """All delivery rows matched to a creative's assets (deduplicated).

    Shared by the /creatives/{id}/performance route and this engine so the
    matching semantics (services/matching, MIN_PREFIX) stay in one place.
    Callers that aggregate many creatives in one request should preload the
    performances table once and pass ``all_performances`` — otherwise each
    creative costs a full table scan (N+1).
    """
    from pathlib import PurePosixPath

    asset_repo = AssetRepository(db)
    variants = VariantRepository(db).list_by_creative(creative.id)

    if all_performances is None:
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

    seen: set[str] = set()
    rows: list[Performance] = []
    for variant in variants:
        asset = asset_repo.get(variant.asset_id)
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
    main_market: str = "",
) -> CreativeMetrics:
    spend = sum(row.spend for row in rows)
    installs = sum(row.installs for row in rows)
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
        main_market=main_market,
    )


def recommend(
    metrics: CreativeMetrics,
    config: MetricConfig | None = None,
) -> tuple[RecommendationAction, list[str]]:
    """Classify a creative; the first matching rule wins (R1→R9).

    Thresholds and the participating metrics come from ``config`` (Settings
    页配置）；未传时用默认值——与未配置的旧行为完全一致。``judge_metrics``
    门控：不在列表里的指标跳过对应判定分支。
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

    if m.spend == 0:
        return "ITERATE", ["尚未投放或未匹配到投放数据，建议投放验证"]
    if (
        m.days_idle is not None
        and m.days_idle > IDLE_DAYS_ARCHIVE
        and (
            m.payers == 0
            or (use_cpp and m.cpp is not None and m.cpp >= red)
        )
    ):
        return "ARCHIVE", [
            f"已 {m.days_idle} 天无消耗，且历史表现不达标"
            + (f"（成本 {cpp_s} 超 ${red:.0f} 红线）" if m.payers else "（0 付费）")
        ]
    # R2.5 演化强信号：多次裂变全部无效 = 方向耗尽（小样本不做加权，只此一条硬规则）
    if m.judged_count >= 2 and m.positive_count == 0:
        return "ARCHIVE", [f"裂变 {m.judged_count} 次全部无效，方向已耗尽"]
    if m.payers == 0 and m.spend >= SPEND_MIN_JUDGE:
        return "PAUSE", [f"消耗 {spend_s} 仍 0 付费，建议暂停"]
    if use_cpp and m.cpp is not None and m.cpp >= t["cpp_pause_line"]:
        return "PAUSE", [f"付费成本 {cpp_s} 远超 ${red:.0f} 红线"]
    if (
        use_cpp
        and use_d1
        and m.cpp is not None
        and m.cpp >= red
        and (m.roas is not None and m.roas < t["roas_weak_line"])
        and m.spend >= SPEND_SIGNIFICANT
    ):
        return "PAUSE", [
            f"成本 {cpp_s} 超红线且 D1 Roas {m.roas * 100:.2f}% "
            f"低于 {t['roas_weak_line'] * 100:.0f}%，消耗已 {spend_s}"
        ]
    if m.days_idle is not None and m.days_idle > IDLE_DAYS_ARCHIVE:
        return "ITERATE", [
            f"已 {m.days_idle} 天无消耗，历史表现达标（成本 {cpp_s}），建议复盘后重启或迭代"
        ]
    if (
        use_cpp
        and m.cpp is not None
        and m.cpp < t["cpp_efficient"]
        and m.spend < SPEND_SIGNIFICANT
    ):
        return "ITERATE", [
            f"效率领先（成本 {cpp_s}）但消耗仅 {spend_s} 未起量，建议加注裂变"
        ]
    if use_cpp and m.cpp is not None and m.cpp >= red:
        return "ITERATE", [f"付费成本 {cpp_s} 超 ${red:.0f} 红线，建议优化变体降本"]
    if use_d1 and m.roas is not None and m.roas < t["roas_green_line"]:
        return "ITERATE", [
            f"成本 {cpp_s} 健康但 D1 Roas {m.roas * 100:.2f}% "
            f"低于 {t['roas_green_line'] * 100:.0f}% 绿线，建议迭代提升回报"
        ]
    if (
        "d3_roas" in judge
        and m.d3_roas is not None
        and m.d3_roas < t["d3_roas_weak_line"]
    ):
        return "ITERATE", [
            f"D3 Roas {m.d3_roas * 100:.2f}% 低于 "
            f"{t['d3_roas_weak_line'] * 100:.0f}% 弱线，后劲不足，建议迭代留存钩子"
        ]
    if (
        "d1_retention" in judge
        and m.d1_retention is not None
        and m.d1_retention < t["d1_retention_weak_line"]
    ):
        return "ITERATE", [
            f"次留 {m.d1_retention * 100:.1f}% 低于 "
            f"{t['d1_retention_weak_line'] * 100:.0f}% 弱线，建议迭代前期节奏"
        ]
    return "KEEP", [f"成本 {cpp_s} 健康、Roas 达标、仍在投放，保持当前节奏"]


def supplementary_reasons(metrics: CreativeMetrics) -> list[str]:
    """Context-only reasons that never change the classification."""
    reasons: list[str] = []
    m = metrics
    if (
        m.cpp is not None
        and m.recent_cpp is not None
        and m.cpp > 0
        and abs(m.recent_cpp - m.cpp) / m.cpp >= TREND_THRESHOLD
    ):
        direction = "上升" if m.recent_cpp > m.cpp else "下降"
        reasons.append(
            f"近 {RECENT_WINDOW_DAYS} 天付费成本{direction}"
            f"（${m.recent_cpp:,.2f} vs 整体 ${m.cpp:,.2f}）"
        )
    if m.variant_count >= 2:
        reasons.append(f"{m.variant_count} 个 Variant 数据可横向对比")
    if m.judged_count >= 1 and m.positive_count > 0:
        reasons.append(
            f"裂变 {m.judged_count} 次、{m.positive_count} 次有效，演化历史健康"
        )
    pending = m.derivation_count - m.judged_count
    if pending > 0:
        reasons.append(f"{pending} 次裂变待判定")
    for partner in m.observation_partners:
        reasons.append(f"与 {partner} 互为观察对，建议对比数据后结案")
    return reasons


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

    items: list[tuple[CreativeMetrics, RecommendationAction, list[str]]] = []
    variant_repo = VariantRepository(db)
    derivation_repo = DerivationRepository(db)
    all_performances = list(db.scalars(select(Performance)).all())
    for creative in creatives:
        rows = collect_creative_performance(
            db, creative, all_performances=all_performances
        )
        dna = dnas.get(creative.dna_id) if creative.dna_id else None
        derivations = derivation_repo.list_for_creative(creative.id)
        judged = [d for d in derivations if d.verdict != "pending"]
        variants = variant_repo.list_by_creative(creative.id)
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
            main_market=main_market,
        )
        # 分市场阈值：市场覆盖 → 用户全局覆盖/品类档 → 全局默认
        market_config = replace(
            metric_config,
            thresholds=resolve_thresholds(db, metrics.main_market or None),
        )
        action, reasons = recommend(metrics, market_config)
        items.append((metrics, action, reasons + supplementary_reasons(metrics)))
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

        repo = graph_sync.get_graph_repository(get_settings())
        records = repo.read_similar_pairs()
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
    items: list[tuple[CreativeMetrics, RecommendationAction, list[str]]]
