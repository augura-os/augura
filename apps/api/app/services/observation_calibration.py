"""Certify selective observation policies on independent labeled decisions."""

from __future__ import annotations

import math
from dataclasses import dataclass
from numbers import Integral, Real

from app.services.observation_stats import clopper_pearson_lower_bound


@dataclass(frozen=True)
class PolicyCalibration:
    policy_id: str
    errors: int
    automated: int
    candidates: int
    p_value: float


@dataclass(frozen=True)
class CertificationResult:
    certified_policy_id: str | None
    risk_limit: float
    confidence: float
    candidates: tuple[PolicyCalibration, ...]
    reason: str


def _valid_rate(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be between 0 and 1")
    number = float(value)
    if not math.isfinite(number) or not 0.0 < number < 1.0:
        raise ValueError(f"{name} must be between 0 and 1")
    return number


def binomial_log_cdf(errors: int, trials: int, probability: float) -> float:
    """Log P(X <= errors), evaluated without a SciPy dependency."""
    if trials < 0 or errors < 0:
        return 0.0
    if errors >= trials:
        return 0.0
    terms = []
    for count in range(errors + 1):
        terms.append(
            math.lgamma(trials + 1)
            - math.lgamma(count + 1)
            - math.lgamma(trials - count + 1)
            + count * math.log(probability)
            + (trials - count) * math.log1p(-probability)
        )
    largest = max(terms)
    return largest + math.log(sum(math.exp(term - largest) for term in terms))


def certify_policy(
    candidates: list[tuple[str, list[bool], int]],
    *,
    risk_limit: float = 0.03,
    confidence: float = 0.95,
) -> CertificationResult:
    """Apply LTT-style Bonferroni certification to conditional decision risk.

    Each candidate is ``(policy_id, errors_on_automated, total_candidates)``.
    Labels must come from independent human decisions, not model-generated targets.
    Among policies passing the FWER-corrected risk test, maximize labeled coverage.
    """
    risk_limit = _valid_rate(risk_limit, "risk_limit")
    confidence = _valid_rate(confidence, "confidence")
    if not candidates:
        return CertificationResult(None, risk_limit, confidence, (), "no_candidates")
    if len({policy_id for policy_id, _, _ in candidates}) != len(candidates):
        raise ValueError("policy_id values must be unique")
    evaluated: list[PolicyCalibration] = []
    for policy_id, errors, total in candidates:
        if (
            not policy_id
            or isinstance(total, bool)
            or not isinstance(total, Integral)
            or total < 0
            or any(not isinstance(error, bool) for error in errors)
        ):
            raise ValueError("invalid candidate labels or coverage")
        if len(errors) > total:
            raise ValueError("automated decisions cannot exceed total candidates")
        error_count = sum(errors)
        log_p = binomial_log_cdf(error_count, len(errors), risk_limit)
        p_value = math.exp(log_p) if log_p > -745.0 else 0.0
        evaluated.append(
            PolicyCalibration(policy_id, error_count, len(errors), total, p_value)
        )
    correction = (1.0 - confidence) / len(candidates)
    certified = [item for item in evaluated if item.p_value <= correction]
    if not certified:
        return CertificationResult(
            None, risk_limit, confidence, tuple(evaluated), "risk_not_controlled"
        )
    winner = max(certified, key=lambda item: (item.candidates, -item.errors, item.policy_id))
    return CertificationResult(
        winner.policy_id, risk_limit, confidence, tuple(evaluated), "certified"
    )


def independent_precision_lower_bound(
    errors: list[bool], *, confidence: float = 0.95
) -> float | None:
    """Final-test precision bound; keep this set out of policy selection."""
    if any(not isinstance(error, bool) for error in errors):
        raise ValueError("errors must be boolean labels")
    return clopper_pearson_lower_bound(
        len(errors) - sum(errors), len(errors), confidence=confidence
    )
