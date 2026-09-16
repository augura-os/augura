"""Correction export: turn edit_logs into allowlisted correction events.

The highest-value behavioral data — human corrections of AI judgements —
is already persisted in ``edit_logs`` (Human > AI audit trail). Exporting
corrections for the knowledge pool therefore needs no new instrumentation,
only a read of that table plus timestamp bucketing.

Also aggregated metric/lifecycle buckets (v0.12): per-creative metrics are
reduced to bucket+count per genre — single-creative raw values (spend/ROAS/
CPP exact numbers) never leave the instance (PRIVACY.md §1.4).

维度升级：聚合桶加 market 维度（genre × market × dna × metric × bucket），
市场用 markets.resolve_market 的文件名×分析标签双层判定，存疑
（conflict）的 creative 不进基准桶（宁可缺数据不污染基准）；
count < K_ANONYMITY_MIN 的桶不上传（k-匿名小样本抑制，PRIVACY.md §3.2）。
correction 事件附 genre/dna_code/market 上下文（只加维度不加身份——
家族级误判率分析所需）。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Creative, CreativeDNA, CreativeVariant, EditLog, VariantDerivation
from app.services import recommendation as rec
from app.services.markets import resolve_creative_markets
from telemetry.anonymize import scrub_slugs, timestamp_bucket

_CORRECTION_ACTIONS = ("update", "merge", "split", "rename")


def _correction_contexts(
    db: Session,
) -> dict[str, tuple[str, str]]:
    """creative_id → (dna_code, market)：correction 事件的上下文反查。"""
    dnas = {d.id: d.code for d in db.scalars(select(CreativeDNA)).all()}
    creatives = {c.id: c for c in db.scalars(select(Creative)).all()}
    markets = resolve_creative_markets(db)
    return {
        cid: (
            dnas.get(creative.dna_id, "") if creative.dna_id else "",
            markets.get(cid, ("", ""))[0],
        )
        for cid, creative in creatives.items()
    }


def corrections_from_edit_logs(
    db: Session,
    *,
    since: datetime | None = None,
    salt: str = "",
    genre: str = "",
) -> list[dict[str, object]]:
    """Allowlist-shaped correction events from the edit_logs audit trail.

    ``salt``（实例 ID）非空时，素材 slug 哈希化——判定结构（字段、方向、
    DNA 编码）保留，素材身份不出本机。

    每条修正附 genre/dna_code/market 上下文（从 entity_id 反查 creative
    的家族与市场；derivation 类实体经 source variant 反查；反查不到留
    空字符串）——只加维度不加身份。
    """
    contexts = _correction_contexts(db)
    # derivation 实体 → source variant 所属 creative（反查链）
    deriv_creative = {
        deriv_id: creative_id
        for deriv_id, creative_id in db.execute(
            select(VariantDerivation.id, CreativeVariant.creative_id).join(
                CreativeVariant,
                CreativeVariant.id == VariantDerivation.source_variant_id,
            )
        ).all()
    }
    stmt = select(EditLog).where(EditLog.action.in_(_CORRECTION_ACTIONS))
    if since is not None:
        stmt = stmt.where(EditLog.created_at >= since)
    stmt = stmt.order_by(EditLog.created_at)
    events: list[dict[str, object]] = []
    for log in db.scalars(stmt).all():
        old_value = log.old_value[:256]
        new_value = log.new_value[:256]
        if salt:
            old_value = scrub_slugs(old_value, salt=salt)
            new_value = scrub_slugs(new_value, salt=salt)
        creative_id = log.entity_id
        if log.entity_type == "derivation":
            creative_id = deriv_creative.get(log.entity_id, "")
        dna_code, market = contexts.get(creative_id, ("", ""))
        events.append(
            {
                "entity_type": log.entity_type,
                "field": log.field,
                "old_value": old_value,
                "new_value": new_value,
                "genre": genre,
                "dna_code": dna_code,
                "market": market,
                "timestamp_bucket": timestamp_bucket(log.created_at),
            }
        )
    return events


# ---------------------------------------------------------------------------
# 聚合桶导出（aggregate_metric / creative_lifecycle）
# ---------------------------------------------------------------------------

_CPP_BUCKET_EDGES = (60.0, 120.0, 180.0)
_RATIO_BUCKET_EDGES = (0.01, 0.02, 0.04)
_RETENTION_BUCKET_EDGES = (0.25, 0.35, 0.45)
_DECAY_BUCKET_EDGES = (0.8, 1.2, 1.5)
_LIFETIME_BUCKET_EDGES = (7, 14, 30)

# k-匿名小样本抑制（隐私硬底线，PRIVACY.md §3.2）：计数低于此值的桶
# 不上传——否则 count=1 的 genre×market×dna×bucket 组合可能反推单个创意
K_ANONYMITY_MIN = 3

# 市场判定不出（无前缀无分析标签）的 creative 归入该桶名
MARKET_UNKNOWN = "unknown"


def _bucket(value: float, edges: tuple[float, ...], unit: str = "") -> str:
    for edge in edges:
        if value < edge:
            return f"<{edge:g}{unit}"
    return f">={edges[-1]:g}{unit}"


def _int_bucket(value: int, edges: tuple[int, ...], unit: str = "") -> str:
    for edge in edges:
        if value < edge:
            return f"<{edge}{unit}"
    return f">={edges[-1]}{unit}"


def aggregate_metric_events(
    db: Session, *, genre: str
) -> list[dict[str, object]]:
    """Per-creative metrics reduced to bucket+count events (no raw values).

    Metrics: cpp / d1_roas / d1_retention distributions + cpp_decay_ratio
    （近 7 天成本 / 整体成本——衰退趋势）。按 genre × market × dna_code
    分桶：不同市场是独立经济学（CPM/CPI 可差数倍），不分市场维度的基准
    没有意义。市场用文件名×分析标签双层判定（markets.resolve_market）；
    存疑（conflict）的 creative 不进基准桶——宁可缺数据不污染基准。
    count < K_ANONYMITY_MIN 的桶被抑制（k-匿名，PRIVACY.md §3.2）。
    """
    creatives = list(db.scalars(select(Creative)).all())
    report = rec.build_report(db, creatives)
    markets = resolve_creative_markets(db)
    counts: dict[tuple[str, str, str, str, str], int] = {}
    for metrics, _action, _reasons in report.items:
        market, confidence = markets.get(metrics.creative_id, ("", "none"))
        if confidence == "conflict":
            continue  # 市场存疑：不进基准桶
        market = market or MARKET_UNKNOWN
        dna_code = metrics.dna_code or "none"
        dna_name = metrics.dna_name or "none"
        if metrics.cpp is not None:
            key = (market, dna_code, dna_name, "cpp", _bucket(metrics.cpp, _CPP_BUCKET_EDGES))
            counts[key] = counts.get(key, 0) + 1
        if metrics.roas is not None:
            key = (
                market, dna_code, dna_name, "d1_roas",
                _bucket(metrics.roas, _RATIO_BUCKET_EDGES),
            )
            counts[key] = counts.get(key, 0) + 1
        if metrics.d1_retention is not None:
            key = (
                market,
                dna_code,
                dna_name,
                "d1_retention",
                _bucket(metrics.d1_retention, _RETENTION_BUCKET_EDGES),
            )
            counts[key] = counts.get(key, 0) + 1
        if metrics.cpp and metrics.recent_cpp and metrics.cpp > 0:
            ratio = metrics.recent_cpp / metrics.cpp
            key = (
                market, dna_code, dna_name, "cpp_decay_ratio",
                _bucket(ratio, _DECAY_BUCKET_EDGES),
            )
            counts[key] = counts.get(key, 0) + 1

    bucket_now = timestamp_bucket()
    return [
        {
            "genre": genre,
            "market": market,
            "dna_code": dna_code,
            "dna_name": dna_name,
            "metric": metric,
            "bucket": bucket,
            "count": count,
            "timestamp_bucket": bucket_now,
        }
        for (market, dna_code, dna_name, metric, bucket), count in sorted(counts.items())
        if count >= K_ANONYMITY_MIN
    ]


def creative_lifecycle_events(
    db: Session, *, genre: str
) -> list[dict[str, object]]:
    """素材存活天数（首个投放日 → 最近投放日）的分布桶（genre × market × dna）。

    市场判定与存疑排除同 aggregate_metric_events；count < K_ANONYMITY_MIN
    的桶被抑制（k-匿名，PRIVACY.md §3.2）。
    """
    dnas = {d.id: (d.code, d.name) for d in db.scalars(select(CreativeDNA)).all()}
    creatives = list(db.scalars(select(Creative)).all())
    markets = resolve_creative_markets(db)
    counts: dict[tuple[str, str, str, str], int] = {}
    for creative in creatives:
        market, confidence = markets.get(creative.id, ("", "none"))
        if confidence == "conflict":
            continue  # 市场存疑：不进基准桶
        market = market or MARKET_UNKNOWN
        rows = rec.collect_creative_performance(db, creative)
        dates = [row.date for row in rows if row.date is not None]
        if not dates:
            continue
        lifetime = (max(dates) - min(dates)).days
        dna_code, dna_name = (
            dnas.get(creative.dna_id, (None, None)) if creative.dna_id else (None, None)
        )
        key = (
            market,
            dna_code or "none",
            dna_name or "none",
            _int_bucket(lifetime, _LIFETIME_BUCKET_EDGES, "d"),
        )
        counts[key] = counts.get(key, 0) + 1

    bucket_now = timestamp_bucket()
    return [
        {
            "genre": genre,
            "market": market,
            "dna_code": dna_code,
            "dna_name": dna_name,
            "lifetime_days_bucket": bucket,
            "count": count,
            "timestamp_bucket": bucket_now,
        }
        for (market, dna_code, dna_name, bucket), count in sorted(counts.items())
        if count >= K_ANONYMITY_MIN
    ]
