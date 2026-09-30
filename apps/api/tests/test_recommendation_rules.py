"""规则注册表（services/recommendation_rules）的结构契约测试。

每条规则的命中行为（action / params / 中文文案逐字节）由
test_recommendation.py 锁定；本文件只锁 registry 自身的结构：code 唯一且
与 ReasonCode 一一对应、级联顺序（first match wins 的优先级语义）、兜底
位置、RULES_VERSION，以及 supplementary ReasonBit code 的枚举覆盖。
"""
from __future__ import annotations

from types import SimpleNamespace

from app.services import recommendation
from app.services.metrics import RECENT_WINDOW_DAYS, aggregate
from app.services.recommendation import supplementary_reasons
from app.services.recommendation_rules import (
    RULES,
    RULES_VERSION,
    ReasonBitCode,
    ReasonCode,
    RuleContext,
)
from app.services.settings import DEFAULT_JUDGE_METRICS, DEFAULT_THRESHOLDS

EXPECTED_ORDER = (
    ReasonCode.NO_DELIVERY,
    ReasonCode.INSUFFICIENT_DATA,
    ReasonCode.IDLE_UNDERPERFORM,
    ReasonCode.DERIVATIONS_EXHAUSTED,
    ReasonCode.FACTOR_EXHAUSTED,
    ReasonCode.ZERO_PAYERS,
    ReasonCode.INSUFFICIENT_PAYERS,
    ReasonCode.CPP_OVER_PAUSE_LINE,
    ReasonCode.CPP_OVER_RED_WEAK_ROAS,
    ReasonCode.IDLE_WAS_HEALTHY,
    ReasonCode.EFFICIENT_NOT_SCALED,
    ReasonCode.CPP_OVER_RED,
    ReasonCode.D1_ROAS_BELOW_GREEN,
    ReasonCode.D3_ROAS_WEAK,
    ReasonCode.D1_RETENTION_WEAK,
    ReasonCode.KEEP_HEALTHY,
)


def _ctx(metrics: object) -> RuleContext:
    """最小 RuleContext（metrics 用 SimpleNamespace 替身，同 test_recommendation）。"""
    return RuleContext(
        metrics=metrics,  # type: ignore[arg-type]
        thresholds=dict(DEFAULT_THRESHOLDS),
        judge_metrics=frozenset(DEFAULT_JUDGE_METRICS),
        use_cpp=True,
        use_d1=True,
        spend_s="$1,000",
        cpp_s="$50.00",
        red=DEFAULT_THRESHOLDS["cpp_red_line"],
    )


class TestRuleRegistry:
    def test_rule_codes_unique(self) -> None:
        codes = [rule.code for rule in RULES]
        assert len(codes) == len(set(codes))

    def test_rule_codes_match_reason_code_enum(self) -> None:
        # 双向覆盖：16 条规则与 ReasonCode 成员一一对应，无遗漏无编外
        assert {rule.code for rule in RULES} == set(ReasonCode)

    def test_rules_order_is_the_priority_contract(self) -> None:
        # first match wins：列表位置 = 优先级，顺序即对外语义
        assert tuple(rule.code for rule in RULES) == EXPECTED_ORDER

    def test_keep_healthy_is_terminal_fallback(self) -> None:
        assert RULES[-1].code == ReasonCode.KEEP_HEALTHY
        metrics = SimpleNamespace(cpp=50.0)
        verdict = RULES[-1].evaluate(_ctx(metrics))
        assert verdict is not None
        assert verdict.action == "KEEP"

    def test_rules_version_pinned(self) -> None:
        # 规则变更必须人工 bump（快照落库回溯用）；改动这里是刻意动作
        assert RULES_VERSION == "rules-v1"


class TestReasonBitCodeCoverage:
    """supplementary_reasons() 产出的 code 全集必须被 ReasonBitCode 收编。"""

    @staticmethod
    def _metrics(**overrides: object) -> SimpleNamespace:
        base: dict[str, object] = dict(
            cpp=None,
            recent_cpp=None,
            variant_count=1,
            derivation_count=0,
            judged_count=0,
            positive_count=0,
            observation_partners=[],
        )
        base.update(overrides)
        return SimpleNamespace(**base)

    def test_emitted_codes_are_enum_members(self) -> None:
        bits = supplementary_reasons(
            self._metrics(
                cpp=100.0,
                recent_cpp=140.0,
                variant_count=2,
                derivation_count=5,
                judged_count=3,
                positive_count=2,
                observation_partners=["partner-a"],
            )
        )
        assert bits
        for bit in bits:  # 任何产出都必须能收编进枚举（ValueError 即漏登记）
            assert ReasonBitCode(bit.code) in set(ReasonBitCode)

    def test_every_bit_code_is_emittable(self) -> None:
        rich = supplementary_reasons(
            self._metrics(
                cpp=100.0,
                recent_cpp=140.0,
                variant_count=2,
                derivation_count=5,
                judged_count=3,
                positive_count=2,
                observation_partners=["partner-a"],
            )
        )
        trend_down = supplementary_reasons(self._metrics(cpp=100.0, recent_cpp=60.0))
        emitted = {ReasonBitCode(bit.code) for bit in rich + trend_down}
        assert emitted == set(ReasonBitCode)


class TestPublicApiCompat:
    """recommendation.py 的公共 API（含再导出）保持可 import。"""

    def test_recommendation_module_attributes(self) -> None:
        for name in (
            "recommend",
            "Verdict",
            "ReasonBit",
            "CreativeMetrics",
            "aggregate",
            "build_report",
            "collect_creative_performance",
            "supplementary_reasons",
            "render_reason_zh",
            "RecommendationAction",
            "ReportData",
            "CPP_RED_LINE",
            "CPP_PAUSE_LINE",
            "CPP_EFFICIENT",
            "ROAS_GREEN_LINE",
            "ROAS_WEAK_LINE",
            "SPEND_MIN_JUDGE",
            "PAYERS_MIN_JUDGE",
            "SPEND_SIGNIFICANT",
            "IDLE_DAYS_ARCHIVE",
            "RECENT_WINDOW_DAYS",
            "TREND_THRESHOLD",
        ):
            assert hasattr(recommendation, name), name

    def test_aggregate_lives_in_metrics_module(self) -> None:
        # recommendation.aggregate 是 services.metrics.aggregate 的再导出（同一对象）
        assert recommendation.aggregate is aggregate
        assert recommendation.RECENT_WINDOW_DAYS == RECENT_WINDOW_DAYS == 7
