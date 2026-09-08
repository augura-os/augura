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
