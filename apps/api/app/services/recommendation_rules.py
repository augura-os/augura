"""推荐判定规则注册表（rule registry）。

recommend() 的判定规则从顺序 if 级联迁为显式 Rule 列表，语义不变：
**first match wins** —— RULES 按原级联顺序排列，recommend() 逐条 evaluate，
返回第一个非 None 的 Verdict；规则优先级 = 列表位置。keep_healthy 兜底
是最后一条 Rule（永远命中）。

级联顺序：投放检查 → R0 数据充分性闸门 → 闲置 / 维度耗尽强信号 → 成本
（CPP）规则 → ROAS / 留存规则 → KEEP 兜底。CPP 类规则需付费样本
≥ PAYERS_MIN_JUDGE，ROAS / 留存规则需消耗 ≥ SPEND_MIN_JUDGE——薄样本
不做方向性判定，降级为 insufficient_payers 或落空。

RULES_VERSION 供后续 verdict 快照落库时随快照存储，用于回溯"这条判定
出自哪版规则"：任何规则增删、顺序调整、文案 / 参数变更都必须人工 bump
（rules-v1 → rules-v2 …）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.recommendation import (
        CreativeMetrics,
        RecommendationAction,
        Verdict,
    )


class ReasonCode(StrEnum):
    """Verdict.reason_code 全集。

    前端 i18n 键为 ``brief.bit.<code>``（apps/web locales），字符串值是对外
    契约——只允许新增成员，禁止修改已有成员的值。
    """

    NO_DELIVERY = "no_delivery"
    INSUFFICIENT_DATA = "insufficient_data"
    IDLE_UNDERPERFORM = "idle_underperform"
    DERIVATIONS_EXHAUSTED = "derivations_exhausted"
    FACTOR_EXHAUSTED = "factor_exhausted"
    ZERO_PAYERS = "zero_payers"
    INSUFFICIENT_PAYERS = "insufficient_payers"
    CPP_OVER_PAUSE_LINE = "cpp_over_pause_line"
    CPP_OVER_RED_WEAK_ROAS = "cpp_over_red_weak_roas"
    IDLE_WAS_HEALTHY = "idle_was_healthy"
    EFFICIENT_NOT_SCALED = "efficient_not_scaled"
    CPP_OVER_RED = "cpp_over_red"
    D1_ROAS_BELOW_GREEN = "d1_roas_below_green"
    D3_ROAS_WEAK = "d3_roas_weak"
    D1_RETENTION_WEAK = "d1_retention_weak"
    KEEP_HEALTHY = "keep_healthy"


class ReasonBitCode(StrEnum):
    """ReasonBit.code（supplementary 补充理由）全集，字符串契约同 ReasonCode。"""

    TREND_CPP_UP = "trend_cpp_up"
    TREND_CPP_DOWN = "trend_cpp_down"
    VARIANTS_COMPARE = "variants_compare"
    DERIVATION_HEALTHY = "derivation_healthy"
    DERIVATION_PENDING = "derivation_pending"
    OBSERVATION_PARTNER = "observation_partner"


# 规则集版本：规则变更时人工 bump（见模块 docstring）。
RULES_VERSION = "rules-v1"

# 判定常量（原 recommendation 模块常量，随规则迁入本模块；recommendation
# 再导出以保持既有 import 路径可用，调参仍是一行改动）。
SPEND_MIN_JUDGE = 50.0  # 低于此消耗不做暂停/ROAS/留存判定
PAYERS_MIN_JUDGE = 3  # 付费样本 <3 时 CPP 波动是倍数级，不做 CPP 类方向性判定
SPEND_SIGNIFICANT = 1000.0  # 起量线
IDLE_DAYS_ARCHIVE = 14


@dataclass
class RuleContext:
    """单条规则判定的全部输入：聚合指标 + 阈值配置 + 派生标志 + 预格式化串。

    由 recommend() 构建一次、RULES 共享；预格式化字符串（spend_s / cpp_s /
    red）保证各规则产出的中文文案与原 if 级联逐字节一致。
    """

    metrics: CreativeMetrics
    thresholds: dict[str, float]
    judge_metrics: frozenset[str]
    use_cpp: bool  # "cpp" in judge_metrics
    use_d1: bool  # "d1_roas" in judge_metrics
    spend_s: str  # f"${spend:,.0f}"
    cpp_s: str  # f"${cpp:,.2f}"；无付费为 "-"
    red: float  # thresholds["cpp_red_line"]


@dataclass
class Rule:
    """一条判定规则：``code`` 全表唯一；``evaluate`` 命中返回 Verdict，否则 None。"""

    code: ReasonCode
    evaluate: Callable[[RuleContext], Verdict | None]


def _verdict(
    action: RecommendationAction,
    code: ReasonCode,
    params: dict[str, float | int | str | None],
    reason: str,
) -> Verdict:
    """构造 Verdict（函数内 import：rules → recommendation 单向依赖，避免成环）。"""
    from app.services.recommendation import Verdict

    return Verdict(action=action, reason_code=code, params=params, reasons=[reason])


def _rule_no_delivery(ctx: RuleContext) -> Verdict | None:
    if ctx.metrics.spend == 0:
        return _verdict(
            "ITERATE",
            ReasonCode.NO_DELIVERY,
            {},
            "尚未投放或未匹配到投放数据，建议投放验证",
        )
    return None


def _rule_insufficient_data(ctx: RuleContext) -> Verdict | None:
    # R0 数据充分性闸门：消耗与曝光双低 = 小样本，不下方向性结论
    # （任一信号达标即放行，进入正常规则链）
    m = ctx.metrics
    t = ctx.thresholds
    if (
        m.spend < t["spend_min_signal"]
        and m.impressions < t["impressions_min_signal"]
    ):
        return _verdict(
            "ITERATE",
            ReasonCode.INSUFFICIENT_DATA,
            {
                "spend": m.spend,
                "spend_min": t["spend_min_signal"],
                "impressions": m.impressions,
                "impressions_min": t["impressions_min_signal"],
            },
            f"观察期：数据不足（消耗 {ctx.spend_s} 未达 ${t['spend_min_signal']:,.0f} "
            f"且曝光 {m.impressions:,} 未达 {int(t['impressions_min_signal']):,}），"
            "继续投放积累数据后再判定",
        )
    return None


def _rule_idle_underperform(ctx: RuleContext) -> Verdict | None:
    m = ctx.metrics
    if (
        m.days_idle is not None
        and m.days_idle > IDLE_DAYS_ARCHIVE
        and (
            m.payers == 0
            or (ctx.use_cpp and m.cpp is not None and m.cpp >= ctx.red)
        )
    ):
        return _verdict(
            "ARCHIVE",
            ReasonCode.IDLE_UNDERPERFORM,
            {"days_idle": m.days_idle, "cpp": m.cpp, "red": ctx.red, "payers": m.payers},
            f"已 {m.days_idle} 天无消耗，且历史表现不达标"
            + (f"（成本 {ctx.cpp_s} 超 ${ctx.red:.0f} 红线）" if m.payers else "（0 付费）"),
        )
    return None


def _rule_derivations_exhausted(ctx: RuleContext) -> Verdict | None:
    # R2.5 演化强信号：维度级耗尽（factor="unknown" 只进总数、不算维度）。
    # ≥2 个维度耗尽 = 方向耗尽（ARCHIVE）；恰好 1 个 = 换维度迭代（下一条规则）
    m = ctx.metrics
    if len(m.exhausted_factors) >= 2:
        factors = [factor for factor, _judged in m.exhausted_factors]
        return _verdict(
            "ARCHIVE",
            ReasonCode.DERIVATIONS_EXHAUSTED,
            {"judged_count": m.judged_count, "factors": ",".join(factors)},
            f"裂变 {m.judged_count} 次全部无效，方向已耗尽",
        )
    return None


def _rule_factor_exhausted(ctx: RuleContext) -> Verdict | None:
    m = ctx.metrics
    if m.exhausted_factors:
        factor, factor_judged = m.exhausted_factors[0]
        return _verdict(
            "ITERATE",
            ReasonCode.FACTOR_EXHAUSTED,
            {"factor": factor, "judged_count": factor_judged},
            f"「{factor}」方向裂变 {factor_judged} 次全部无效，建议换维度迭代",
        )
    return None


def _rule_zero_payers(ctx: RuleContext) -> Verdict | None:
    m = ctx.metrics
    if m.payers == 0 and m.spend >= SPEND_MIN_JUDGE:
        return _verdict(
            "PAUSE",
            ReasonCode.ZERO_PAYERS,
            {"spend": m.spend},
            f"消耗 {ctx.spend_s} 仍 0 付费，建议暂停",
        )
    return None


def _rule_insufficient_payers(ctx: RuleContext) -> Verdict | None:
    # 薄付费样本：消耗够判定线但付费 <PAYERS_MIN_JUDGE 时 CPP 波动是倍数级，
    # 不做方向性判定（防薄样本漏到 keep_healthy 显示"成本健康"）
    m = ctx.metrics
    if m.spend >= SPEND_MIN_JUDGE and 0 < m.payers < PAYERS_MIN_JUDGE:
        return _verdict(
            "ITERATE",
            ReasonCode.INSUFFICIENT_PAYERS,
            {"spend": m.spend, "payers": m.payers},
            f"消耗 {ctx.spend_s} 但仅 {m.payers} 个付费，样本太薄"
            f"（<{PAYERS_MIN_JUDGE}），继续投放积累数据后再判定",
        )
    return None


def _rule_cpp_over_pause_line(ctx: RuleContext) -> Verdict | None:
    m = ctx.metrics
    t = ctx.thresholds
    if (
        ctx.use_cpp
        and m.payers >= PAYERS_MIN_JUDGE
        and m.cpp is not None
        and m.cpp >= t["cpp_pause_line"]
    ):
        return _verdict(
            "PAUSE",
            ReasonCode.CPP_OVER_PAUSE_LINE,
            {"cpp": m.cpp, "red": ctx.red},
            f"付费成本 {ctx.cpp_s} 远超 ${ctx.red:.0f} 红线",
        )
    return None


def _rule_cpp_over_red_weak_roas(ctx: RuleContext) -> Verdict | None:
    m = ctx.metrics
    t = ctx.thresholds
    if (
        ctx.use_cpp
        and ctx.use_d1
        and m.payers >= PAYERS_MIN_JUDGE
        and m.cpp is not None
        and m.cpp >= ctx.red
        and (m.roas is not None and m.roas < t["roas_weak_line"])
        and m.spend >= SPEND_SIGNIFICANT
    ):
        return _verdict(
            "PAUSE",
            ReasonCode.CPP_OVER_RED_WEAK_ROAS,
            {
                "cpp": m.cpp, "red": ctx.red, "roas": m.roas,
                "roas_weak": t["roas_weak_line"], "spend": m.spend,
            },
            f"成本 {ctx.cpp_s} 超红线且 D1 Roas {m.roas * 100:.2f}% "
            f"低于 {t['roas_weak_line'] * 100:.0f}%，消耗已 {ctx.spend_s}",
        )
    return None


def _rule_idle_was_healthy(ctx: RuleContext) -> Verdict | None:
    m = ctx.metrics
    if m.days_idle is not None and m.days_idle > IDLE_DAYS_ARCHIVE:
        return _verdict(
            "ITERATE",
            ReasonCode.IDLE_WAS_HEALTHY,
            {"days_idle": m.days_idle, "cpp": m.cpp},
            f"已 {m.days_idle} 天无消耗，历史表现达标（成本 {ctx.cpp_s}），建议复盘后重启或迭代",
        )
    return None


def _rule_efficient_not_scaled(ctx: RuleContext) -> Verdict | None:
    m = ctx.metrics
    t = ctx.thresholds
    if (
        ctx.use_cpp
        and m.payers >= PAYERS_MIN_JUDGE
        and m.cpp is not None
        and m.cpp < t["cpp_efficient"]
        and m.spend < SPEND_SIGNIFICANT
    ):
        return _verdict(
            "ITERATE",
            ReasonCode.EFFICIENT_NOT_SCALED,
            {"cpp": m.cpp, "spend": m.spend},
            f"效率领先（成本 {ctx.cpp_s}）但消耗仅 {ctx.spend_s} 未起量，建议加注裂变",
        )
    return None


def _rule_cpp_over_red(ctx: RuleContext) -> Verdict | None:
    m = ctx.metrics
    if (
        ctx.use_cpp
        and m.payers >= PAYERS_MIN_JUDGE
        and m.cpp is not None
        and m.cpp >= ctx.red
    ):
        return _verdict(
            "ITERATE",
            ReasonCode.CPP_OVER_RED,
            {"cpp": m.cpp, "red": ctx.red},
            f"付费成本 {ctx.cpp_s} 超 ${ctx.red:.0f} 红线，建议优化变体降本",
        )
    return None


def _rule_d1_roas_below_green(ctx: RuleContext) -> Verdict | None:
    m = ctx.metrics
    t = ctx.thresholds
    if (
        ctx.use_d1
        and m.spend >= SPEND_MIN_JUDGE
        and m.roas is not None
        and m.roas < t["roas_green_line"]
    ):
        return _verdict(
            "ITERATE",
            ReasonCode.D1_ROAS_BELOW_GREEN,
            {"cpp": m.cpp, "roas": m.roas, "roas_green": t["roas_green_line"]},
            f"成本 {ctx.cpp_s} 健康但 D1 Roas {m.roas * 100:.2f}% "
            f"低于 {t['roas_green_line'] * 100:.0f}% 绿线，建议迭代提升回报",
        )
    return None


def _rule_d3_roas_weak(ctx: RuleContext) -> Verdict | None:
    m = ctx.metrics
    t = ctx.thresholds
    if (
        "d3_roas" in ctx.judge_metrics
        and m.spend >= SPEND_MIN_JUDGE
        and m.d3_roas is not None
        and m.d3_roas < t["d3_roas_weak_line"]
    ):
        return _verdict(
            "ITERATE",
            ReasonCode.D3_ROAS_WEAK,
            {"d3_roas": m.d3_roas, "d3_roas_weak": t["d3_roas_weak_line"]},
            f"D3 Roas {m.d3_roas * 100:.2f}% 低于 "
            f"{t['d3_roas_weak_line'] * 100:.0f}% 弱线，后劲不足，建议迭代留存钩子",
        )
    return None


def _rule_d1_retention_weak(ctx: RuleContext) -> Verdict | None:
    m = ctx.metrics
    t = ctx.thresholds
    if (
        "d1_retention" in ctx.judge_metrics
        and m.spend >= SPEND_MIN_JUDGE
        and m.d1_retention is not None
        and m.d1_retention < t["d1_retention_weak_line"]
    ):
        return _verdict(
            "ITERATE",
            ReasonCode.D1_RETENTION_WEAK,
            {"d1_retention": m.d1_retention, "d1_retention_weak": t["d1_retention_weak_line"]},
            f"次留 {m.d1_retention * 100:.1f}% 低于 "
            f"{t['d1_retention_weak_line'] * 100:.0f}% 弱线，建议迭代前期节奏",
        )
    return None


def _rule_keep_healthy(ctx: RuleContext) -> Verdict | None:
    # KEEP 兜底：永远命中（first match wins 下必须是 RULES 的最后一条）
    m = ctx.metrics
    return _verdict(
        "KEEP",
        ReasonCode.KEEP_HEALTHY,
        {"cpp": m.cpp},
        f"成本 {ctx.cpp_s} 健康、Roas 达标、仍在投放，保持当前节奏",
    )


# first match wins：recommend() 按此顺序逐条 evaluate，返回第一个非 None 的
# Verdict；规则优先级 = 列表位置（与原 if 级联的代码顺序一一对应）。
RULES: tuple[Rule, ...] = (
    Rule(ReasonCode.NO_DELIVERY, _rule_no_delivery),
    Rule(ReasonCode.INSUFFICIENT_DATA, _rule_insufficient_data),
    Rule(ReasonCode.IDLE_UNDERPERFORM, _rule_idle_underperform),
    Rule(ReasonCode.DERIVATIONS_EXHAUSTED, _rule_derivations_exhausted),
    Rule(ReasonCode.FACTOR_EXHAUSTED, _rule_factor_exhausted),
    Rule(ReasonCode.ZERO_PAYERS, _rule_zero_payers),
    Rule(ReasonCode.INSUFFICIENT_PAYERS, _rule_insufficient_payers),
    Rule(ReasonCode.CPP_OVER_PAUSE_LINE, _rule_cpp_over_pause_line),
    Rule(ReasonCode.CPP_OVER_RED_WEAK_ROAS, _rule_cpp_over_red_weak_roas),
    Rule(ReasonCode.IDLE_WAS_HEALTHY, _rule_idle_was_healthy),
    Rule(ReasonCode.EFFICIENT_NOT_SCALED, _rule_efficient_not_scaled),
    Rule(ReasonCode.CPP_OVER_RED, _rule_cpp_over_red),
    Rule(ReasonCode.D1_ROAS_BELOW_GREEN, _rule_d1_roas_below_green),
    Rule(ReasonCode.D3_ROAS_WEAK, _rule_d3_roas_weak),
    Rule(ReasonCode.D1_RETENTION_WEAK, _rule_d1_retention_weak),
    # 兜底：永远命中，必须保持在最后
    Rule(ReasonCode.KEEP_HEALTHY, _rule_keep_healthy),
)
