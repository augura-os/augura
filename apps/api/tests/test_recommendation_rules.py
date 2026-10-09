"""规则注册表（services/recommendation_rules）的结构契约测试。

每条规则的命中行为（action / params / 中文文案逐字节）由
test_recommendation.py 锁定；本文件只锁 registry 自身的结构：code 唯一且
与 ReasonCode 一一对应、级联顺序（first match wins 的优先级语义）、兜底
位置、RULES_VERSION，以及 supplementary ReasonBit code 的枚举覆盖。
"""
from __future__ import annotations

from types import SimpleNamespace

from app.services import recommendation
from app.services.market_stats import MarketBaseline
from app.services.metrics import RECENT_WINDOW_DAYS, aggregate
from app.services.recommendation import supplementary_reasons
from app.services.recommendation_rules import (
    RULES,
    RULES_VERSION,
    LabelCode,
    ReasonBitCode,
    ReasonCode,
    RuleContext,
    classify_labels,
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
        # rules-v2：新增 Verdict 附加标签（LabelCode），主规则语义不变
        assert RULES_VERSION == "rules-v2"


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


class TestClassifyLabels:
    """赢家七分类标签（classify_labels）：每类 ≥1 命中 + ≥1 边界不命中。

    标签是 Verdict 附加信息——判定表按序 first-match，underexplored 短路门
    命中即唯一标签；metrics 用 SimpleNamespace 替身（同 _ctx 模式）。
    """

    @staticmethod
    def _metrics(**overrides: object) -> SimpleNamespace:
        # 默认"大曝光、无亮点"：不过 underexplored 门，也不误中任何标签
        base: dict[str, object] = dict(
            impressions=10000,
            spend=100.0,
            payers=0,
            cpp=None,
            roas=None,
            prev_spend=0.0,
            recent_spend=0.0,
            market_count=0,
            ctr=None,
        )
        base.update(overrides)
        return SimpleNamespace(**base)

    @staticmethod
    def _baseline(**overrides: object) -> MarketBaseline:
        base: dict[str, object] = dict(
            code="US", cpp_median=150.0, roas_median=None,
            creative_count=5, ctr_median=0.02,
        )
        base.update(overrides)
        return MarketBaseline(**base)  # type: ignore[arg-type]

    def _classify(self, metrics: SimpleNamespace, **kwargs: object) -> list[str]:
        return classify_labels(
            str(kwargs.get("action", "ITERATE")),
            str(kwargs.get("reason_code", "keep_healthy")),
            metrics,  # type: ignore[arg-type]
            dict(DEFAULT_THRESHOLDS),
            kwargs.get("baseline"),  # type: ignore[arg-type]
        )

    # -- underexplored（短路门） --

    def test_underexplored_by_reason_code(self) -> None:
        for code in (ReasonCode.NO_DELIVERY, ReasonCode.INSUFFICIENT_DATA):
            assert self._classify(self._metrics(), reason_code=code) == [
                LabelCode.UNDEREXPLORED
            ]

    def test_underexplored_by_low_impressions(self) -> None:
        labels = self._classify(self._metrics(impressions=4999))
        assert labels == [LabelCode.UNDEREXPLORED]

    def test_underexplored_short_circuits_other_labels(self) -> None:
        # 满足 proven_winner 全部条件但曝光不足 → 只出 underexplored
        labels = self._classify(
            self._metrics(impressions=4999, spend=5000.0, payers=10),
            action="KEEP",
        )
        assert labels == [LabelCode.UNDEREXPLORED]

    def test_underexplored_boundary_impressions_at_line(self) -> None:
        # 曝光恰达 5000 不过短路门；其余条件平平 → 无标签
        assert self._classify(self._metrics(impressions=5000)) == []

    # -- proven_winner --

    def test_proven_winner_hit(self) -> None:
        labels = self._classify(
            self._metrics(spend=1000.0, payers=3), action="KEEP"
        )
        assert labels == [LabelCode.PROVEN_WINNER]

    def test_proven_winner_boundary_below_significant(self) -> None:
        labels = self._classify(
            self._metrics(spend=999.0, payers=10), action="KEEP"
        )
        assert LabelCode.PROVEN_WINNER not in labels

    def test_proven_winner_boundary_thin_payers(self) -> None:
        labels = self._classify(
            self._metrics(spend=5000.0, payers=2), action="KEEP"
        )
        assert LabelCode.PROVEN_WINNER not in labels

    def test_proven_winner_requires_keep_action(self) -> None:
        labels = self._classify(
            self._metrics(spend=5000.0, payers=10), action="ITERATE"
        )
        assert LabelCode.PROVEN_WINNER not in labels

    # -- saturated --

    def test_saturated_hit(self) -> None:
        labels = self._classify(
            self._metrics(payers=5, cpp=100.0, prev_spend=200.0, recent_spend=99.0)
        )
        assert labels == [LabelCode.SATURATED]

    def test_saturated_boundary_no_spend_halving(self) -> None:
        # recent == 0.5 × prev 恰好不腰斩 → 不命中
        labels = self._classify(
            self._metrics(payers=5, cpp=100.0, prev_spend=200.0, recent_spend=100.0)
        )
        assert LabelCode.SATURATED not in labels

    def test_saturated_boundary_prev_below_judge_line(self) -> None:
        # prev < SPEND_MIN_JUDGE：历史上就没起量，谈不上衰减
        labels = self._classify(
            self._metrics(payers=5, cpp=100.0, prev_spend=49.0, recent_spend=10.0)
        )
        assert LabelCode.SATURATED not in labels

    def test_saturated_boundary_cpp_over_red(self) -> None:
        labels = self._classify(
            self._metrics(payers=5, cpp=120.0, prev_spend=200.0, recent_spend=50.0)
        )
        assert LabelCode.SATURATED not in labels

    # -- audience_niche_winner --

    def test_audience_niche_winner_hit(self) -> None:
        labels = self._classify(
            self._metrics(market_count=1, payers=5, cpp=100.0, spend=1500.0),
            baseline=self._baseline(),
        )
        assert labels == [LabelCode.AUDIENCE_NICHE_WINNER]

    def test_audience_niche_winner_boundary_multi_market(self) -> None:
        labels = self._classify(
            self._metrics(market_count=2, payers=5, cpp=100.0, spend=1500.0),
            baseline=self._baseline(),
        )
        assert LabelCode.AUDIENCE_NICHE_WINNER not in labels

    def test_audience_niche_winner_boundary_unreliable_baseline(self) -> None:
        labels = self._classify(
            self._metrics(market_count=1, payers=5, cpp=100.0, spend=1500.0),
            baseline=self._baseline(creative_count=2),
        )
        assert LabelCode.AUDIENCE_NICHE_WINNER not in labels

    def test_audience_niche_winner_boundary_cpp_at_median(self) -> None:
        labels = self._classify(
            self._metrics(market_count=1, payers=5, cpp=150.0, spend=1500.0),
            baseline=self._baseline(),
        )
        assert LabelCode.AUDIENCE_NICHE_WINNER not in labels

    # -- potential_winner --

    def test_potential_winner_hit_by_cpp(self) -> None:
        labels = self._classify(self._metrics(spend=500.0, payers=3, cpp=59.0))
        assert labels == [LabelCode.POTENTIAL_WINNER]

    def test_potential_winner_hit_by_roas(self) -> None:
        labels = self._classify(self._metrics(spend=500.0, roas=0.02))
        assert labels == [LabelCode.POTENTIAL_WINNER]

    def test_potential_winner_boundary_at_significant(self) -> None:
        # 已起量（≥1000）不是"潜力"
        labels = self._classify(self._metrics(spend=1000.0, payers=3, cpp=59.0))
        assert LabelCode.POTENTIAL_WINNER not in labels

    def test_potential_winner_boundary_roas_below_green(self) -> None:
        labels = self._classify(self._metrics(spend=500.0, roas=0.019))
        assert LabelCode.POTENTIAL_WINNER not in labels

    # -- low_click_high_value --

    def test_low_click_high_value_hit(self) -> None:
        labels = self._classify(
            self._metrics(spend=1500.0, payers=3, cpp=59.0, ctr=0.01),
            baseline=self._baseline(),
        )
        assert labels == [LabelCode.LOW_CLICK_HIGH_VALUE]

    def test_low_click_high_value_boundary_ctr_at_median(self) -> None:
        labels = self._classify(
            self._metrics(spend=1500.0, payers=3, cpp=59.0, ctr=0.02),
            baseline=self._baseline(),
        )
        assert LabelCode.LOW_CLICK_HIGH_VALUE not in labels

    def test_low_click_high_value_requires_baseline(self) -> None:
        labels = self._classify(self._metrics(spend=1500.0, payers=3, cpp=59.0, ctr=0.01))
        assert LabelCode.LOW_CLICK_HIGH_VALUE not in labels

    # -- high_click_low_value --

    def test_high_click_low_value_hit_by_cpp_red(self) -> None:
        labels = self._classify(
            self._metrics(spend=1500.0, payers=5, cpp=150.0, ctr=0.03),
            baseline=self._baseline(),
        )
        assert labels == [LabelCode.HIGH_CLICK_LOW_VALUE]

    def test_high_click_low_value_hit_by_zero_payers(self) -> None:
        labels = self._classify(
            self._metrics(spend=100.0, payers=0, ctr=0.03),
            baseline=self._baseline(),
        )
        assert labels == [LabelCode.HIGH_CLICK_LOW_VALUE]

    def test_high_click_low_value_boundary_ctr_at_median(self) -> None:
        labels = self._classify(
            self._metrics(spend=1500.0, payers=5, cpp=150.0, ctr=0.02),
            baseline=self._baseline(),
        )
        assert LabelCode.HIGH_CLICK_LOW_VALUE not in labels

    def test_high_click_low_value_requires_baseline(self) -> None:
        labels = self._classify(self._metrics(spend=1500.0, payers=5, cpp=150.0, ctr=0.03))
        assert LabelCode.HIGH_CLICK_LOW_VALUE not in labels

    # -- 结构契约 --

    def test_label_codes_are_strings(self) -> None:
        labels = self._classify(
            self._metrics(spend=1000.0, payers=3), action="KEEP"
        )
        assert all(isinstance(label, str) for label in labels)
        assert all(LabelCode(label) in set(LabelCode) for label in labels)

    def test_no_label_for_plain_metrics(self) -> None:
        assert self._classify(self._metrics()) == []


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
