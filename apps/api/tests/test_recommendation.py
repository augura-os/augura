"""Tests for the rule-based recommendation engine (services/recommendation)."""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy.orm import Session

from app.models import Creative, CreativeAsset, CreativeVariant, Performance
from app.services.recommendation import (
    aggregate,
    build_report,
    collect_creative_performance,
    recommend,
    supplementary_reasons,
)


def _metrics(**overrides: object) -> object:
    base = dict(
        creative_id="c1",
        creative_name="test-creative",
        dna_code="D01",
        dna_name="测试",
        spend=1000.0,
        payers=20,
        installs=500,
        cpp=50.0,
        roas=0.03,
        cpi=2.0,
        ipm=3.0,
        row_count=10,
        days_idle=0,
        recent_spend=300.0,
        recent_cpp=50.0,
        variant_count=1,
        observation_partners=[],
        derivation_count=0,
        judged_count=0,
        positive_count=0,
        d3_roas=None,
        d1_retention=None,
    )
    base.update(overrides)

    class M:  # simple stand-in matching CreativeMetrics attributes
        pass

    metrics = M()
    for key, value in base.items():
        setattr(metrics, key, value)
    return metrics


class TestRecommendRules:
    def test_r1_no_spend_iterate(self) -> None:
        action, reasons = recommend(_metrics(spend=0.0, payers=0, cpp=None, roas=None))
        assert action == "ITERATE"
        assert "投放" in reasons[0]

    def test_r2_idle_and_bad_archive(self) -> None:
        action, _ = recommend(_metrics(days_idle=20, cpp=150.0))
        assert action == "ARCHIVE"

    def test_r2_idle_but_good_not_archive(self) -> None:
        action, reasons = recommend(_metrics(days_idle=20, cpp=50.0))
        assert action == "ITERATE"
        assert "无消耗" in reasons[0]

    def test_r25_all_failed_derivations_archive(self) -> None:
        action, reasons = recommend(
            _metrics(judged_count=3, positive_count=0, derivation_count=3)
        )
        assert action == "ARCHIVE"
        assert "耗尽" in reasons[0]

    def test_r25_not_triggered_with_one_positive(self) -> None:
        action, _ = recommend(
            _metrics(judged_count=2, positive_count=1, derivation_count=2)
        )
        assert action != "ARCHIVE"

    def test_r25_not_triggered_below_two_judged(self) -> None:
        action, _ = recommend(
            _metrics(judged_count=1, positive_count=0, derivation_count=1)
        )
        assert action != "ARCHIVE"

    def test_r3_zero_payers_pause(self) -> None:
        action, reasons = recommend(_metrics(spend=97.0, payers=0, cpp=None))
        assert action == "PAUSE"
        assert "0 付费" in reasons[0]

    def test_r3_spend_below_threshold_not_pause(self) -> None:
        action, _ = recommend(_metrics(spend=43.0, payers=0, cpp=None, roas=0.0))
        assert action == "ITERATE"  # falls through to Roas rule

    def test_r4_cpp_extreme_pause(self) -> None:
        action, _ = recommend(_metrics(cpp=185.0))
        assert action == "PAUSE"

    def test_r5_cpp_high_weak_roas_pause(self) -> None:
        action, _ = recommend(_metrics(cpp=128.49, roas=0.007, spend=2056.0))
        assert action == "PAUSE"

    def test_r6_efficient_low_spend_iterate(self) -> None:
        action, reasons = recommend(_metrics(cpp=41.76, spend=752.0))
        assert action == "ITERATE"
        assert "加注" in reasons[0]

    def test_r7_cpp_above_red_iterate(self) -> None:
        action, _ = recommend(_metrics(cpp=150.0, roas=0.03))
        assert action == "ITERATE"

    def test_r8_weak_roas_iterate(self) -> None:
        action, reasons = recommend(_metrics(cpp=88.38, roas=0.0167))
        assert action == "ITERATE"
        assert "Roas" in reasons[0]

    def test_r9_keep(self) -> None:
        action, reasons = recommend(_metrics(cpp=81.41, roas=0.03))
        assert action == "KEEP"
        assert reasons

    def test_priority_archive_beats_pause(self) -> None:
        # idle>14 AND cpp≥180 → ARCHIVE (R2) wins over PAUSE (R4)
        action, _ = recommend(_metrics(days_idle=20, cpp=200.0))
        assert action == "ARCHIVE"

    def test_reasons_contain_numbers(self) -> None:
        _, reasons = recommend(_metrics(cpp=41.76, spend=752.0))
        assert "41.76" in reasons[0]
        assert "752" in reasons[0]


class TestMetricConfigGating:
    """judge_metrics / 阈值配置对判定树的门控（v0.11 指标配置）。"""

    @staticmethod
    def _config(judge: list[str], **thresholds: float):
        from app.services.settings import (
            DEFAULT_THRESHOLDS,
            MetricConfig,
        )

        merged = {**DEFAULT_THRESHOLDS, **thresholds}
        return MetricConfig(profile=[], judge_metrics=judge, thresholds=merged)

    def test_threshold_override_changes_verdict(self) -> None:
        # 默认红线 120：cpp 110 + roas 3% → KEEP；红线改 100 → ITERATE
        metrics = _metrics(cpp=110.0, roas=0.03)
        assert recommend(metrics)[0] == "KEEP"
        config = self._config(["cpp", "d1_roas"], cpp_red_line=100.0)
        action, reasons = recommend(metrics, config)
        assert action == "ITERATE"
        assert "100" in reasons[0]

    def test_cpp_removed_from_judge_disables_cpp_rules(self) -> None:
        # cpp 185 默认 PAUSE；judge_metrics 去掉 cpp 后不再因成本判定
        metrics = _metrics(cpp=185.0, roas=0.03)
        assert recommend(metrics)[0] == "PAUSE"
        config = self._config(["d1_roas"])
        assert recommend(metrics, config)[0] == "KEEP"

    def test_d3_roas_weak_triggers_iterate_when_enabled(self) -> None:
        metrics = _metrics(cpp=80.0, roas=0.03, d3_roas=0.02)
        # 默认不参与判定 → KEEP
        assert recommend(metrics)[0] == "KEEP"
        config = self._config(["cpp", "d1_roas", "d3_roas"])
        action, reasons = recommend(metrics, config)
        assert action == "ITERATE"
        assert "D3 Roas" in reasons[0]

    def test_d1_retention_weak_triggers_iterate_when_enabled(self) -> None:
        metrics = _metrics(cpp=80.0, roas=0.03, d1_retention=0.20)
        config = self._config(["cpp", "d1_roas", "d1_retention"])
        action, reasons = recommend(metrics, config)
        assert action == "ITERATE"
        assert "次留" in reasons[0]

    def test_d3_roas_above_weak_keeps(self) -> None:
        metrics = _metrics(cpp=80.0, roas=0.03, d3_roas=0.08)
        config = self._config(["cpp", "d1_roas", "d3_roas"])
        assert recommend(metrics, config)[0] == "KEEP"


class TestSupplementaryReasons:
    def test_trend_reason(self) -> None:
        reasons = supplementary_reasons(_metrics(cpp=100.0, recent_cpp=140.0))
        assert any("上升" in reason for reason in reasons)

    def test_no_trend_within_threshold(self) -> None:
        assert supplementary_reasons(_metrics(cpp=100.0, recent_cpp=110.0)) == []

    def test_variant_and_partner_reasons(self) -> None:
        reasons = supplementary_reasons(
            _metrics(cpp=None, recent_cpp=None, variant_count=2,
                     observation_partners=["other-creative"])
        )
        assert any("Variant" in reason for reason in reasons)
        assert any("观察对" in reason for reason in reasons)

    def test_evolution_reasons(self) -> None:
        reasons = supplementary_reasons(
            _metrics(derivation_count=5, judged_count=3, positive_count=2)
        )
        assert any("3 次、2 次有效" in reason for reason in reasons)
        assert any("2 次裂变待判定" in reason for reason in reasons)


def _seed_creative(db: Session) -> Creative:
    creative = Creative(id=str(uuid.uuid4()), name="seed-creative")
    asset = CreativeAsset(
        id=str(uuid.uuid4()),
        filename="KS_EN-test-seed-creative-name-long-enough-竖.mp4",
        file_type="video",
        storage_key=f"test/{uuid.uuid4()}",
    )
    db.add_all([creative, asset])
    db.flush()
    db.add(
        CreativeVariant(
            id=str(uuid.uuid4()), creative_id=creative.id, asset_id=asset.id, name="v1"
        )
    )
    stem = "ks_en-test-seed-creative-name-long-enough"
    db.add_all(
        [
            Performance(
                id=str(uuid.uuid4()),
                creative_name=stem,
                date=date(2026, 7, 10),
                spend=100.0,
                installs=50,
                raw={"付费人数": 2, "D1_Roas": 0.02},
            ),
            Performance(
                id=str(uuid.uuid4()),
                creative_name=stem,
                date=date(2026, 7, 18),
                spend=300.0,
                installs=100,
                raw={"付费人数": 4, "D1_Roas": 0.04},
            ),
        ]
    )
    db.flush()
    return creative


class TestAggregate:
    def test_collect_and_aggregate(self, db_session: Session) -> None:
        creative = _seed_creative(db_session)
        rows = collect_creative_performance(db_session, creative)
        assert len(rows) == 2

        metrics = aggregate(
            creative, None, None, rows, max_date=date(2026, 7, 19), variant_count=1
        )
        assert metrics.spend == 400.0
        assert metrics.payers == 6
        assert metrics.cpp == 400.0 / 6
        assert metrics.installs == 150
        assert metrics.days_idle == 1
        # spend-weighted roas: (100*0.02 + 300*0.04) / 400
        assert metrics.roas == 0.035

    def test_build_report_covers_seeded_creative(self, db_session: Session) -> None:
        creative = _seed_creative(db_session)
        report = build_report(db_session, [creative])
        assert len(report.items) == 1
        metrics, action, reasons = report.items[0]
        assert metrics.creative_id == creative.id
        assert action in ("KEEP", "ITERATE", "PAUSE", "ARCHIVE")
        assert reasons
