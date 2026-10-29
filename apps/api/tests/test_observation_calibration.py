from __future__ import annotations

import math

import pytest

from app.services.observation_calibration import (
    binomial_log_cdf,
    certify_policy,
    independent_precision_lower_bound,
)


def test_binomial_cdf_known_values() -> None:
    assert math.exp(binomial_log_cdf(0, 99, 0.03)) == pytest.approx(0.97**99)
    assert binomial_log_cdf(2, 2, 0.03) == 0.0


def test_zero_errors_needs_enough_automated_decisions() -> None:
    insufficient = certify_policy([("small", [False] * 30, 100)])
    certified = certify_policy([("enough", [False] * 99, 100)])

    assert insufficient.certified_policy_id is None
    assert insufficient.reason == "risk_not_controlled"
    assert certified.certified_policy_id == "enough"
    assert independent_precision_lower_bound([False] * 99) >= 0.97
    assert independent_precision_lower_bound([False] * 30) < 0.97


def test_bonferroni_applies_to_all_candidates() -> None:
    result = certify_policy(
        [("small", [False] * 30, 100), ("also-small", [False] * 30, 100)]
    )

    assert result.certified_policy_id is None
    assert all(candidate.p_value > 0.025 for candidate in result.candidates)


def test_maximizes_coverage_only_among_certified_policies() -> None:
    result = certify_policy(
        [
            ("narrow", [False] * 100, 100),
            ("wide", [False] * 150, 200),
            ("unsafe", [True] * 10 + [False] * 90, 1000),
        ]
    )

    assert result.certified_policy_id == "wide"


def test_errors_reduce_final_precision_bound() -> None:
    clean = independent_precision_lower_bound([False] * 100)
    with_errors = independent_precision_lower_bound([True] * 3 + [False] * 97)

    assert with_errors < clean
    assert independent_precision_lower_bound([]) is None


def test_invalid_labels_are_rejected() -> None:
    with pytest.raises(ValueError):
        certify_policy([("bad", [False], 0)])
    with pytest.raises(ValueError):
        certify_policy([("bad", [0], 1)])  # type: ignore[list-item]
