"""Tests for scripts/migrate_market_codes（settings 市场标识归一为市场码）。"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import EditLog
from app.repositories.settings import SettingsRepository

# 迁移脚本只在私有库，未随公开仓库发布；缺失时跳过本文件而不是让整个 CI 红掉
migrate_market_codes = pytest.importorskip(
    "scripts.migrate_market_codes", reason="scripts 未随公开仓库发布"
).migrate_market_codes


def _seed(db: Session) -> None:
    repo = SettingsRepository(db)
    repo.set("market_prefixes", "KS_PT,KS_ES")
    repo.set(
        "market_thresholds",
        json.dumps({"KS_PT": {"cpp_red_line": 80}, "PT": {"cpp_efficient": 50}}),
    )
    repo.set(
        "market_tag_map",
        json.dumps({"brazil-pt": "KS_PT", "spanish-latam": "KS_ES"}),
    )
    db.flush()


class TestMigrateMarketCodes:
    def test_migrates_keys_and_values(self, db_session: Session) -> None:
        _seed(db_session)
        changes = migrate_market_codes(db_session, dry_run=False)
        assert "market_thresholds 键 KS_PT → PT" in changes
        assert "market_tag_map 值 brazil-pt: KS_PT → PT" in changes
        repo = SettingsRepository(db_session)
        thresholds = json.loads(repo.get("market_thresholds"))
        # KS_PT → PT 与已有 PT 键按条目合并（码键优先，两边条目都保留）
        assert thresholds == {"PT": {"cpp_efficient": 50, "cpp_red_line": 80}}
        tag_map = json.loads(repo.get("market_tag_map"))
        assert tag_map == {"brazil-pt": "PT", "spanish-latam": "ES"}
        # edit_logs 留痕
        logs = db_session.scalars(
            select(EditLog).where(EditLog.entity_type == "settings")
        ).all()
        assert len(logs) == 2

    def test_idempotent(self, db_session: Session) -> None:
        _seed(db_session)
        migrate_market_codes(db_session, dry_run=False)
        assert migrate_market_codes(db_session, dry_run=False) == []

    def test_dry_run_writes_nothing(self, db_session: Session) -> None:
        _seed(db_session)
        changes = migrate_market_codes(db_session, dry_run=True)
        assert changes  # 报告了计划变更
        repo = SettingsRepository(db_session)
        # 但库里原样
        assert "KS_PT" in json.loads(repo.get("market_thresholds"))
        assert json.loads(repo.get("market_tag_map"))["brazil-pt"] == "KS_PT"
        assert db_session.scalars(
            select(EditLog).where(EditLog.entity_type == "settings")
        ).all() == []

    def test_empty_settings_noop(self, db_session: Session) -> None:
        assert migrate_market_codes(db_session, dry_run=False) == []
