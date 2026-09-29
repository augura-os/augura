"""Tests for the monetized expected-loss priority (services/priority).

Anchors are hand-computed from the closed forms in the module:
- payers ≥ 1: P = Φ((log(spend/payers) − log(red)) · √payers)
- payers = 0: P = 1 − exp(−spend/red)
"""
from __future__ import annotations

import math

import pytest

from app.services.priority import priority_score, prob_cpp_over


def _m(
    *,
    spend: float,
    payers: int,
    cpp: float | None,
    recent_spend: float = 0.0,
    row_count: int = 10,
) -> object:
    class M:  # stand-in matching the CreativeMetrics attributes priority reads
        pass

    m = M()
    m.spend = spend
    m.payers = payers
    m.cpp = cpp
    m.recent_spend = recent_spend
    m.row_count = row_count
    return m


class TestProbCppOver:
    def test_cpp_exactly_at_red_is_fifty_fifty(self) -> None:
        # cpp == red → z = 0 → P = 0.5，与样本量无关
        assert prob_cpp_over(3600.0, 30, 120.0) == pytest.approx(0.5)
        assert prob_cpp_over(360000.0, 3000, 120.0) == pytest.approx(0.5)

    def test_small_sample_stays_uncertain(self) -> None:
        # cpp = 1.05×red, payers=30 → z = ln(1.05)·√30 ≈ 0.2673 → Φ ≈ 0.6053
        assert prob_cpp_over(3780.0, 30, 120.0) == pytest.approx(0.6053, abs=1e-3)

    def test_large_sample_is_confident(self) -> None:
        # cpp = 1.05×red, payers=3000 → z = ln(1.05)·√3000 ≈ 2.6726 → Φ ≈ 0.9962
        assert prob_cpp_over(378000.0, 3000, 120.0) == pytest.approx(0.9962, abs=1e-3)

    def test_zero_payers_heavy_spend_is_near_certain(self) -> None:
        # spend/red = 20 → 1 − e^−20 ≈ 1
        assert prob_cpp_over(2400.0, 0, 120.0) == pytest.approx(1.0, abs=1e-6)

    def test_zero_payers_light_spend_stays_uncertain(self) -> None:
        # spend/red = 0.5 → 1 − e^−0.5 ≈ 0.3935
        assert prob_cpp_over(60.0, 0, 120.0) == pytest.approx(0.3935, abs=1e-3)

    def test_no_data_is_fifty_fifty(self) -> None:
        assert prob_cpp_over(0.0, 0, 120.0) == 0.5


class TestPriorityScore:
    def test_big_sample_pause_outranks_small_sample(self) -> None:
        """§10 回归：同样超红 5%、同样日消耗，大样本建议排在前面。"""
        small = _m(spend=3780.0, payers=30, cpp=126.0, recent_spend=700.0)
        big = _m(spend=378000.0, payers=3000, cpp=126.0, recent_spend=700.0)
        small_dollars, small_conf = priority_score(small, "PAUSE", 120.0)
        big_dollars, big_conf = priority_score(big, "PAUSE", 120.0)
        assert big_dollars > small_dollars > 0
        assert big_conf > small_conf

    def test_zero_payers_pause_values_full_daily_spend(self) -> None:
        # 0 付费 = 消耗完全无回收：dollars ≈ 日消耗 × 把握(≈1)
        m = _m(spend=2400.0, payers=0, cpp=None, recent_spend=700.0)
        dollars, confidence = priority_score(m, "PAUSE", 120.0)
        assert dollars == pytest.approx(100.0, abs=0.01)
        assert confidence == pytest.approx(1.0, abs=1e-6)

    def test_efficient_not_scaled_uses_headroom(self) -> None:
        # gain=0.5, headroom=1000−360=640, p_under=Φ(ln2·√6)≈0.9552
        m = _m(spend=360.0, payers=6, cpp=60.0, recent_spend=280.0)
        dollars, confidence = priority_score(m, "ITERATE", 120.0)
        expected = 0.5 * (640.0 / 30.0) * 0.95521
        assert dollars == pytest.approx(expected, abs=0.05)
        assert confidence == pytest.approx(0.9552, abs=1e-3)

    def test_scale_headroom_override(self) -> None:
        m = _m(spend=360.0, payers=6, cpp=60.0, recent_spend=280.0)
        dollars, _ = priority_score(m, "ITERATE", 120.0, scale_headroom=100.0)
        assert dollars == pytest.approx(0.5 * (100.0 / 30.0) * 0.95521, abs=0.02)

    def test_keep_gets_placeholder_weight(self) -> None:
        m = _m(spend=1000.0, payers=20, cpp=50.0, recent_spend=350.0)
        dollars, _ = priority_score(m, "KEEP", 120.0)
        assert dollars == pytest.approx(50.0 * 0.1)

    def test_no_spend_scores_zero(self) -> None:
        m = _m(spend=0.0, payers=0, cpp=None, recent_spend=0.0, row_count=0)
        dollars, confidence = priority_score(m, "ITERATE", 120.0)
        assert dollars == 0.0
        assert 0.0 <= confidence <= 1.0

    def test_garbage_input_degrades_gracefully(self) -> None:
        m = _m(spend=-5.0, payers=-1, cpp=None, recent_spend=0.0)
        dollars, confidence = priority_score(m, "PAUSE", 120.0)
        assert math.isfinite(dollars) and dollars >= 0.0
        assert 0.0 <= confidence <= 1.0
