"""Verdict 决策快照落库（services/verdict_snapshots + refresh 挂接）的测试。

首次 refresh 产出全量快照（字段完整、rules_version 落库）；内容不变的
二次 refresh 幂等不插行（content_hash 去重）；投放数据或阈值变化后再
refresh 产生新行；GET /creatives/recommendations 纯读不写快照。
"""
from __future__ import annotations

import json
import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AnalysisResult,
    Creative,
    CreativeAsset,
    CreativeVariant,
    Performance,
    VerdictSnapshot,
)
from app.repositories.settings import SettingsRepository
from app.repositories.verdict_snapshots import VerdictSnapshotRepository
from app.services.daily_brief import refresh_creative_states
from app.services.recommendation import CreativeMetrics, Verdict
from app.services.recommendation_rules import RULES_VERSION
from app.services.settings import METRIC_THRESHOLDS_SETTING
from app.services.verdict_snapshots import write_snapshots


def _seed_creative(db: Session) -> Creative:
    """一个带投放数据的 creative：spend 1000 / 5 付费 → cpp 200 超暂停线（180）。

    文件名茎长 ≥ MIN_PREFIX(34) 才能匹配到 Performance 行（services/matching）。
    """
    stem = "KS_EN-260101-58-制作人甲-快照测试决策快照验证素材V1-竖"
    creative = Creative(id=str(uuid.uuid4()), name="snap-one")
    asset = CreativeAsset(
        id=str(uuid.uuid4()), filename=f"{stem}.mp4", file_type="video",
        storage_key=f"test/{uuid.uuid4()}",
    )
    db.add_all([creative, asset])
    db.flush()
    db.add(
        CreativeVariant(
            id=str(uuid.uuid4()), creative_id=creative.id,
            asset_id=asset.id, name=stem,
        )
    )
    db.add(AnalysisResult(id=str(uuid.uuid4()), asset_id=asset.id, confidence=0.9))
    db.add(
        Performance(
            id=str(uuid.uuid4()),
            creative_name=stem.lower(),
            date=date(2026, 8, 1),
            spend=1000.0,
            installs=10,
            raw={"付费人数": 5, "D1_Roas": 0.0},
        )
    )
    db.flush()
    return creative


def _snapshot_count(db: Session) -> int:
    return int(db.scalar(select(func.count()).select_from(VerdictSnapshot)) or 0)


class TestVerdictSnapshots:
    def test_first_refresh_writes_full_snapshot(self, db_session: Session) -> None:
        creative = _seed_creative(db_session)
        stats = refresh_creative_states(db_session)

        assert stats["snapshots"] == 1
        snap = db_session.scalar(select(VerdictSnapshot))
        assert snap is not None
        assert snap.creative_id == creative.id
        assert snap.computed_at is not None
        # 判定输出
        assert snap.action == "PAUSE"
        assert snap.reason_code == "cpp_over_pause_line"  # cpp 200 ≥ 180 暂停线
        assert isinstance(snap.params, dict) and snap.params["cpp"] == 200.0
        assert isinstance(snap.reasons, list) and snap.reasons
        assert isinstance(snap.supplementary, list)
        assert isinstance(snap.priority_dollars, float)
        assert isinstance(snap.confidence, float)
        # 判定输入：规则版本 / 阈值 / 指标同口径落库
        # rules-v3：优化方式分口径（OBJECTIVE_PROFILES + install CPI 相对判定）；
        # 无类型行回落 aeo，阈值不变
        assert snap.rules_version == RULES_VERSION == "rules-v3"
        assert snap.thresholds["cpp_red_line"] == 120.0
        assert snap.metrics["spend"] == 1000.0
        assert snap.metrics["payers"] == 5
        # 赢家标签（rules-v2）：0 曝光触发 underexplored 短路门
        assert snap.labels == ["underexplored"]
        assert len(snap.content_hash) == 64

    def test_second_refresh_without_changes_is_idempotent(
        self, db_session: Session
    ) -> None:
        _seed_creative(db_session)
        assert refresh_creative_states(db_session)["snapshots"] == 1
        # 无变化二次刷新：指纹相同不插新行（表不膨胀）
        assert refresh_creative_states(db_session)["snapshots"] == 0
        assert _snapshot_count(db_session) == 1

    def test_data_change_produces_new_snapshot(self, db_session: Session) -> None:
        creative = _seed_creative(db_session)
        refresh_creative_states(db_session)

        row = db_session.scalar(select(Performance))
        assert row is not None
        row.spend = 2000.0
        db_session.flush()

        assert refresh_creative_states(db_session)["snapshots"] == 1
        assert _snapshot_count(db_session) == 2
        latest = VerdictSnapshotRepository(db_session).latest_for_creative(creative.id)
        assert latest is not None
        assert latest.metrics["spend"] == 2000.0
        # 历史可对比：旧行保留；内容稳定后再次幂等
        assert refresh_creative_states(db_session)["snapshots"] == 0
        assert _snapshot_count(db_session) == 2

    def test_threshold_change_produces_new_snapshot(self, db_session: Session) -> None:
        creative = _seed_creative(db_session)
        refresh_creative_states(db_session)

        # Settings 页阈值覆盖：判定输入（thresholds）变化 → 指纹变 → 新行
        SettingsRepository(db_session).set(
            METRIC_THRESHOLDS_SETTING, json.dumps({"cpp_red_line": 200.0})
        )
        db_session.flush()

        assert refresh_creative_states(db_session)["snapshots"] == 1
        assert _snapshot_count(db_session) == 2
        latest = VerdictSnapshotRepository(db_session).latest_for_creative(creative.id)
        assert latest is not None
        assert latest.thresholds["cpp_red_line"] == 200.0

    def test_get_does_not_write_snapshots(self, db_session: Session) -> None:
        from app.api.routes.creatives import creative_recommendations

        _seed_creative(db_session)
        result = creative_recommendations(db=db_session)

        assert result.success is True
        assert result.data is not None and len(result.data.items) == 1
        assert _snapshot_count(db_session) == 0

    def test_cascade_delete_removes_snapshots(self, db_session: Session) -> None:
        creative = _seed_creative(db_session)
        refresh_creative_states(db_session)
        assert _snapshot_count(db_session) == 1

        db_session.delete(creative)
        db_session.flush()
        assert _snapshot_count(db_session) == 0

    def test_labels_participate_in_content_hash(self, db_session: Session) -> None:
        """labels 变化（其余输入不变）→ 新快照行；labels 稳定 → 幂等。"""
        import dataclasses

        creative = Creative(id=str(uuid.uuid4()), name="KS_FAKE-labels-hash")
        db_session.add(creative)
        db_session.flush()
        metrics = CreativeMetrics(
            creative_id=creative.id,
            creative_name=creative.name,
            dna_code=None,
            dna_name=None,
            spend=100.0,
            payers=5,
            installs=10,
            cpp=20.0,
            roas=0.03,
            cpi=None,
            ipm=None,
            row_count=1,
            days_idle=0,
            recent_spend=100.0,
            recent_cpp=20.0,
            variant_count=1,
        )
        verdict = Verdict(
            action="KEEP", reason_code="keep_healthy", params={}, reasons=["ok"]
        )
        assert write_snapshots(db_session, [(metrics, verdict)]) == 1
        latest = VerdictSnapshotRepository(db_session).latest_for_creative(creative.id)
        assert latest is not None and latest.labels == []

        # 同样的 metrics/action，仅 labels 变化 → 指纹变 → 新行
        labeled = dataclasses.replace(verdict, labels=["proven_winner"])
        assert write_snapshots(db_session, [(metrics, labeled)]) == 1
        assert _snapshot_count(db_session) == 2
        latest = VerdictSnapshotRepository(db_session).latest_for_creative(creative.id)
        assert latest is not None and latest.labels == ["proven_winner"]

        # labels 稳定后再次幂等
        assert write_snapshots(db_session, [(metrics, labeled)]) == 0
        assert _snapshot_count(db_session) == 2
