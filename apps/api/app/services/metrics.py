"""KPI 聚合口径注册表：Performance 行 → CreativeMetrics 的每项指标在此定义。

新增 KPI 必须先在下方注册口径（聚合语义），再实现——口径只许有一份定义，
recommendation / priority / review / creative_score 等消费方共用本模块的
``aggregate``。人读版镜像见 docs/metrics-semantics.md（改口径时两处同步）。

| 指标 | 口径 |
| --- | --- |
| spend / installs / impressions / payers | 行级求和；payers 从 raw JSONB 经 metrics_from_raw 解析 |
| cpp | spend / payers（ratio of sums；无付费为 None） |
| cpi | spend / installs（ratio of sums；无安装为 None） |
| roas（d1_roas）/ d3_roas / d1_retention / ipm | 行级比值的消耗加权平均（见下注） |
| days_idle | 最近数据行距全库最大日期的天数；无数据为 None |
| recent_spend / recent_cpp | 近 RECENT_WINDOW_DAYS（7）天窗口，相对全库最大日期 |
| main_market | 派生注入（build_report），非本模块计算 |

注：消耗加权平均 = spend-weighted row mean，非 ratio of sums——有意为之，
抗极端小行；缺该指标的行不进分子分母。main_market 由 build_report 注入：
消耗最高变体的市场，回退文件名前缀。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING, Sequence

from app.models import Creative, Performance
from app.services.excel import metrics_from_raw

if TYPE_CHECKING:
    from app.services.recommendation import CreativeMetrics

RECENT_WINDOW_DAYS = 7  # 近 7 天窗口（相对全库最大日期）


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
    from app.services.recommendation import CreativeMetrics  # 延迟 import：避免循环依赖

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
