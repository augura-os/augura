"""Deterministic tests for the pure observation-pair statistical core."""

from __future__ import annotations

import math

import pytest

from app.services.observation_stats import (
    ALTERNATIVE_WEIGHT,
    BETA_SHAPE,
    ObservationArm,
    clopper_pearson_lower_bound,
    cost_ratio,
    msprt_decision,
    msprt_log_likelihood_ratio,
)


def _closed_form_log_ratio(arm_a: ObservationArm, arm_b: ObservationArm) -> float:
    """Independent Beta-binomial anchor for the numerical quadrature."""
    successes = arm_a.results
    total = arm_a.results + arm_b.results
    p0 = arm_a.spend / (arm_a.spend + arm_b.spend)

    def log_beta(a: float, b: float) -> float:
        return math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)

    log_integral = (
        log_beta(successes + BETA_SHAPE, total - successes + BETA_SHAPE)
        - log_beta(BETA_SHAPE, BETA_SHAPE)
        - successes * math.log(p0)
        - (total - successes) * math.log1p(-p0)
    )
    log_null = math.log1p(-ALTERNATIVE_WEIGHT)
    log_alternative = math.log(ALTERNATIVE_WEIGHT) + log_integral
    largest = max(log_null, log_alternative)
    return largest + math.log(math.exp(log_null - largest) + math.exp(log_alternative - largest))


class TestCostRatio:
    def test_practical_separation_is_symmetric(self) -> None:
        efficient = ObservationArm(spend=1000.0, results=90)
        expensive = ObservationArm(spend=1000.0, results=10)
        assert cost_ratio(efficient, expensive) == pytest.approx(9.0)
        assert cost_ratio(expensive, efficient) == pytest.approx(9.0)

    def test_zero_or_missing_cost_returns_none(self) -> None:
        assert cost_ratio(ObservationArm(100.0, 0), ObservationArm(100.0, 0)) is None
        assert cost_ratio(ObservationArm(100.0, 0), ObservationArm(100.0, 10)) is None
        assert cost_ratio(ObservationArm(0.0, 10), ObservationArm(100.0, 10)) is None


class TestMSPRT:
    def test_no_evidence_returns_none(self) -> None:
        empty_a = ObservationArm(100.0, 0)
        empty_b = ObservationArm(100.0, 0)
        assert msprt_log_likelihood_ratio(empty_a, empty_b) is None
        assert msprt_decision(empty_a, empty_b) is None
        assert msprt_log_likelihood_ratio(ObservationArm(0.0, 0), ObservationArm(100.0, 10)) is None

    def test_arm_swap_is_symmetric_with_unequal_exposure(self) -> None:
        arm_a = ObservationArm(100.0, 20)
        arm_b = ObservationArm(300.0, 60)
        assert msprt_log_likelihood_ratio(arm_a, arm_b) == pytest.approx(
            msprt_log_likelihood_ratio(arm_b, arm_a), abs=1e-10
        )

    def test_clear_cost_divergence_crosses_threshold(self) -> None:
        efficient = ObservationArm(1000.0, 90)
        expensive = ObservationArm(1000.0, 10)
        log_ratio = msprt_log_likelihood_ratio(efficient, expensive)
        assert log_ratio is not None
        assert log_ratio > math.log(1.0 / 0.03)
        assert msprt_decision(efficient, expensive) == "threshold_crossed"

    def test_equal_observed_cost_only_continues(self) -> None:
        arm_a = ObservationArm(1000.0, 50)
        arm_b = ObservationArm(1000.0, 50)
        assert msprt_decision(arm_a, arm_b) == "continue"

    def test_repeated_evaluation_is_deterministic_and_cumulative(self) -> None:
        arm_a = ObservationArm(1000.0, 18)
        arm_b = ObservationArm(1000.0, 6)
        first = msprt_log_likelihood_ratio(arm_a, arm_b)
        assert first == msprt_log_likelihood_ratio(arm_a, arm_b)
        assert msprt_decision(arm_a, arm_b) == "continue"

        cumulative_a = ObservationArm(2000.0, 36)
        cumulative_b = ObservationArm(2000.0, 12)
        cumulative = msprt_log_likelihood_ratio(cumulative_a, cumulative_b)
        assert first is not None and cumulative is not None
        assert cumulative > first
        assert msprt_decision(cumulative_a, cumulative_b) == "threshold_crossed"

    def test_large_counts_match_beta_binomial_closed_form(self) -> None:
        arm_a = ObservationArm(100000.0, 120000)
        arm_b = ObservationArm(100000.0, 80000)
        log_ratio = msprt_log_likelihood_ratio(arm_a, arm_b)
        assert log_ratio is not None
        assert math.isfinite(log_ratio)
        assert log_ratio == pytest.approx(_closed_form_log_ratio(arm_a, arm_b), abs=1e-7)

    @pytest.mark.parametrize(
        "invalid",
        [
            ObservationArm(-1.0, 1),
            ObservationArm(1.0, -1),
            ObservationArm(math.nan, 1),
            ObservationArm(math.inf, 1),
            ObservationArm(1.0, 1.5),
            ObservationArm(1.0, True),
        ],
    )
    def test_invalid_evidence_degrades_to_none(self, invalid: ObservationArm) -> None:
        valid = ObservationArm(100.0, 10)
        assert cost_ratio(invalid, valid) is None
        assert msprt_log_likelihood_ratio(invalid, valid) is None
        assert msprt_decision(invalid, valid) is None

    @pytest.mark.parametrize("alpha", [0.0, 1.0, -0.1, math.nan, math.inf, True])
    def test_alpha_validation(self, alpha: float) -> None:
        with pytest.raises(ValueError):
            msprt_decision(ObservationArm(100.0, 10), ObservationArm(100.0, 5), alpha=alpha)


class TestClopperPearsonLowerBound:
    @pytest.mark.parametrize(
        ("successes", "trials", "expected"),
        [
            (0, 20, 0.0),
            (1, 2, 1.0 - math.sqrt(0.95)),
            (2, 2, math.sqrt(0.05)),
            (5, 5, 0.05 ** (1.0 / 5.0)),
            (8, 10, 0.4930986989367978),
        ],
    )
    def test_known_one_sided_bounds(self, successes: int, trials: int, expected: float) -> None:
        assert clopper_pearson_lower_bound(successes, trials) == pytest.approx(expected, abs=1e-12)

    @pytest.mark.parametrize(
        ("successes", "trials"),
        [(0, 0), (-1, 10), (11, 10), (1.5, 10), (1, 10.5), (True, 10)],
    )
    def test_no_trials_or_invalid_counts_return_none(self, successes: int, trials: int) -> None:
        assert clopper_pearson_lower_bound(successes, trials) is None

    @pytest.mark.parametrize("confidence", [0.0, 1.0, math.nan, math.inf, True])
    def test_confidence_validation(self, confidence: float) -> None:
        with pytest.raises(ValueError):
            clopper_pearson_lower_bound(8, 10, confidence=confidence)
