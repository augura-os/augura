"""Tests for periodic consolidation trigger（services/consolidation）。

扫描器与校准全部 mock，只测触发条件、计数/时间双条件与留痕。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Creative, EditLog
from app.repositories.settings import SettingsRepository
from app.services import consolidation, missed_merge_scan
from app.services.consolidation import (
    LAST_RUN_SETTING,
    NEW_SINCE_SETTING,
    maybe_consolidate,
    should_consolidate,
)
from app.services.missed_merge_scan import ScanStats


def _set_last_run(db: Session, days_ago: float) -> None:
    SettingsRepository(db).set(
        LAST_RUN_SETTING,
        (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(),
    )
    db.flush()


class TestShouldConsolidate:
    def test_never_ran(self, db_session: Session) -> None:
        assert should_consolidate(db_session) is True

    def test_counter_threshold(self, db_session: Session) -> None:
        _set_last_run(db_session, 0)
        SettingsRepository(db_session).set(NEW_SINCE_SETTING, "50")
        db_session.flush()
        assert should_consolidate(db_session) is True

    def test_time_threshold(self, db_session: Session) -> None:
        _set_last_run(db_session, 8)
        SettingsRepository(db_session).set(NEW_SINCE_SETTING, "3")
        db_session.flush()
        assert should_consolidate(db_session) is True

    def test_neither(self, db_session: Session) -> None:
        _set_last_run(db_session, 2)
        SettingsRepository(db_session).set(NEW_SINCE_SETTING, "10")
        db_session.flush()
        assert should_consolidate(db_session) is False

    def test_corrupt_timestamp_triggers(self, db_session: Session) -> None:
        SettingsRepository(db_session).set(LAST_RUN_SETTING, "not-a-date")
        db_session.flush()
        assert should_consolidate(db_session) is True


class TestMaybeConsolidate:
    def test_triggers_scan_and_logs(
        self, db_session: Session, monkeypatch
    ) -> None:
        _set_last_run(db_session, 10)
        calls: list[bool] = []

        def _fake_scan(*args, **kwargs) -> ScanStats:  # noqa: ANN002, ANN003
            calls.append(True)
            return ScanStats(recalled=2, suggested=1)

        monkeypatch.setattr(
            missed_merge_scan, "scan_missed_merges", _fake_scan
        )
        # 校准在 maybe_consolidate 里懒加载，patch 源模块
        import app.services.threshold_calibration as calib

        monkeypatch.setattr(calib, "suggest_threshold", lambda db: None)

        assert maybe_consolidate(db_session, Settings(), None) is True
        assert calls == [True]
        repo = SettingsRepository(db_session)
        assert repo.get(NEW_SINCE_SETTING) == "0"  # 触发后清零
        assert repo.get(LAST_RUN_SETTING) is not None
        log = db_session.scalars(
            select(EditLog).where(EditLog.entity_id == "consolidation")
        ).one()
        assert log.action == "auto_scan"
        assert log.new_value.startswith("auto: 周期巩固扫描")
        assert "召回 2 对" in log.new_value

    def test_skips_when_not_due(
        self, db_session: Session, monkeypatch
    ) -> None:
        _set_last_run(db_session, 1)
        SettingsRepository(db_session).set(NEW_SINCE_SETTING, "3")
        db_session.flush()
        monkeypatch.setattr(
            missed_merge_scan,
            "scan_missed_merges",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("不该扫")),
        )
        assert maybe_consolidate(db_session, Settings(), None) is False
        # 计数照常被 +1（3 → 4）
        assert SettingsRepository(db_session).get(NEW_SINCE_SETTING) == "4"

    def test_scan_failure_contained(
        self, db_session: Session, monkeypatch
    ) -> None:
        _set_last_run(db_session, 10)

        def _boom(*a, **k):  # noqa: ANN002, ANN003, ANN202
            raise RuntimeError("扫描炸了")

        monkeypatch.setattr(missed_merge_scan, "scan_missed_merges", _boom)
        assert maybe_consolidate(db_session, Settings(), None) is False


class TestPostAnalysisHook:
    def test_run_post_analysis_calls_consolidate(
        self, db_session: Session, monkeypatch
    ) -> None:
        from app.config import Settings
        from app.services import judge_pipeline

        creative = Creative(id=str(uuid.uuid4()), name="hook-check")
        db_session.add(creative)
        db_session.flush()
        calls: list[bool] = []
        monkeypatch.setattr(
            consolidation, "maybe_consolidate",
            lambda db, settings, config: calls.append(True) or False,
        )
        judge_pipeline.run_post_analysis(
            db_session, None, creative.id, settings=Settings()
        )
        assert calls == [True]


class TestBrakeSidecar:
    """刹车随行：巩固触发时携载 judge 校准（自动降级），失败不拖垮主流程。"""

    def _seed_overrides(self, db: Session) -> None:
        """30 条 auto 归族、12 条被人工改判 → 40% 超限。"""
        now = datetime.now(timezone.utc)
        for i in range(30):
            cid = str(uuid.uuid4())
            db.add(EditLog(
                id=str(uuid.uuid4()), entity_type="creative", entity_id=cid,
                action="update", field="dna_id", old_value="",
                new_value=f"auto: D1 家族（{i}）",
                created_at=now - timedelta(hours=2),
            ))
            if i < 12:
                db.add(EditLog(
                    id=str(uuid.uuid4()), entity_type="creative", entity_id=cid,
                    action="update", field="dna_id", old_value="",
                    new_value="D9 人工", created_at=now - timedelta(hours=1),
                ))
        db.flush()

    def _mock_scan(self, monkeypatch) -> None:  # noqa: ANN001
        monkeypatch.setattr(
            missed_merge_scan,
            "scan_missed_merges",
            lambda *a, **k: ScanStats(recalled=0, suggested=0),  # noqa: ANN002, ANN003
        )
        import app.services.threshold_calibration as calib

        monkeypatch.setattr(calib, "suggest_threshold", lambda db: None)

    def test_brake_rides_consolidation(
        self, db_session: Session, monkeypatch
    ) -> None:
        _set_last_run(db_session, 10)
        self._mock_scan(monkeypatch)
        self._seed_overrides(db_session)

        assert maybe_consolidate(db_session, Settings(), None) is True
        repo = SettingsRepository(db_session)
        # 类别闸与总闸（整体 40% 同样超限）都被踩下
        assert repo.get("judge_auto_enabled:dna_assign") == "false"
        assert repo.get("judge_auto_enabled") == "false"
        # 留痕：edit_logs（judge/auto_brake）+ 收件箱通知
        brake_log = db_session.scalars(
            select(EditLog).where(
                EditLog.entity_type == "judge",
                EditLog.entity_id == "judge_auto_enabled:dna_assign",
            )
        ).one()
        assert brake_log.action == "auto_brake"
        assert brake_log.new_value.startswith("auto:")

    def test_brake_failure_contained(
        self, db_session: Session, monkeypatch
    ) -> None:
        _set_last_run(db_session, 10)
        self._mock_scan(monkeypatch)
        import app.services.judge_calibration as judge_calib

        def _boom(db):  # noqa: ANN001, ANN202
            raise RuntimeError("刹车炸了")

        monkeypatch.setattr(judge_calib, "run_calibration", _boom)
        # 刹车失败：巩固主流程不受影响，自身留痕仍在
        assert maybe_consolidate(db_session, Settings(), None) is True
        log = db_session.scalars(
            select(EditLog).where(EditLog.entity_id == "consolidation")
        ).one()
        assert log.action == "auto_scan"


class TestRuleFeedbackSidecar:
    """规则层回流随行：巩固触发时携载规则词挖掘，失败不拖垮主流程。"""

    def _mock_scan(self, monkeypatch) -> None:  # noqa: ANN001
        monkeypatch.setattr(
            missed_merge_scan,
            "scan_missed_merges",
            lambda *a, **k: ScanStats(recalled=0, suggested=0),  # noqa: ANN002, ANN003
        )
        import app.services.threshold_calibration as calib

        monkeypatch.setattr(calib, "suggest_threshold", lambda db: None)

    def test_rule_feedback_rides_consolidation(
        self, db_session: Session, monkeypatch
    ) -> None:
        _set_last_run(db_session, 10)
        self._mock_scan(monkeypatch)
        import app.services.rule_feedback as feedback

        calls: list[int] = []
        monkeypatch.setattr(
            feedback, "suggest_keywords", lambda db: calls.append(1) or 0
        )
        assert maybe_consolidate(db_session, Settings(), None) is True
        assert calls == [1]

    def test_rule_feedback_failure_contained(
        self, db_session: Session, monkeypatch
    ) -> None:
        _set_last_run(db_session, 10)
        self._mock_scan(monkeypatch)
        import app.services.rule_feedback as feedback

        def _boom(db):  # noqa: ANN001, ANN202
            raise RuntimeError("挖掘炸了")

        monkeypatch.setattr(feedback, "suggest_keywords", _boom)
        # 挖掘失败：巩固主流程不受影响，自身留痕仍在
        assert maybe_consolidate(db_session, Settings(), None) is True
        log = db_session.scalars(
            select(EditLog).where(EditLog.entity_id == "consolidation")
        ).one()
        assert log.action == "auto_scan"


class TestBackfillSidecar:
    """向量回填随行（E2 设计 §4.4）：巩固触发时携载分批回填，失败不拖垮。"""

    def _mock_scan(self, monkeypatch) -> None:  # noqa: ANN001
        monkeypatch.setattr(
            missed_merge_scan,
            "scan_missed_merges",
            lambda *a, **k: ScanStats(recalled=0, suggested=0),  # noqa: ANN002, ANN003
        )
        import app.services.threshold_calibration as calib

        monkeypatch.setattr(calib, "suggest_threshold", lambda db: None)

    def test_backfill_rides_consolidation(
        self, db_session: Session, monkeypatch
    ) -> None:
        _set_last_run(db_session, 10)
        self._mock_scan(monkeypatch)
        import app.services.embedding as emb

        calls: list[int | None] = []

        def _fake(db, config, *, limit=None):  # noqa: ANN001, ANN202
            calls.append(limit)
            return emb.BackfillStats(analyses=2, remaining=3)

        monkeypatch.setattr(emb, "backfill_embeddings", _fake)
        assert maybe_consolidate(db_session, Settings(), None) is True
        # 随行回填按命名常量分批（≤ BACKFILL_BATCH_SIZE 条/轮）
        assert calls == [emb.BACKFILL_BATCH_SIZE]

    def test_backfill_failure_contained(
        self, db_session: Session, monkeypatch
    ) -> None:
        _set_last_run(db_session, 10)
        self._mock_scan(monkeypatch)
        import app.services.embedding as emb

        def _boom(db, config, *, limit=None):  # noqa: ANN001, ANN202
            raise RuntimeError("回填炸了")

        monkeypatch.setattr(emb, "backfill_embeddings", _boom)
        # 回填失败：巩固主流程不受影响，自身留痕仍在
        assert maybe_consolidate(db_session, Settings(), None) is True
        log = db_session.scalars(
            select(EditLog).where(EditLog.entity_id == "consolidation")
        ).one()
        assert log.action == "auto_scan"
