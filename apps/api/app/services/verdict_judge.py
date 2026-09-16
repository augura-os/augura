"""裂变判定预裁（pending-verdict pre-adjudication）。

每条 DERIVED_FROM 裂变边（source -[factor]-> target）在投放数据回流后
需要人工判定有效/无效（Experiment 层）。LLM 自洽性投票（N=3）先给
建议，人工在收件箱一键确认——与观察对预裁同一模式：

- ``positive``：裂变有效——target 成本相当或更优，方向值得继续
- ``negative``：裂变无效——target 明显更差，别再沿这个因子裂变
- ``insufficient``：数据不足（任一方消耗过低或 0 付费），暂不判

跨市场口径（language-market 因子）：不同市场 CPP 量级天然不同（巴西
$50 是巴西的好，美国 $150 可能是美国的好），source-vs-target 绝对值
对比必然误判——改为**目标市场内相对口径**：target CPP 与市场基准
（services/market_stats 的创意级中位数）比较。基准不可靠（样本不足）
时退回 source-vs-target 旧规则。非跨市场因子不变。

**仅建议级**：不写 verdict、不改状态（Human > AI）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.schemas.derivation import VariantBrief
from app.services.llm_judge import vote_json
from app.services.market_stats import MarketBaseline
from app.services.settings import AIConfig

logger = logging.getLogger(__name__)

VERDICTS = ("positive", "negative", "insufficient")

# 任一方消耗低于此值 → 数据不足以裁定
MIN_SPEND_FOR_VERDICT = 300.0

_SYSTEM = (
    "你是手游广告买量分析师。一条素材裂变出了新版本（source 原版 → "
    "target 变体，变化因子已给出），现在给你双方投放数据，判断这次裂变"
    "是否有效。只输出 JSON："
    "{\"verdict\": \"positive\"|\"negative\"|\"insufficient\", "
    "\"reason\": \"一句话理由\"}。\n"
    "判定规则：\n"
    "- target 付费成本更低或与 source 相当（±10%内）且 ROAS 不衰 → positive\n"
    "- target 付费成本高出 >20% 或 ROAS 明显下滑 → negative\n"
    f"- 任一方消耗 <${MIN_SPEND_FOR_VERDICT:.0f} 或 0 付费 → insufficient\n"
    "- 因子为 language-market 时成本差包含市场差异，着重看 ROAS 与量级是否成立\n"
    "没有把握时输出 insufficient，不要硬判。"
)

# 跨市场（language-market）且有可靠市场基准时，追加市场内相对口径规则
_SYSTEM_CROSS_MARKET = (
    "\n跨市场移植（language-market）补充规则：\n"
    "- 不与 source 比绝对成本——不同市场 CPM/CPI 天然差数倍；\n"
    "  只看 target 在**目标市场内**的相对表现（基准数据见下方输入）：\n"
    "  target 付费成本 ≤ 目标市场 CPP 中位数×1.2 且量级成立 → positive（移植成功）\n"
    "  target 付费成本 > 目标市场 CPP 中位数×1.5 → negative（在该市场没有竞争力）\n"
    "- 介于两线之间时结合 ROAS 与市场内相对水平判断；没把握输出 insufficient"
)


@dataclass
class VerdictJudgement:
    verdict: str  # "positive" | "negative" | "insufficient"
    votes: int  # 自洽性票数（2 或 3；<2 不产出建议）
    reason: str


def _fmt(brief: VariantBrief) -> str:
    cpp = f"${brief.cpp:,.2f}" if brief.cpp is not None else "无付费"
    roas = f"{brief.roas * 100:.2f}%" if brief.roas is not None else "-"
    return f"消耗 ${brief.spend:,.0f}；付费 {brief.payers} 人；付费成本 {cpp}；D1 Roas {roas}"


def judge_verdict(
    config: AIConfig | None,
    *,
    creative_name: str,
    factor: str,
    source: VariantBrief,
    target: VariantBrief,
    market_baseline: MarketBaseline | None = None,
) -> VerdictJudgement | None:
    """LLM 自洽性投票（N=3）；无 API key、票数 <2 或 verdict 非法时返回 None。

    market_baseline 仅在 factor=="language-market" 且基准可靠时生效
    （目标市场的创意级 CPP/ROAS 中位数），其余情况与旧行为一致。
    """
    if config is None or not config.api_key:
        return None
    system = _SYSTEM
    baseline_block = ""
    if (
        factor == "language-market"
        and market_baseline is not None
        and market_baseline.reliable
        and market_baseline.cpp_median is not None
    ):
        system = _SYSTEM + _SYSTEM_CROSS_MARKET
        roas_s = (
            f"{market_baseline.roas_median * 100:.2f}%"
            if market_baseline.roas_median is not None
            else "无数据"
        )
        baseline_block = (
            f"\n目标市场基准（{market_baseline.code}，"
            f"{market_baseline.creative_count} 个创意的中位数）："
            f"CPP 中位数 ${market_baseline.cpp_median:,.2f}；"
            f"D1 ROAS 中位数 {roas_s}"
        )
    user = (
        f"Creative：{creative_name}\n裂变因子：{factor}\n"
        f"source（原版）：{_fmt(source)}\n"
        f"target（变体）：{_fmt(target)}"
        f"{baseline_block}"
    )
    winner, count, reasons = vote_json(
        config, system=system, user=user, key="verdict", n=3
    )
    if winner not in VERDICTS or count < 2:
        return None
    reason = reasons[0] if reasons else "LLM 自洽性投票"
    return VerdictJudgement(verdict=winner, votes=count, reason=reason)
