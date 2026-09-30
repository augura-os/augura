"""Tests for the rule-based recommendation engine (services/recommendation)."""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy.orm import Session

from app.models import (
    Creative,
    CreativeAsset,
    CreativeVariant,
    Performance,
    VariantDerivation,
)
from app.services.recommendation import (
    aggregate,
    build_report,
    collect_creative_performance,
    recommend,
    render_reason_zh,
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
        impressions=0,
        recent_spend=300.0,
        recent_cpp=50.0,
        variant_count=1,
        observation_partners=[],
        derivation_count=0,
        judged_count=0,
        positive_count=0,
        exhausted_factors=(),
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


class TestDataSufficiencyGate:
    """R0 数据充分性闸门：消耗与曝光双低 → 观察期，不下方向性结论。"""

    def test_low_spend_low_impressions_observation(self) -> None:
        # 线上真实案例：消耗 $3.91 / 0 付费 / 低曝光，原链会落到 "成本 - 健康"
        verdict = recommend(
            _metrics(spend=3.91, payers=0, cpp=None, roas=0.0, impressions=833)
        )
        assert verdict.action == "ITERATE"
        assert verdict.reason_code == "insufficient_data"
        assert "数据不足" in verdict.reasons[0]
        assert "健康" not in verdict.reasons[0]

    def test_spend_at_signal_line_enters_normal_chain(self) -> None:
        # 消耗达标（≥$10 且 ≥$50 暂停线）→ 原 R3 0 付费暂停链不受影响
        verdict = recommend(_metrics(spend=60.0, payers=0, cpp=None, impressions=100))
        assert verdict.action == "PAUSE"
        assert "0 付费" in verdict.reasons[0]

    def test_impressions_alone_sufficient(self) -> None:
        # 曝光达标但消耗不足 → 不进观察期，走正常规则链；
        # 消耗 < $50 时 ROAS 规则不触发（样本不足），落到 KEEP
        verdict = recommend(
            _metrics(spend=5.0, payers=0, cpp=None, roas=0.0, impressions=6000)
        )
        assert verdict.action == "KEEP"
        assert "数据不足" not in verdict.reasons[0]

    def test_observation_reason_carries_numbers(self) -> None:
        verdict = recommend(
            _metrics(spend=3.91, payers=0, cpp=None, roas=0.0, impressions=833)
        )
        assert verdict.params["impressions"] == 833
        assert "833" in verdict.reasons[0]
        assert "$10" in verdict.reasons[0]


class TestRecommendRules:
    def test_r1_no_spend_iterate(self) -> None:
        verdict = recommend(_metrics(spend=0.0, payers=0, cpp=None, roas=None))
        assert verdict.action == "ITERATE"
        assert verdict.reason_code == "no_delivery"
        assert "投放" in verdict.reasons[0]

    def test_r2_idle_and_bad_archive(self) -> None:
        verdict = recommend(_metrics(days_idle=20, cpp=150.0))
        assert verdict.action == "ARCHIVE"
        assert verdict.reason_code == "idle_underperform"

    def test_r2_idle_but_good_not_archive(self) -> None:
        verdict = recommend(_metrics(days_idle=20, cpp=50.0))
        assert verdict.action == "ITERATE"
        assert verdict.reason_code == "idle_was_healthy"
        assert "无消耗" in verdict.reasons[0]

    def test_r3_zero_payers_pause(self) -> None:
        verdict = recommend(_metrics(spend=97.0, payers=0, cpp=None))
        assert verdict.action == "PAUSE"
        assert verdict.reason_code == "zero_payers"
        assert "0 付费" in verdict.reasons[0]

    def test_r3_spend_below_threshold_not_pause(self) -> None:
        # 消耗 < $50：0 付费不暂停，ROAS 规则同样不触发（样本不足）→ KEEP
        verdict = recommend(_metrics(spend=43.0, payers=0, cpp=None, roas=0.0))
        assert verdict.action == "KEEP"
        assert verdict.reason_code == "keep_healthy"

    def test_r4_cpp_extreme_pause(self) -> None:
        verdict = recommend(_metrics(cpp=185.0))
        assert verdict.action == "PAUSE"
        assert verdict.reason_code == "cpp_over_pause_line"

    def test_r5_cpp_high_weak_roas_pause(self) -> None:
        verdict = recommend(_metrics(cpp=128.49, roas=0.007, spend=2056.0))
        assert verdict.action == "PAUSE"
        assert verdict.reason_code == "cpp_over_red_weak_roas"

    def test_r6_efficient_low_spend_iterate(self) -> None:
        verdict = recommend(_metrics(cpp=41.76, spend=752.0))
        assert verdict.action == "ITERATE"
        assert verdict.reason_code == "efficient_not_scaled"
        assert "加注" in verdict.reasons[0]

    def test_r7_cpp_above_red_iterate(self) -> None:
        verdict = recommend(_metrics(cpp=150.0, roas=0.03))
        assert verdict.action == "ITERATE"
        assert verdict.reason_code == "cpp_over_red"

    def test_r8_weak_roas_iterate(self) -> None:
        verdict = recommend(_metrics(cpp=88.38, roas=0.0167))
        assert verdict.action == "ITERATE"
        assert verdict.reason_code == "d1_roas_below_green"
        assert "Roas" in verdict.reasons[0]

    def test_r9_keep(self) -> None:
        verdict = recommend(_metrics(cpp=81.41, roas=0.03))
        assert verdict.action == "KEEP"
        assert verdict.reason_code == "keep_healthy"
        assert verdict.reasons

    def test_priority_archive_beats_pause(self) -> None:
        # idle>14 AND cpp≥180 → ARCHIVE (R2) wins over PAUSE (R4)
        verdict = recommend(_metrics(days_idle=20, cpp=200.0))
        assert verdict.action == "ARCHIVE"

    def test_reasons_contain_numbers(self) -> None:
        verdict = recommend(_metrics(cpp=41.76, spend=752.0))
        assert "41.76" in verdict.reasons[0]
        assert "752" in verdict.reasons[0]

    def test_params_carry_numbers_for_i18n(self) -> None:
        verdict = recommend(_metrics(cpp=41.76, spend=752.0))
        assert verdict.params["cpp"] == 41.76
        assert verdict.params["spend"] == 752.0


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
        assert recommend(metrics).action == "KEEP"
        config = self._config(["cpp", "d1_roas"], cpp_red_line=100.0)
        verdict = recommend(metrics, config)
        assert verdict.action == "ITERATE"
        assert "100" in verdict.reasons[0]

    def test_cpp_removed_from_judge_disables_cpp_rules(self) -> None:
        # cpp 185 默认 PAUSE；judge_metrics 去掉 cpp 后不再因成本判定
        metrics = _metrics(cpp=185.0, roas=0.03)
        assert recommend(metrics).action == "PAUSE"
        config = self._config(["d1_roas"])
        assert recommend(metrics, config).action == "KEEP"

    def test_d3_roas_weak_triggers_iterate_when_enabled(self) -> None:
        metrics = _metrics(cpp=80.0, roas=0.03, d3_roas=0.02)
        # 默认不参与判定 → KEEP
        assert recommend(metrics).action == "KEEP"
        config = self._config(["cpp", "d1_roas", "d3_roas"])
        verdict = recommend(metrics, config)
        assert verdict.action == "ITERATE"
        assert verdict.reason_code == "d3_roas_weak"
        assert "D3 Roas" in verdict.reasons[0]

    def test_d1_retention_weak_triggers_iterate_when_enabled(self) -> None:
        metrics = _metrics(cpp=80.0, roas=0.03, d1_retention=0.20)
        config = self._config(["cpp", "d1_roas", "d1_retention"])
        verdict = recommend(metrics, config)
        assert verdict.action == "ITERATE"
        assert verdict.reason_code == "d1_retention_weak"
        assert "次留" in verdict.reasons[0]

    def test_d3_roas_above_weak_keeps(self) -> None:
        metrics = _metrics(cpp=80.0, roas=0.03, d3_roas=0.08)
        config = self._config(["cpp", "d1_roas", "d3_roas"])
        assert recommend(metrics, config).action == "KEEP"


class TestPayerSufficiency:
    """样本充分性：CPP 类判定需付费 ≥3；ROAS/留存判定需消耗 ≥$50。"""

    def test_thin_payers_not_paused(self) -> None:
        # 消耗 $400 / 2 付费 → CPP $200 超暂停线，但样本太薄，不判 PAUSE
        verdict = recommend(_metrics(spend=400.0, payers=2, cpp=200.0, roas=0.03))
        assert verdict.action == "ITERATE"
        assert verdict.reason_code == "insufficient_payers"
        assert verdict.params["spend"] == 400.0
        assert verdict.params["payers"] == 2
        assert "样本太薄" in verdict.reasons[0]

    def test_three_payers_over_pause_line_still_pause(self) -> None:
        verdict = recommend(_metrics(spend=600.0, payers=3, cpp=200.0, roas=0.03))
        assert verdict.action == "PAUSE"
        assert verdict.reason_code == "cpp_over_pause_line"

    def test_thin_payers_block_cpp_over_red(self) -> None:
        verdict = recommend(_metrics(spend=300.0, payers=1, cpp=300.0, roas=0.03))
        assert verdict.action == "ITERATE"
        assert verdict.reason_code == "insufficient_payers"

    def test_low_spend_skips_roas_and_retention_rules(self) -> None:
        from app.services.settings import DEFAULT_THRESHOLDS, MetricConfig

        config = MetricConfig(
            profile=[],
            judge_metrics=["cpp", "d1_roas", "d3_roas", "d1_retention"],
            thresholds=dict(DEFAULT_THRESHOLDS),
        )
        metrics = _metrics(
            spend=40.0, payers=5, cpp=80.0,
            roas=0.0, d3_roas=0.01, d1_retention=0.10,
        )
        verdict = recommend(metrics, config)
        assert verdict.action == "KEEP"
        assert verdict.reason_code == "keep_healthy"

    def test_r0_gate_unchanged_by_thin_payers(self) -> None:
        # R0 总闸门不变：消耗/曝光双低仍进观察期，与付费人数无关
        verdict = recommend(
            _metrics(spend=3.91, payers=2, cpp=None, roas=0.0, impressions=833)
        )
        assert verdict.action == "ITERATE"
        assert verdict.reason_code == "insufficient_data"


class TestFactorExhaustion:
    """维度级裂变耗尽：≥2 维度 → ARCHIVE；恰好 1 维度 → ITERATE 换维度。"""

    def test_two_dimensions_exhausted_archive(self) -> None:
        verdict = recommend(
            _metrics(
                judged_count=4, positive_count=0, derivation_count=4,
                exhausted_factors=(("aspect-ratio", 2), ("remake", 2)),
            )
        )
        assert verdict.action == "ARCHIVE"
        assert verdict.reason_code == "derivations_exhausted"
        assert verdict.params["judged_count"] == 4
        assert verdict.params["factors"] == "aspect-ratio,remake"
        assert "耗尽" in verdict.reasons[0]

    def test_single_dimension_exhausted_iterate(self) -> None:
        verdict = recommend(
            _metrics(
                judged_count=3, positive_count=0, derivation_count=3,
                exhausted_factors=(("remake", 3),),
            )
        )
        assert verdict.action == "ITERATE"
        assert verdict.reason_code == "factor_exhausted"
        assert verdict.params["factor"] == "remake"
        assert verdict.params["judged_count"] == 3
        assert "换维度" in verdict.reasons[0]

    def test_no_exhausted_dimension_falls_through(self) -> None:
        verdict = recommend(
            _metrics(judged_count=2, positive_count=1, derivation_count=2)
        )
        assert verdict.action != "ARCHIVE"
        assert verdict.reason_code not in ("derivations_exhausted", "factor_exhausted")


class TestSupplementaryReasons:
    def test_trend_bit(self) -> None:
        bits = supplementary_reasons(_metrics(cpp=100.0, recent_cpp=140.0))
        assert [bit.code for bit in bits] == ["trend_cpp_up"]
        assert bits[0].params == {"recent_cpp": 140.0, "cpp": 100.0}
        assert "上升" in render_reason_zh(bits[0])

    def test_trend_down_bit(self) -> None:
        bits = supplementary_reasons(_metrics(cpp=100.0, recent_cpp=60.0))
        assert [bit.code for bit in bits] == ["trend_cpp_down"]
        assert "下降" in render_reason_zh(bits[0])

    def test_no_trend_within_threshold(self) -> None:
        assert supplementary_reasons(_metrics(cpp=100.0, recent_cpp=110.0)) == []

    def test_variant_and_partner_bits(self) -> None:
        bits = supplementary_reasons(
            _metrics(cpp=None, recent_cpp=None, variant_count=2,
                     observation_partners=["other-creative"])
        )
        codes = [bit.code for bit in bits]
        assert "variants_compare" in codes
        assert "observation_partner" in codes
        partner_bit = bits[codes.index("observation_partner")]
        assert partner_bit.params["partner"] == "other-creative"
        legacy = [render_reason_zh(bit) for bit in bits]
        assert any("Variant" in reason for reason in legacy)
        assert any("观察对" in reason for reason in legacy)

    def test_evolution_bits(self) -> None:
        bits = supplementary_reasons(
            _metrics(derivation_count=5, judged_count=3, positive_count=2)
        )
        codes = [bit.code for bit in bits]
        assert "derivation_healthy" in codes
        assert "derivation_pending" in codes
        healthy = bits[codes.index("derivation_healthy")]
        assert healthy.params == {"judged_count": 3, "positive_count": 2}
        pending = bits[codes.index("derivation_pending")]
        assert pending.params == {"pending": 2}


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
        metrics, verdict = report.items[0]
        assert metrics.creative_id == creative.id
        assert verdict.action in ("KEEP", "ITERATE", "PAUSE", "ARCHIVE")
        assert verdict.reason_code
        assert verdict.reasons
        # priority 已计算（金额 ≥ 0，把握在 0-1 之间）
        assert verdict.priority_dollars >= 0.0
        assert 0.0 <= verdict.confidence <= 1.0


def _seed_derivation_creative(
    db: Session, derivations: list[tuple[str, str]]
) -> Creative:
    """Seed a creative with ``len(derivations) + 1`` variants chained by
    (factor, verdict) derivation edges, plus one healthy performance row
    (spend ≥ $50, payers ≥ 3) so only the exhaustion rules can fire."""
    creative = Creative(id=str(uuid.uuid4()), name="KS_FAKE-derivation-creative")
    db.add(creative)
    variants: list[CreativeVariant] = []
    for index in range(len(derivations) + 1):
        asset = CreativeAsset(
            id=str(uuid.uuid4()),
            filename=f"KS_FAKE-260930-chain-{index}-variant-name-long-enough.mp4",
            file_type="video",
            storage_key=f"test/{uuid.uuid4()}",
        )
        db.add(asset)
        db.flush()
        variant = CreativeVariant(
            id=str(uuid.uuid4()),
            creative_id=creative.id,
            asset_id=asset.id,
            name=f"v{index}",
        )
        db.add(variant)
        variants.append(variant)
    db.flush()
    for index, (factor, verdict_value) in enumerate(derivations):
        db.add(
            VariantDerivation(
                id=str(uuid.uuid4()),
                source_variant_id=variants[index].id,
                target_variant_id=variants[index + 1].id,
                factor=factor,
                verdict=verdict_value,
            )
        )
    db.add(
        Performance(
            id=str(uuid.uuid4()),
            creative_name="ks_fake-260930-chain-0-variant-name-long-enough",
            date=date(2026, 7, 18),
            spend=100.0,
            installs=50,
            raw={"付费人数": 5, "D1_Roas": 0.05},
        )
    )
    db.flush()
    return creative


class TestBuildReportExhaustion:
    """build_report 的维度分组统计：unknown 只进总数，维度耗尽映射到规则。"""

    def test_unknown_factor_not_counted_as_dimension(
        self, db_session: Session
    ) -> None:
        creative = _seed_derivation_creative(
            db_session, [("unknown", "negative"), ("unknown", "negative")]
        )
        report = build_report(db_session, [creative])
        metrics, verdict = report.items[0]
        # unknown 进 judged/positive 总数，但不构成维度 → 不触发耗尽规则
        assert metrics.judged_count == 2
        assert metrics.positive_count == 0
        assert metrics.exhausted_factors == ()
        assert verdict.action != "ARCHIVE"
        assert verdict.reason_code != "derivations_exhausted"

    def test_single_exhausted_dimension_iterates(self, db_session: Session) -> None:
        creative = _seed_derivation_creative(
            db_session, [("remake", "negative"), ("remake", "negative")]
        )
        report = build_report(db_session, [creative])
        metrics, verdict = report.items[0]
        assert metrics.exhausted_factors == (("remake", 2),)
        assert verdict.action == "ITERATE"
        assert verdict.reason_code == "factor_exhausted"
        assert verdict.params["factor"] == "remake"
        assert verdict.params["judged_count"] == 2

    def test_two_exhausted_dimensions_archive(self, db_session: Session) -> None:
        creative = _seed_derivation_creative(
            db_session,
            [
                ("aspect-ratio", "negative"),
                ("aspect-ratio", "negative"),
                ("remake", "negative"),
                ("remake", "negative"),
            ],
        )
        report = build_report(db_session, [creative])
        _metrics_out, verdict = report.items[0]
        assert verdict.action == "ARCHIVE"
        assert verdict.reason_code == "derivations_exhausted"
        assert verdict.params["factors"] == "aspect-ratio,remake"

    def test_build_report_fills_supplementary_bits(self, db_session: Session) -> None:
        creative = _seed_derivation_creative(
            db_session, [("remake", "positive"), ("remake", "pending")]
        )
        report = build_report(db_session, [creative])
        _metrics_out, verdict = report.items[0]
        codes = [bit.code for bit in verdict.supplementary]
        assert "variants_compare" in codes  # 2 个变体可横向对比
        assert "derivation_healthy" in codes
        assert "derivation_pending" in codes
        # 旧中文 reasons 与 supplementary 一一对应（旧消费方兼容）
        assert len(verdict.reasons) == 1 + len(verdict.supplementary)
