"""Deterministic, side-effect-free evidence policy for observation pairs.

The default policy is shadow-only. A statistical threshold crossing is not a
97%-precision certification and can never enable ``auto_split`` by itself.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Literal

from app.models import Creative, Performance
from app.services.excel import metrics_from_raw
from app.services.markets import DEFAULT_MARKET_PREFIXES, market_tag
from app.services.observation_stats import (
    DEFAULT_ALPHA,
    ObservationArm,
    cost_ratio,
    msprt_decision,
    msprt_log_likelihood_ratio,
)
from app.services.recommendation import collect_creative_performance

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.models import CreativeAsset, CreativeVariant
    from app.services.matching import PerformanceIndex

OBSERVATION_RULE_VERSION = "observation-policy-v1"
MIN_CERTIFIED_PRECISION = 0.97

PolicyOutput = Literal["observing", "actionable", "auto_split"]


@dataclass(frozen=True)
class ObservationPolicyConfig:
    """Immutable thresholds plus an explicit external certification reference."""

    rule_version: str = OBSERVATION_RULE_VERSION
    min_effect_ratio: float = 1.5
    alpha: float = DEFAULT_ALPHA
    min_results_per_arm: int = 1
    min_spend_per_arm: float = 0.0
    certification_reference: str | None = None
    certified_precision_lower_bound: float | None = None

    def __post_init__(self) -> None:
        if not self.rule_version.strip():
            raise ValueError("rule_version must not be empty")
        if not math.isfinite(self.min_effect_ratio) or self.min_effect_ratio <= 1.0:
            raise ValueError("min_effect_ratio must be finite and greater than one")
        if not math.isfinite(self.alpha) or not 0.0 < self.alpha < 1.0:
            raise ValueError("alpha must be finite and strictly between zero and one")
        if (
            isinstance(self.min_results_per_arm, bool)
            or not isinstance(self.min_results_per_arm, int)
            or self.min_results_per_arm < 1
        ):
            raise ValueError("min_results_per_arm must be a positive integer")
        if not math.isfinite(self.min_spend_per_arm) or self.min_spend_per_arm < 0.0:
            raise ValueError("min_spend_per_arm must be finite and nonnegative")
        if self.certified_precision_lower_bound is not None and (
            not math.isfinite(self.certified_precision_lower_bound)
            or not 0.0 <= self.certified_precision_lower_bound <= 1.0
        ):
            raise ValueError("certified_precision_lower_bound must be in [0, 1]")
        if self.certification_reference is not None and not self.certification_reference.strip():
            raise ValueError("certification_reference must not be empty")

    @property
    def certified(self) -> bool:
        return bool(self.certification_reference) and (
            self.certified_precision_lower_bound is not None
            and self.certified_precision_lower_bound >= MIN_CERTIFIED_PRECISION
        )


DEFAULT_POLICY_CONFIG = ObservationPolicyConfig()


@dataclass(frozen=True)
class PolicyResult:
    output: PolicyOutput
    waiting_reason: str
    rule_version: str
    evidence: dict[str, object]


@dataclass(frozen=True)
class _EvidenceRow:
    row_id: str
    row_date: date
    creative_name: str
    spend: float
    payers: int
    installs: int
    objective: str
    market: str

    def as_dict(self) -> dict[str, object]:
        return {
            "row_id": self.row_id,
            "date": self.row_date.isoformat(),
            "creative_name": self.creative_name,
            "spend": self.spend,
            "payers": self.payers,
            "installs": self.installs,
            "objective": self.objective,
            "market": self.market,
        }


class _InvalidPerformanceData(ValueError):
    pass


def _canonical(value: object) -> object:
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite numbers cannot be hashed")
        return 0.0 if value == 0.0 else value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("evidence mapping keys must be strings")
        return {key: _canonical(item) for key, item in sorted(value.items())}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_canonical(item) for item in value]
    raise TypeError(f"unsupported evidence type: {type(value).__name__}")


def hash_input_evidence(evidence: Mapping[str, object]) -> str:
    """Hash canonical JSON without coercing arbitrary objects through ``repr``."""

    canonical = json.dumps(
        _canonical(evidence),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def collect_observation_performance(
    db: Session,
    creative_a: Creative,
    creative_b: Creative,
    *,
    all_performances: Sequence[Performance] | None = None,
    performance_index: PerformanceIndex | None = None,
    variants_a: Sequence[CreativeVariant] | None = None,
    variants_b: Sequence[CreativeVariant] | None = None,
    assets_by_id: dict[str, CreativeAsset] | None = None,
) -> tuple[list[Performance], list[Performance]]:
    """Collect both arms through the existing creative/asset matching semantics."""

    rows_a = collect_creative_performance(
        db,
        creative_a,
        all_performances=all_performances,
        performance_index=performance_index,
        variants=variants_a,
        assets_by_id=assets_by_id,
    )
    rows_b = collect_creative_performance(
        db,
        creative_b,
        all_performances=all_performances,
        performance_index=performance_index,
        variants=variants_b,
        assets_by_id=assets_by_id,
    )
    return rows_a, rows_b


def _as_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else None


def _eligible_rows(
    rows: Sequence[Performance], *, start: date, as_of: date
) -> tuple[list[Performance], dict[str, int]]:
    eligible: list[Performance] = []
    counts = {"pre_registration": 0, "future": 0, "undated": 0}
    for row in rows:
        row_date = _as_date(row.date)
        if row_date is None:
            counts["undated"] += 1
        elif row_date < start:
            counts["pre_registration"] += 1
        elif row_date > as_of:
            counts["future"] += 1
        else:
            eligible.append(row)
    return eligible, counts


def _prepare_rows(
    rows: Sequence[Performance],
    *,
    prefixes: tuple[str, ...],
    aliases: Mapping[str, str] | None,
) -> list[_EvidenceRow]:
    prepared: list[_EvidenceRow] = []
    try:
        for row in rows:
            row_date = _as_date(row.date)
            if row_date is None:
                raise _InvalidPerformanceData("eligible row has no date")
            spend = float(row.spend or 0.0)
            installs = int(row.installs or 0)
            payers = int(metrics_from_raw(row.raw or {})["payers"] or 0)
            if not math.isfinite(spend) or min(spend, installs, payers) < 0:
                raise _InvalidPerformanceData("negative or non-finite performance data")
            creative_name = row.creative_name if isinstance(row.creative_name, str) else ""
            market, _rest = market_tag(
                creative_name, prefixes, dict(aliases) if aliases is not None else None
            )
            prepared.append(
                _EvidenceRow(
                    row_id=str(row.id or ""),
                    row_date=row_date,
                    creative_name=creative_name,
                    spend=spend,
                    payers=payers,
                    installs=installs,
                    objective=(row.optimization_type or "").strip().lower(),
                    market=market,
                )
            )
    except (TypeError, ValueError, OverflowError) as exc:
        raise _InvalidPerformanceData("invalid performance data") from exc
    prepared.sort(key=lambda row: json.dumps(row.as_dict(), sort_keys=True))
    return prepared


def _aggregate(rows: Sequence[_EvidenceRow], objective: str) -> ObservationArm:
    return ObservationArm(
        spend=math.fsum(row.spend for row in rows),
        results=sum(row.payers if objective == "aeo" else row.installs for row in rows),
    )


def _arm_evidence(arm: ObservationArm) -> dict[str, object]:
    cost = arm.spend / arm.results if arm.results > 0 else None
    return {"spend": arm.spend, "results": arm.results, "cost_per_result": cost}


def _comparison(arm_a: ObservationArm, arm_b: ObservationArm) -> dict[str, object]:
    evidence: dict[str, object] = {
        "arm_a": _arm_evidence(arm_a),
        "arm_b": _arm_evidence(arm_b),
        "cost_ratio": cost_ratio(arm_a, arm_b),
    }
    if arm_a.results > 0 and arm_b.results > 0:
        cost_a = arm_a.spend / arm_a.results
        cost_b = arm_b.spend / arm_b.results
        direction = "a" if cost_a < cost_b else "b" if cost_b < cost_a else "equal"
    else:
        direction = "unknown"
    evidence["more_efficient_arm"] = direction
    return evidence


def _config_evidence(config: ObservationPolicyConfig) -> dict[str, object]:
    return {
        "rule_version": config.rule_version,
        "min_effect_ratio": config.min_effect_ratio,
        "alpha": config.alpha,
        "min_results_per_arm": config.min_results_per_arm,
        "min_spend_per_arm": config.min_spend_per_arm,
        "certification_reference": config.certification_reference,
        "certified_precision_lower_bound": config.certified_precision_lower_bound,
    }


def evaluate_observation_policy(
    *,
    creative_id_a: str,
    creative_id_b: str,
    rows_a: Sequence[Performance],
    rows_b: Sequence[Performance],
    registered_at: datetime | None,
    evidence_start_at: date | None,
    as_of: date,
    config: ObservationPolicyConfig = DEFAULT_POLICY_CONFIG,
    market_prefixes: Sequence[str] = DEFAULT_MARKET_PREFIXES,
    market_aliases: Mapping[str, str] | None = None,
) -> PolicyResult:
    """Evaluate already-collected rows; perform no I/O and never auto-merge."""

    evidence: dict[str, object] = {
        "as_of": as_of.isoformat(),
        "registered_at": registered_at.isoformat() if registered_at else None,
        "evidence_start_at": evidence_start_at.isoformat() if evidence_start_at else None,
        "rows_supplied_a": len(rows_a),
        "rows_supplied_b": len(rows_b),
        "shadow": not config.certified,
        "policy": _config_evidence(config),
    }
    prefixes = tuple(market_prefixes)

    def finish(
        waiting_reason: str,
        *,
        output: PolicyOutput = "observing",
        eligible_a: Sequence[_EvidenceRow] = (),
        eligible_b: Sequence[_EvidenceRow] = (),
    ) -> PolicyResult:
        evidence["input_hash"] = hash_input_evidence(
            {
                "creative_id_a": creative_id_a,
                "creative_id_b": creative_id_b,
                "registered_at": registered_at,
                "evidence_start_at": evidence_start_at,
                "as_of": as_of,
                "policy": _config_evidence(config),
                "rows_a": [row.as_dict() for row in eligible_a],
                "rows_b": [row.as_dict() for row in eligible_b],
            }
        )
        return PolicyResult(output, waiting_reason, config.rule_version, evidence)

    if registered_at is None:
        return finish("registration_missing")
    if evidence_start_at is None:
        return finish("evidence_start_missing")

    effective_start = max(registered_at.date(), evidence_start_at)
    evidence["effective_start"] = effective_start.isoformat()
    raw_a, excluded_a = _eligible_rows(rows_a, start=effective_start, as_of=as_of)
    raw_b, excluded_b = _eligible_rows(rows_b, start=effective_start, as_of=as_of)
    evidence["excluded_a"] = excluded_a
    evidence["excluded_b"] = excluded_b
    evidence["eligible_rows_a"] = len(raw_a)
    evidence["eligible_rows_b"] = len(raw_b)
    if not rows_a and not rows_b:
        return finish("no_performance_data")
    if not raw_a or not raw_b:
        return finish("no_post_registration_data")

    try:
        eligible_a = _prepare_rows(raw_a, prefixes=prefixes, aliases=market_aliases)
        eligible_b = _prepare_rows(raw_b, prefixes=prefixes, aliases=market_aliases)
    except _InvalidPerformanceData:
        return finish("invalid_performance_data")

    all_rows = [*eligible_a, *eligible_b]
    known_objectives = {"aeo", "install", "vo"}
    if any(row.objective not in known_objectives for row in all_rows):
        return finish("unknown_objective", eligible_a=eligible_a, eligible_b=eligible_b)
    objectives_a = {row.objective for row in eligible_a}
    objectives_b = {row.objective for row in eligible_b}
    if len(objectives_a) != 1 or len(objectives_b) != 1:
        return finish("mixed_objectives", eligible_a=eligible_a, eligible_b=eligible_b)
    objective = next(iter(objectives_a))
    if objective != next(iter(objectives_b)):
        return finish("objective_mismatch", eligible_a=eligible_a, eligible_b=eligible_b)
    if objective == "vo":
        return finish("unsupported_objective", eligible_a=eligible_a, eligible_b=eligible_b)
    evidence["objective"] = objective
    evidence["result_metric"] = "payers" if objective == "aeo" else "installs"

    if any(not row.market for row in all_rows):
        return finish("market_unknown", eligible_a=eligible_a, eligible_b=eligible_b)
    markets_a = {row.market for row in eligible_a}
    markets_b = {row.market for row in eligible_b}
    if len(markets_a) != 1 or len(markets_b) != 1:
        return finish("mixed_markets", eligible_a=eligible_a, eligible_b=eligible_b)
    market = next(iter(markets_a))
    if market != next(iter(markets_b)):
        return finish("market_mismatch", eligible_a=eligible_a, eligible_b=eligible_b)
    evidence["market"] = market

    arm_a = _aggregate(eligible_a, objective)
    arm_b = _aggregate(eligible_b, objective)
    overall = _comparison(arm_a, arm_b)
    overall["msprt_log_likelihood_ratio"] = msprt_log_likelihood_ratio(arm_a, arm_b)
    overall["msprt_decision"] = msprt_decision(arm_a, arm_b, alpha=config.alpha)
    evidence["overall"] = overall

    if min(arm_a.results, arm_b.results) == 0:
        return finish("zero_results", eligible_a=eligible_a, eligible_b=eligible_b)
    if min(arm_a.spend, arm_b.spend) <= 0.0:
        return finish("insufficient_spend", eligible_a=eligible_a, eligible_b=eligible_b)
    if min(arm_a.results, arm_b.results) < config.min_results_per_arm:
        return finish("insufficient_results", eligible_a=eligible_a, eligible_b=eligible_b)
    if min(arm_a.spend, arm_b.spend) < config.min_spend_per_arm:
        return finish("insufficient_spend", eligible_a=eligible_a, eligible_b=eligible_b)

    ratio = overall["cost_ratio"]
    if not isinstance(ratio, float) or ratio < config.min_effect_ratio:
        return finish("effect_below_minimum", eligible_a=eligible_a, eligible_b=eligible_b)
    if overall["msprt_decision"] != "threshold_crossed":
        return finish("msprt_not_crossed", eligible_a=eligible_a, eligible_b=eligible_b)

    dates = sorted({row.row_date for row in all_rows})
    if len(dates) < 2:
        return finish(
            "insufficient_chronological_halves",
            eligible_a=eligible_a,
            eligible_b=eligible_b,
        )
    midpoint = len(dates) // 2
    first_dates = set(dates[:midpoint])
    first_a = [row for row in eligible_a if row.row_date in first_dates]
    first_b = [row for row in eligible_b if row.row_date in first_dates]
    second_a = [row for row in eligible_a if row.row_date not in first_dates]
    second_b = [row for row in eligible_b if row.row_date not in first_dates]
    half_arms = (
        _aggregate(first_a, objective),
        _aggregate(first_b, objective),
        _aggregate(second_a, objective),
        _aggregate(second_b, objective),
    )
    first = _comparison(half_arms[0], half_arms[1])
    second = _comparison(half_arms[2], half_arms[3])
    evidence["halves"] = {
        "first": {"start": dates[0].isoformat(), "end": dates[midpoint - 1].isoformat(), **first},
        "second": {"start": dates[midpoint].isoformat(), "end": dates[-1].isoformat(), **second},
    }
    if any(min(arm.spend, arm.results) <= 0 for arm in half_arms):
        return finish("insufficient_half_data", eligible_a=eligible_a, eligible_b=eligible_b)

    direction = overall["more_efficient_arm"]
    stable = all(
        half["more_efficient_arm"] == direction
        and isinstance(half["cost_ratio"], float)
        and half["cost_ratio"] >= config.min_effect_ratio
        for half in (first, second)
    )
    evidence["stable_across_halves"] = stable
    if not stable:
        return finish("unstable_halves", eligible_a=eligible_a, eligible_b=eligible_b)

    evidence["performance_rule_passed"] = True
    evidence["resolution_type"] = "performance_split"
    if not config.certified:
        return finish("uncertified_policy", eligible_a=eligible_a, eligible_b=eligible_b)
    return finish("", output="auto_split", eligible_a=eligible_a, eligible_b=eligible_b)
