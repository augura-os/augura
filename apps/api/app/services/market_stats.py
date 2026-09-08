"""市场维度 KPI 基准：按文件名市场前缀分组的创意级中位数。

不同市场（KS_EN / KS_KR 等，按项目配置）是独立经济学——CPM/CPI 可差
3-10 倍，全局一刀切的 CPP 红线在低成本市场误杀、在高成本市场放水。
本模块从 performance 行按市场码（markets.market_tag 归一）聚合出每个市场
的创意级基准，供三处使用：

- 分市场阈值（settings.resolve_thresholds 的市场覆盖层）
- 裂变判定（verdict_judge 的 language-market 跨市场相对口径）

统计口径：先在创意（creative_name）内聚合（CPP = 总消耗/总付费，ROAS
按消耗加权），再对市场内各创意取**中位数**（抗极端值，小样本市场的
标准做法）。样本不足 MIN_CREATIVES_FOR_RELIABLE 个创意的市场标记为
不可靠（reliable=False），调用方退回全局阈值/旧口径。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Performance
from app.services.excel import metrics_from_raw
from app.services.markets import (
    market_tag,
    resolve_market_aliases,
    resolve_market_prefixes,
)

# 市场基准可靠的最小创意样本数（<3 退回全局口径——中位数在极小样本下无意义）
MIN_CREATIVES_FOR_RELIABLE = 3


@dataclass(frozen=True)
class MarketBaseline:
    """单市场的创意级基准（中位数口径）。"""

    code: str  # 市场码（market_tag 输出，如 US / PT / KR）
    cpp_median: float | None  # 创意级 CPP 中位数；全市场无付费 → None
    roas_median: float | None  # 创意级 D1 ROAS 中位数；全市场无数据 → None
    creative_count: int  # 该市场有投放行的创意数

    @property
    def reliable(self) -> bool:
        return self.creative_count >= MIN_CREATIVES_FOR_RELIABLE


def compute_baselines(
    rows: Sequence[Performance],
    prefixes: tuple[str, ...],
    aliases: dict[str, str] | None = None,
) -> dict[str, MarketBaseline]:
    """纯计算：performance 行 → 市场码 → MarketBaseline（供测试复用）。"""
    # 市场 → 创意名 → (spend, payers, roas加权分子, roas加权分母)
    grouped: dict[str, dict[str, list[float]]] = {}
    for row in rows:
        if not row.creative_name:
            continue
        tag, _rest = market_tag(row.creative_name, prefixes, aliases)
        if not tag:
            continue  # 无市场前缀的行不参与市场基准
        metrics = metrics_from_raw(row.raw or {})
        bucket = grouped.setdefault(tag, {}).setdefault(
            row.creative_name, [0.0, 0.0, 0.0, 0.0]
        )
        bucket[0] += row.spend
        bucket[1] += float(int(metrics["payers"] or 0))
        roas = metrics["d1_roas"]
        if roas is not None and row.spend > 0:
            bucket[2] += row.spend * float(roas)
            bucket[3] += row.spend

    baselines: dict[str, MarketBaseline] = {}
    for tag, creatives in grouped.items():
        cpps = [
            spend / payers
            for spend, payers, _w, _ws in creatives.values()
            if payers > 0
        ]
        roases = [
            weighted / roas_spend
            for _s, _p, weighted, roas_spend in creatives.values()
            if roas_spend > 0
        ]
        baselines[tag] = MarketBaseline(
            code=tag,
            cpp_median=median(cpps) if cpps else None,
            roas_median=median(roases) if roases else None,
            creative_count=len(creatives),
        )
    return baselines


def market_baselines(
    db: Session, *, all_performances: Sequence[Performance] | None = None
) -> dict[str, MarketBaseline]:
    """全库 performance 行按市场聚合成基准表（市场标签 → MarketBaseline）。"""
    rows = (
        list(all_performances)
        if all_performances is not None
        else list(db.scalars(select(Performance)).all())
    )
    return compute_baselines(
        rows, resolve_market_prefixes(db), resolve_market_aliases(db)
    )


def main_market_for_rows(
    rows: Sequence[Performance],
    prefixes: tuple[str, ...],
    aliases: dict[str, str] | None = None,
) -> str:
    """一组投放行的主市场：消耗最高行的市场码；无投放 → ""。"""
    spend_by_market: dict[str, float] = {}
    for row in rows:
        if not row.creative_name:
            continue
        tag, _rest = market_tag(row.creative_name, prefixes, aliases)
        if tag:
            spend_by_market[tag] = spend_by_market.get(tag, 0.0) + row.spend
    if not spend_by_market:
        return ""
    return max(spend_by_market.items(), key=lambda item: item[1])[0]


def main_market_for_filenames(
    filenames: Sequence[str],
    prefixes: tuple[str, ...],
    aliases: dict[str, str] | None = None,
) -> str:
    """无投放数据时的兜底：取第一个带市场前缀的文件名的市场码。"""
    for filename in filenames:
        tag, _rest = market_tag(filename, prefixes, aliases)
        if tag:
            return tag
    return ""
