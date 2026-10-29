"""Pure statistical evidence for observation-pair resolution.

A threshold crossing is evidence against equal cost per result, not proof of a
particular business verdict. ``continue`` never establishes equivalence. The
mSPRT alpha controls its nominal sequential Type-I error, not auto-close
accuracy: alpha=0.03 does not imply 97% precision or accuracy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from numbers import Integral, Real
from typing import Literal

import numpy as np

ALTERNATIVE_WEIGHT = 0.5
BETA_SHAPE = 2.0
DEFAULT_ALPHA = 0.03

_QUADRATURE_POINTS = 64
_QUADRATURE_RADIUS = 16.0


@dataclass(frozen=True)
class ObservationArm:
    """Cumulative spend and nonnegative integer results for one arm."""

    spend: float
    results: int


def _arm_values(arm: ObservationArm) -> tuple[float, int] | None:
    if (
        isinstance(arm.spend, bool)
        or not isinstance(arm.spend, Real)
        or isinstance(arm.results, bool)
        or not isinstance(arm.results, Integral)
    ):
        return None
    try:
        spend = float(arm.spend)
    except (OverflowError, ValueError):
        return None
    results = int(arm.results)
    if not math.isfinite(spend) or spend < 0 or results < 0:
        return None
    return spend, results


def _pair_values(
    arm_a: ObservationArm, arm_b: ObservationArm
) -> tuple[float, int, float, int] | None:
    values_a = _arm_values(arm_a)
    values_b = _arm_values(arm_b)
    if values_a is None or values_b is None:
        return None
    spend_a, results_a = values_a
    spend_b, results_b = values_b
    return spend_a, results_a, spend_b, results_b


def cost_ratio(arm_a: ObservationArm, arm_b: ObservationArm) -> float | None:
    """Return larger cost/result divided by smaller cost/result.

    The symmetric ratio is at least one and describes practical separation only;
    a value near one is not an equivalence test. Missing or invalid costs return
    ``None``.
    """
    values = _pair_values(arm_a, arm_b)
    if values is None:
        return None
    spend_a, results_a, spend_b, results_b = values
    if min(spend_a, spend_b, results_a, results_b) <= 0:
        return None
    cost_a = spend_a / results_a
    cost_b = spend_b / results_b
    if min(cost_a, cost_b) <= 0:
        return None
    ratio = max(cost_a, cost_b) / min(cost_a, cost_b)
    return ratio if math.isfinite(ratio) else None


def _null_probability(spend_a: float, spend_b: float) -> float | None:
    if spend_a <= 0 or spend_b <= 0:
        return None
    scale = max(spend_a, spend_b)
    scaled_a = spend_a / scale
    scaled_b = spend_b / scale
    p0 = scaled_a / (scaled_a + scaled_b)
    return p0 if 0.0 < p0 < 1.0 else None


@lru_cache(maxsize=1)
def _legendre_rule() -> tuple[np.ndarray, np.ndarray]:
    return np.polynomial.legendre.leggauss(_QUADRATURE_POINTS)


def _log_beta_mixture_integral(results_a: int, total: int, p0: float) -> float | None:
    """Integrate the fixed Beta(2,2)/null likelihood ratio in logit space.

    With k=results_a and n=total, the alternative/null ratio is
    integral[(p/p0)^k ((1-p)/(1-p0))^(n-k) Beta(p; 2, 2) dp].
    The binomial coefficient cancels. Both Beta parameters and the 50/50
    null mixture are fixed across all sequential looks, never fitted to data.
    """
    results_b = total - results_a
    shape = BETA_SHAPE
    mode = math.log(results_a + shape) - math.log(results_b + shape)
    scale = math.sqrt(1.0 / (results_a + shape) + 1.0 / (results_b + shape))
    nodes, weights = _legendre_rule()
    t = mode + scale * _QUADRATURE_RADIUS * nodes
    log_dt = math.log(scale * _QUADRATURE_RADIUS) + np.log(weights)

    log_p = -np.logaddexp(0.0, -t)
    log_one_minus_p = -np.logaddexp(0.0, t)
    log_normalizer = 2.0 * math.lgamma(shape) - math.lgamma(2.0 * shape)
    log_terms = (
        results_a * (log_p - math.log(p0))
        + results_b * (log_one_minus_p - math.log1p(-p0))
        + shape * (log_p + log_one_minus_p)
        - log_normalizer
        + log_dt
    )
    max_log = float(np.max(log_terms))
    if not math.isfinite(max_log):
        return None
    log_integral = max_log + math.log(float(np.exp(log_terms - max_log).sum()))
    return log_integral if math.isfinite(log_integral) else None


def _log_add_exp(log_a: float, log_b: float) -> float:
    largest = max(log_a, log_b)
    return largest + math.log(math.exp(log_a - largest) + math.exp(log_b - largest))


def msprt_log_likelihood_ratio(arm_a: ObservationArm, arm_b: ObservationArm) -> float | None:
    """Return the conditional-binomial mixture SPRT log likelihood ratio.

    Conditional on total results, equal cost/result under fixed spend exposures
    gives A | A+B ~ Binomial(n, p0), p0=spendA/(spendA+spendB). The numerator is
    50% point null plus 50% symmetric Beta(2,2) alternative; the denominator is
    the point null. Zero results or invalid/degenerate exposures return ``None``.
    """
    values = _pair_values(arm_a, arm_b)
    if values is None:
        return None
    spend_a, results_a, spend_b, results_b = values
    total = results_a + results_b
    if total == 0:
        return None
    p0 = _null_probability(spend_a, spend_b)
    if p0 is None:
        return None
    log_integral = _log_beta_mixture_integral(results_a, total, p0)
    if log_integral is None:
        return None
    return _log_add_exp(
        math.log1p(-ALTERNATIVE_WEIGHT),
        math.log(ALTERNATIVE_WEIGHT) + log_integral,
    )


def _unit_interval(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite number strictly between 0 and 1")
    number = float(value)
    if not math.isfinite(number) or not 0.0 < number < 1.0:
        raise ValueError(f"{name} must be a finite number strictly between 0 and 1")
    return number


def msprt_decision(
    arm_a: ObservationArm,
    arm_b: ObservationArm,
    *,
    alpha: float = DEFAULT_ALPHA,
) -> Literal["continue", "threshold_crossed"] | None:
    """Cross log(1/alpha), otherwise continue; never declare equivalence.

    Alpha is a nominal sequential false-positive budget under the null. It is
    not a guarantee that automatic decisions are correct at rate 1-alpha.
    """
    alpha = _unit_interval(alpha, "alpha")
    log_ratio = msprt_log_likelihood_ratio(arm_a, arm_b)
    if log_ratio is None:
        return None
    return "threshold_crossed" if log_ratio >= math.log(1.0 / alpha) else "continue"


def _beta_continued_fraction(a: float, b: float, x: float) -> float:
    max_iterations = 200
    epsilon = 3.0e-14
    tiny = 1.0e-300
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for iteration in range(1, max_iterations + 1):
        m2 = 2 * iteration
        coefficient = iteration * (b - iteration) * x / ((qam + m2) * (a + m2))
        d = 1.0 + coefficient * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + coefficient / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        coefficient = -(a + iteration) * (qab + iteration) * x / ((a + m2) * (qap + m2))
        d = 1.0 + coefficient * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + coefficient / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < epsilon:
            break
    return h


def _regularized_beta(x: float, a: float, b: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    log_front = (
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    )
    front = math.exp(log_front)
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _beta_continued_fraction(a, b, x) / a
    return 1.0 - front * _beta_continued_fraction(b, a, 1.0 - x) / b


def clopper_pearson_lower_bound(
    successes: int, trials: int, *, confidence: float = 0.95
) -> float | None:
    """Exact one-sided Clopper-Pearson lower bound for Bernoulli precision.

    For x>0, solves P_p(X >= x) = 1-confidence using X~Binomial(n,p).
    No trials or invalid counts return ``None``. This bound estimates decision
    precision from labeled outcomes; it is unrelated to the mSPRT alpha.
    """
    confidence = _unit_interval(confidence, "confidence")
    if (
        isinstance(successes, bool)
        or not isinstance(successes, Integral)
        or isinstance(trials, bool)
        or not isinstance(trials, Integral)
    ):
        return None
    successes = int(successes)
    trials = int(trials)
    if trials <= 0 or successes < 0 or successes > trials:
        return None
    if successes == 0:
        return 0.0
    alpha = 1.0 - confidence
    if successes == trials:
        return alpha ** (1.0 / trials)

    low = 0.0
    high = 1.0
    for _ in range(80):
        midpoint = (low + high) / 2.0
        cumulative = _regularized_beta(midpoint, successes, trials - successes + 1)
        if cumulative > alpha:
            high = midpoint
        else:
            low = midpoint
    return (low + high) / 2.0
