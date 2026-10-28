"""Focused tests for deterministic observation-pair policy evidence."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.models import Performance
from app.services.observation_policy import (
    ObservationPolicyConfig,
    evaluate_observation_policy,
    hash_input_evidence,
)

REGISTERED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)
AS_OF = date(2026, 1, 31)
TEST_CERTIFIED_CONFIG = ObservationPolicyConfig(
    rule_version="test-certified-v1",
    min_effect_ratio=2.0,
    certification_reference="test-only-certification",
    certified_precision_lower_bound=0.97,
)


def _row(
    row_id: str,
    day: int,
    *,
    spend: float = 1000.0,
    payers: int,
    installs: int = 0,
    objective: str | None = "aeo",
    creative_name: str = "KS_EN-creative.mp4",
    month: int = 1,
    year: int = 2026,
) -> Performance:
    return Performance(
        id=row_id,
        date=date(year, month, day),
        creative_name=creative_name,
        spend=spend,
        installs=installs,
        raw={"付费人数": payers},
        optimization_type=objective,
    )


def _diverging_rows() -> tuple[list[Performance], list[Performance]]:
    rows_a = [_row("a1", 1, payers=45), _row("a2", 2, payers=45)]
    rows_b = [_row("b1", 1, payers=5), _row("b2", 2, payers=5)]
    return rows_a, rows_b


def _evaluate(
    rows_a: list[Performance],
    rows_b: list[Performance],
    *,
    config: ObservationPolicyConfig = ObservationPolicyConfig(),
    registered_at: datetime | None = REGISTERED_AT,
    evidence_start_at: date | None = date(2026, 1, 1),
    as_of: date = AS_OF,
):
    return evaluate_observation_policy(
        creative_id_a="creative-a",
        creative_id_b="creative-b",
        rows_a=rows_a,
        rows_b=rows_b,
        registered_at=registered_at,
        evidence_start_at=evidence_start_at,
        as_of=as_of,
        config=config,
    )


def test_no_data_stays_in_background_observation() -> None:
    result = _evaluate([], [])

    assert result.output == "observing"
    assert result.waiting_reason == "no_performance_data"
    assert result.evidence["shadow"] is True
    assert len(result.evidence["input_hash"]) == 64


def test_pre_registration_rows_are_not_evidence() -> None:
    rows_a = [_row("a-old", 20, month=12, year=2025, payers=90)]
    rows_b = [_row("b-old", 20, month=12, year=2025, payers=10)]

    result = _evaluate(rows_a, rows_b)

    assert result.output == "observing"
    assert result.waiting_reason == "no_post_registration_data"
    assert result.evidence["eligible_rows_a"] == 0
    assert result.evidence["excluded_a"] == {"pre_registration": 1, "future": 0, "undated": 0}


def test_future_rows_cannot_change_decision_or_input_hash() -> None:
    rows_a, rows_b = _diverging_rows()
    baseline = _evaluate(rows_a, rows_b, config=TEST_CERTIFIED_CONFIG)
    future_a = [_row("a-future", 1, month=2, payers=0)]
    future_b = [_row("b-future", 1, month=2, payers=1000)]

    with_future = _evaluate(
        [*rows_a, *future_a], [*rows_b, *future_b], config=TEST_CERTIFIED_CONFIG
    )

    assert baseline.output == with_future.output == "auto_split"
    assert baseline.evidence["overall"] == with_future.evidence["overall"]
    assert baseline.evidence["input_hash"] == with_future.evidence["input_hash"]
    assert with_future.evidence["excluded_a"]["future"] == 1
    assert with_future.evidence["excluded_b"]["future"] == 1


def test_mixed_objectives_are_incomparable() -> None:
    rows_a, rows_b = _diverging_rows()
    rows_a[1].optimization_type = "install"

    result = _evaluate(rows_a, rows_b, config=TEST_CERTIFIED_CONFIG)

    assert result.output == "observing"
    assert result.waiting_reason == "mixed_objectives"


@pytest.mark.parametrize(
    ("objective", "reason"),
    [(None, "unknown_objective"), ("vo", "unsupported_objective")],
)
def test_unknown_and_vo_objectives_never_auto_split(
    objective: str | None, reason: str
) -> None:
    rows_a, rows_b = _diverging_rows()
    for row in [*rows_a, *rows_b]:
        row.optimization_type = objective

    result = _evaluate(rows_a, rows_b, config=TEST_CERTIFIED_CONFIG)

    assert result.output == "observing"
    assert result.waiting_reason == reason


def test_zero_results_is_not_divergence() -> None:
    rows_a = [_row("a1", 1, payers=0), _row("a2", 2, payers=0)]
    rows_b = [_row("b1", 1, payers=0), _row("b2", 2, payers=0)]

    result = _evaluate(rows_a, rows_b, config=TEST_CERTIFIED_CONFIG)

    assert result.output == "observing"
    assert result.waiting_reason == "zero_results"
    assert result.evidence["overall"]["cost_ratio"] is None


def test_configured_minimum_results_is_enforced() -> None:
    rows_a, rows_b = _diverging_rows()
    config = ObservationPolicyConfig(min_results_per_arm=100)

    result = _evaluate(rows_a, rows_b, config=config)

    assert result.output == "observing"
    assert result.waiting_reason == "insufficient_results"


def test_clear_divergence_remains_shadow_without_certification() -> None:
    rows_a, rows_b = _diverging_rows()

    result = _evaluate(rows_a, rows_b)

    assert result.output == "observing"
    assert result.waiting_reason == "uncertified_policy"
    assert result.evidence["shadow"] is True
    assert result.evidence["performance_rule_passed"] is True
    assert result.evidence["overall"]["msprt_decision"] == "threshold_crossed"


def test_clear_stable_divergence_with_explicit_test_certification() -> None:
    rows_a, rows_b = _diverging_rows()

    result = _evaluate(rows_a, rows_b, config=TEST_CERTIFIED_CONFIG)

    assert result.output == "auto_split"
    assert result.waiting_reason == ""
    assert result.rule_version == "test-certified-v1"
    assert result.evidence["shadow"] is False
    assert result.evidence["resolution_type"] == "performance_split"
    assert result.evidence["stable_across_halves"] is True


def test_reversed_half_direction_is_unstable() -> None:
    rows_a = [_row("a1", 1, payers=90), _row("a2", 2, payers=1)]
    rows_b = [_row("b1", 1, payers=10), _row("b2", 2, payers=2)]

    result = _evaluate(rows_a, rows_b, config=TEST_CERTIFIED_CONFIG)

    assert result.output == "observing"
    assert result.waiting_reason == "unstable_halves"
    assert result.evidence["stable_across_halves"] is False
    assert result.evidence["overall"]["msprt_decision"] == "threshold_crossed"


def test_install_objective_uses_installs_not_payers() -> None:
    rows_a = [
        _row("a1", 1, payers=5, installs=45, objective="install"),
        _row("a2", 2, payers=5, installs=45, objective="install"),
    ]
    rows_b = [
        _row("b1", 1, payers=45, installs=5, objective="install"),
        _row("b2", 2, payers=45, installs=5, objective="install"),
    ]

    result = _evaluate(rows_a, rows_b, config=TEST_CERTIFIED_CONFIG)

    assert result.output == "auto_split"
    assert result.evidence["result_metric"] == "installs"
    assert result.evidence["overall"]["more_efficient_arm"] == "a"
    assert result.evidence["overall"]["arm_a"]["results"] == 90


def test_different_markets_are_explicitly_incomparable() -> None:
    rows_a, rows_b = _diverging_rows()
    for row in rows_b:
        row.creative_name = "KS_KR-creative.mp4"

    result = _evaluate(rows_a, rows_b, config=TEST_CERTIFIED_CONFIG)

    assert result.output == "observing"
    assert result.waiting_reason == "market_mismatch"


def test_input_hash_is_deterministic_and_order_independent() -> None:
    rows_a, rows_b = _diverging_rows()
    first = _evaluate(rows_a, rows_b, config=TEST_CERTIFIED_CONFIG)
    second = _evaluate(
        list(reversed(rows_a)), list(reversed(rows_b)), config=TEST_CERTIFIED_CONFIG
    )

    assert first.evidence["input_hash"] == second.evidence["input_hash"]
    assert hash_input_evidence({"b": [2, 1], "a": date(2026, 1, 1)}) == (
        hash_input_evidence({"a": date(2026, 1, 1), "b": [2, 1]})
    )
    with pytest.raises(TypeError):
        hash_input_evidence({"unsafe": object()})
