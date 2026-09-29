"""Monetized expected-loss priority for the Daily Brief.

Every recommendation carries two numbers: ``priority_dollars`` (roughly what
acting — or not acting — on this call is worth per day) and ``confidence``
(the posterior probability of the direction the rule asserts). Posteriors are
closed-form conjugate approximations over CPP: stdlib ``math`` only,
deterministic, no LLM, no new dependencies.

Why dollars instead of a 0-100 score: the unit is comparable across the
urgent/optimize groups, the summary line can sum it ("≈ $X/day at risk"), and
small samples automatically sink (few payers → wide posterior → probability
near 0.5) — the "30 judgements vs 3000" problem needs no extra rule.

Fallback contract: any anomaly in the posterior path degrades to the plain
spend-weighted score, so a math edge case can never break the brief.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.recommendation import CreativeMetrics, RecommendationAction

RECENT_WINDOW_DAYS = 7.0
SCALE_RAMP_DAYS = 30.0  # 加注空间按一个月爬坡折算成日度金额
SPEND_SIGNIFICANT = 1000.0  # 与 recommendation.SPEND_SIGNIFICANT 同口径
FALLBACK_WEIGHT = 0.1  # 非金额化条目的占位低权


def _phi(z: float) -> float:
    """Standard normal CDF (via math.erf, no scipy)."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def prob_cpp_over(spend: float, payers: int, red_line: float) -> float:
    """P(true CPP > red_line | observed spend/payers).

    payers ≥ 1: log(CPP) ~ Normal(log(spend/payers), 1/√payers) — the payer
    count is the sample size, so few payers widen the posterior toward 0.5.
    payers = 0: Poisson upper bound — if the true CPP were exactly red_line
    we'd expect spend/red payers; P(observing zero) gives the tail.
    """
    if spend <= 0 or red_line <= 0:
        return 0.5
    if payers <= 0:
        expected_at_red = spend / red_line
        return 1.0 - math.exp(-expected_at_red)
    mean = math.log(spend / payers)
    se = 1.0 / math.sqrt(payers)
    return _phi((mean - math.log(red_line)) / se)


def _daily_spend(m: "CreativeMetrics") -> float:
    """Near-term daily burn: recent 7-day window, else whole-window average."""
    if m.recent_spend > 0:
        return m.recent_spend / RECENT_WINDOW_DAYS
    if m.spend > 0:
        return m.spend / max(m.row_count, 1)
    return 0.0


def _score(
    m: "CreativeMetrics",
    action: "RecommendationAction",
    red_line: float,
    *,
    scale_headroom: float | None,
) -> tuple[float, float]:
    daily = _daily_spend(m)
    p_over = prob_cpp_over(m.spend, m.payers, red_line)

    if action in ("PAUSE", "ARCHIVE"):
        # 每日浪费 = 日消耗 × 超红幅度 × 超红把握；0 付费 = 消耗完全无回收
        if m.payers == 0:
            overshoot = 1.0
        elif m.cpp is not None and m.cpp > 0:
            overshoot = max(0.0, 1.0 - red_line / m.cpp)
        else:
            overshoot = 0.0
        return daily * overshoot * p_over, p_over

    if action == "ITERATE" and m.cpp is not None and 0 < m.cpp < red_line:
        # 加注型：效率领先未起量。价值 = 领先幅度 × 日度可加注空间 × 领先把握
        gain = (red_line - m.cpp) / red_line
        headroom = (
            scale_headroom
            if scale_headroom is not None
            else max(0.0, SPEND_SIGNIFICANT - m.spend)
        )
        p_under = 1.0 - p_over
        return gain * (headroom / SCALE_RAMP_DAYS) * p_under, p_under

    # 其余（降本迭代 / 保持等）：占位低权，只保证组内稳定排序
    return daily * FALLBACK_WEIGHT, max(p_over, 1.0 - p_over)


def priority_score(
    m: "CreativeMetrics",
    action: "RecommendationAction",
    red_line: float,
    *,
    scale_headroom: float | None = None,
) -> tuple[float, float]:
    """(priority_dollars, confidence); degrades to spend weighting on error."""
    try:
        dollars, confidence = _score(m, action, red_line, scale_headroom=scale_headroom)
    except Exception:  # noqa: BLE001 — 数学边界永远不能拖垮简报
        return _daily_spend(m) * FALLBACK_WEIGHT, 0.5
    if not (math.isfinite(dollars) and math.isfinite(confidence)):
        return _daily_spend(m) * FALLBACK_WEIGHT, 0.5
    return max(dollars, 0.0), min(max(confidence, 0.0), 1.0)
