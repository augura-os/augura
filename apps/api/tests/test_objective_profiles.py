"""优化方式分口径判定（rules-v3，services/recommendation_rules.OBJECTIVE_PROFILES）。

- install：zero_payers 关闭；CPP/ROAS/留存类指标剔除；CPI 相对市场基准两档
  （≥2× → PAUSE，≥1.5× → ITERATE），基准缺失/不可靠/安装样本薄时不触发
- vo：CPP 红线/暂停线 ×2——高 CPP 但 ROAS 达标不再误伤
- 未知/NULL：回落 aeo = 现状（向后兼容锚点，旧数据逐字节一致）
- build_report 主类型判定：消耗最大桶喂 aggregate()，非主桶计入 mixed_spend
  并追加 mixed_objectives 补充理由
"""
from __future__ import annotations

import uuid
from datetime import date
from types import SimpleNamespace

from sqlalchemy.orm import Session

from app.models import Creative, CreativeAsset, CreativeVariant, Performance
from app.services.market_stats import MarketBaseline
from app.services.recommendation import build_report, recommend
from app.services.recommendation_rules import (
    OBJECTIVE_PROFILES,
    ReasonCode,
    objective_profile,
    profile_judge_metrics,
    profile_thresholds,
)
from app.services.settings import (
    DEFAULT_JUDGE_METRICS,
    DEFAULT_THRESHOLDS,
    MetricConfig,
)


def _metrics(**overrides: object) -> SimpleNamespace:
    base: dict[str, object] = dict(
        creative_id="c1",
        creative_name="KS_FAKE-objective",
        dna_code=None,
        dna_name=None,
        spend=100.0,
        payers=0,
        installs=100,
        cpp=None,
        roas=None,
        cpi=None,
        ipm=None,
        row_count=1,
        days_idle=None,
        impressions=10000,
        clicks=100,
        prev_spend=0.0,
        market_count=1,
        recent_spend=100.0,
        recent_cpp=None,
        variant_count=1,
        observation_partners=[],
        derivation_count=0,
        judged_count=0,
        positive_count=0,
        exhausted_factors=(),
        d3_roas=None,
        d1_retention=None,
        optimization_type="",
        mixed_spend=0.0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)  # ctr property 缺失时 getattr 安全


def _baseline(cpi_median: float | None, creative_count: int = 5) -> MarketBaseline:
    return MarketBaseline(
        code="US",
        cpp_median=None,
        roas_median=None,
        creative_count=creative_count,
        cpi_median=cpi_median,
    )


def _config_for(objective: str) -> MetricConfig:
    """build_report 内部同款组合：profile 叠加在默认配置上。"""
    return MetricConfig(
        profile=[],
        judge_metrics=profile_judge_metrics(list(DEFAULT_JUDGE_METRICS), objective),
        thresholds=profile_thresholds(dict(DEFAULT_THRESHOLDS), objective),
    )


class TestObjectiveProfiles:
    def test_unknown_objective_falls_back_to_aeo(self) -> None:
        assert objective_profile(None) is OBJECTIVE_PROFILES["aeo"]
        assert objective_profile("") is OBJECTIVE_PROFILES["aeo"]
        assert objective_profile("something-else") is OBJECTIVE_PROFILES["aeo"]

    def test_profile_thresholds_returns_copy_and_never_mutates(self) -> None:
        base = dict(DEFAULT_THRESHOLDS)
        merged = profile_thresholds(base, "vo")
        assert merged["cpp_red_line"] == 240.0
        assert merged["cpp_pause_line"] == 360.0
        assert base["cpp_red_line"] == 120.0  # 入参（市场阈值缓存）不被改动
        # 无乘数时原样返回（不复制）
        assert profile_thresholds(base, "aeo") is base
        assert profile_thresholds(base, None) is base

    def test_install_profile_drops_cpp_family(self) -> None:
        judge = profile_judge_metrics(
            ["cpp", "d1_roas", "d3_roas", "d1_retention"], "install"
        )
        assert judge == []
        # aeo / vo 不剔除任何指标
        assert profile_judge_metrics(["cpp", "d1_roas"], "aeo") == ["cpp", "d1_roas"]
        assert profile_judge_metrics(["cpp", "d1_roas"], "vo") == ["cpp", "d1_roas"]


class TestZeroPayersGate:
    def test_install_zero_payers_not_paused(self) -> None:
        # 安装优化买来少量付费是常态：spend≥50 且 0 付费不再 PAUSE
        verdict = recommend(
            _metrics(optimization_type="install", spend=200.0, payers=0, cpp=None),
            _config_for("install"),
        )
        assert verdict.action != "PAUSE"
        assert verdict.reason_code != ReasonCode.ZERO_PAYERS

    def test_aeo_and_unknown_zero_payers_still_paused(self) -> None:
        # 向后兼容锚点：aeo 与无类型（NULL）维持 rules-v2 行为
        for objective in ("aeo", ""):
            verdict = recommend(
                _metrics(optimization_type=objective, spend=200.0, payers=0, cpp=None)
            )
            assert verdict.action == "PAUSE"
            assert verdict.reason_code == ReasonCode.ZERO_PAYERS


class TestCpiMarketRules:
    def test_cpi_far_over_market_pauses(self) -> None:
        verdict = recommend(
            _metrics(
                optimization_type="install", spend=600.0, installs=100, cpi=6.0
            ),
            _config_for("install"),
            baseline=_baseline(cpi_median=3.0),
        )
        assert verdict.action == "PAUSE"
        assert verdict.reason_code == ReasonCode.CPI_FAR_OVER_MARKET
        assert verdict.params["cpi"] == 6.0
        assert verdict.params["cpi_median"] == 3.0

    def test_cpi_over_market_iterates(self) -> None:
        # 4.5 ≥ 1.5 × 3.0 但 < 2 × → ITERATE 档
        verdict = recommend(
            _metrics(
                optimization_type="install", spend=450.0, installs=100, cpi=4.5
            ),
            _config_for("install"),
            baseline=_baseline(cpi_median=3.0),
        )
        assert verdict.action == "ITERATE"
        assert verdict.reason_code == ReasonCode.CPI_OVER_MARKET

    def test_cpi_rules_skip_unreliable_baseline(self) -> None:
        # 基准样本不足（<3 创意）→ 不用不可靠基准误判
        verdict = recommend(
            _metrics(
                optimization_type="install", spend=600.0, installs=100, cpi=60.0
            ),
            _config_for("install"),
            baseline=_baseline(cpi_median=3.0, creative_count=2),
        )
        assert verdict.reason_code not in (
            ReasonCode.CPI_FAR_OVER_MARKET,
            ReasonCode.CPI_OVER_MARKET,
        )

    def test_cpi_rules_skip_missing_baseline(self) -> None:
        verdict = recommend(
            _metrics(
                optimization_type="install", spend=600.0, installs=100, cpi=60.0
            ),
            _config_for("install"),
            baseline=None,
        )
        assert verdict.reason_code not in (
            ReasonCode.CPI_FAR_OVER_MARKET,
            ReasonCode.CPI_OVER_MARKET,
        )

    def test_cpi_rules_skip_thin_installs(self) -> None:
        # 安装样本 <30：CPI 波动是倍数级，不做方向性判定
        verdict = recommend(
            _metrics(optimization_type="install", spend=600.0, installs=29, cpi=60.0),
            _config_for("install"),
            baseline=_baseline(cpi_median=3.0),
        )
        assert verdict.reason_code not in (
            ReasonCode.CPI_FAR_OVER_MARKET,
            ReasonCode.CPI_OVER_MARKET,
        )

    def test_cpi_rules_only_fire_for_install(self) -> None:
        # 同一组数字换成 aeo：CPI 规则不参与（CPP 类规则照常）
        verdict = recommend(
            _metrics(optimization_type="aeo", spend=600.0, installs=100, cpi=6.0),
            _config_for("aeo"),
            baseline=_baseline(cpi_median=3.0),
        )
        assert verdict.reason_code not in (
            ReasonCode.CPI_FAR_OVER_MARKET,
            ReasonCode.CPI_OVER_MARKET,
        )


class TestVoProfile:
    def test_cpp_over_old_red_line_with_good_roas_no_longer_flagged(self) -> None:
        # CPP $190 = 原红线 1.58×（超原暂停线 $180），ROAS 3% 达标：
        # aeo 口径 → PAUSE；vo 口径（红线/暂停线 ×2）→ KEEP
        metrics = _metrics(
            optimization_type="vo", spend=2000.0, payers=11, cpp=190.0, roas=0.03
        )
        aeo_verdict = recommend(metrics, _config_for("aeo"))
        assert aeo_verdict.action == "PAUSE"
        assert aeo_verdict.reason_code == ReasonCode.CPP_OVER_PAUSE_LINE
        vo_verdict = recommend(metrics, _config_for("vo"))
        assert vo_verdict.action == "KEEP"
        assert vo_verdict.reason_code not in (
            ReasonCode.CPP_OVER_PAUSE_LINE,
            ReasonCode.CPP_OVER_RED,
        )

    def test_cpp_over_doubled_pause_line_with_weak_roas_still_pauses(self) -> None:
        # 超加倍后暂停线且 ROAS 弱：vo 不误伤≠放水
        verdict = recommend(
            _metrics(
                optimization_type="vo", spend=2000.0, payers=5, cpp=370.0, roas=0.005
            ),
            _config_for("vo"),
        )
        assert verdict.action == "PAUSE"
        assert verdict.reason_code == ReasonCode.CPP_OVER_PAUSE_LINE


def _seed_mixed_creative(
    db: Session, *, install_spend: float, aeo_spend: float
) -> Creative:
    """同一 creative 下 install / aeo 两种优化方式各一行的混用素材。"""
    creative = Creative(id=str(uuid.uuid4()), name="KS_FAKE-mixed-objective")
    asset = CreativeAsset(
        id=str(uuid.uuid4()),
        filename="KS_FAKE-mixed-objective-variant-name-long-enough.mp4",
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
    stem = "ks_fake-mixed-objective-variant-name-long-enough"
    db.add_all(
        [
            Performance(
                id=str(uuid.uuid4()),
                creative_name=stem,
                date=date(2026, 7, 18),
                spend=install_spend,
                installs=100,
                impressions=5000,
                clicks=50,
                raw={"付费人数": 0},
                optimization_type="install",
            ),
            Performance(
                id=str(uuid.uuid4()),
                creative_name=stem,
                date=date(2026, 7, 18),
                spend=aeo_spend,
                installs=10,
                impressions=5000,
                clicks=50,
                raw={"付费人数": 3, "D1_Roas": 0.05},
                optimization_type="aeo",
            ),
        ]
    )
    db.flush()
    return creative


class TestMainObjectiveBucketing:
    """build_report 主类型判定：消耗最大桶喂判定，非主桶计入 mixed_spend。"""

    def test_install_main_bucket(self, db_session: Session) -> None:
        creative = _seed_mixed_creative(
            db_session, install_spend=300.0, aeo_spend=100.0
        )
        report = build_report(db_session, [creative])
        metrics, verdict = report.items[0]
        assert metrics.optimization_type == "install"
        assert metrics.mixed_spend == 100.0
        # 判定只喂主桶：spend/payers 只含 install 行（aeo 行的 3 付费不参与）
        assert metrics.spend == 300.0
        assert metrics.payers == 0
        # install 口径：0 付费不再 PAUSE；KS_FAKE 无市场前缀 → 基准缺失，
        # CPI 相对规则不触发 → KEEP
        assert verdict.action == "KEEP"
        assert "mixed_objectives" in [bit.code for bit in verdict.supplementary]

    def test_aeo_main_bucket(self, db_session: Session) -> None:
        creative = _seed_mixed_creative(
            db_session, install_spend=100.0, aeo_spend=300.0
        )
        report = build_report(db_session, [creative])
        metrics, verdict = report.items[0]
        assert metrics.optimization_type == "aeo"
        assert metrics.mixed_spend == 100.0
        # 主桶 = aeo 行：3 付费、CPP $100（install 行的 100 安装不参与）
        assert metrics.spend == 300.0
        assert metrics.payers == 3
        assert metrics.cpp == 100.0
        assert "mixed_objectives" in [bit.code for bit in verdict.supplementary]

    def test_null_objective_rows_behave_as_before(self, db_session: Session) -> None:
        # 向后兼容锚点：行无类型 → 主桶 = 全量，判定与分桶前逐字节一致
        creative = Creative(id=str(uuid.uuid4()), name="KS_FAKE-null-objective")
        asset = CreativeAsset(
            id=str(uuid.uuid4()),
            filename="KS_FAKE-null-objective-variant-name-long-enough.mp4",
            file_type="video",
            storage_key=f"test/{uuid.uuid4()}",
        )
        db_session.add_all([creative, asset])
        db_session.flush()
        db_session.add(
            CreativeVariant(
                id=str(uuid.uuid4()), creative_id=creative.id, asset_id=asset.id,
                name="v1",
            )
        )
        stem = "ks_fake-null-objective-variant-name-long-enough"
        for spend in (100.0, 300.0):
            db_session.add(
                Performance(
                    id=str(uuid.uuid4()),
                    creative_name=stem,
                    date=date(2026, 7, 18),
                    spend=spend,
                    installs=10,
                    impressions=5000,
                    clicks=50,
                    raw={"付费人数": 1},
                )
            )
        db_session.flush()
        report = build_report(db_session, [creative])
        metrics, verdict = report.items[0]
        assert metrics.optimization_type == ""
        assert metrics.mixed_spend == 0.0
        # 全部行参与聚合：spend 400 / 2 付费 → 薄样本 ITERATE（旧行为）
        assert metrics.spend == 400.0
        assert metrics.payers == 2
        assert verdict.reason_code == "insufficient_payers"
        assert "mixed_objectives" not in [bit.code for bit in verdict.supplementary]
