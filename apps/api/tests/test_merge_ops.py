"""Tests for merge_ops（POST /graph/merge 路由与 judge_pipeline 自动合并
共用的执行体）：人工/自动两条路径的守卫与 edit_logs 语义。

Neo4j 同步 patch 成 no-op（测试机的 Neo4j 不能写测试节点）。
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.exceptions import ApiError
from app.models import Creative, CreativeAsset, CreativeVariant, EditLog
from app.services import graph_sync, merge_ops
from app.services.merge_guard import GuardHit


class _FakeGraphRepository:
    def merge_creatives(self, *_args) -> None:  # noqa: ANN002
        pass


@pytest.fixture(autouse=True)
def _no_neo4j(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        graph_sync, "get_graph_repository", lambda _settings: _FakeGraphRepository()
    )


def _creative_with_variant(db: Session, name: str, filename: str) -> Creative:
    creative = Creative(id=str(uuid.uuid4()), name=name)
    asset = CreativeAsset(
        id=str(uuid.uuid4()), filename=filename, file_type="video",
        storage_key=f"test/{uuid.uuid4()}",
    )
    db.add_all([creative, asset])
    db.flush()
    db.add(
        CreativeVariant(
            id=str(uuid.uuid4()), creative_id=creative.id, asset_id=asset.id,
            name=Path(filename).stem,
        )
    )
    db.flush()
    return creative


def _pair(db: Session) -> tuple[Creative, Creative]:
    return (
        _creative_with_variant(db, "merge-ops-alpha-beta", "KS_EN-a.mp4"),
        _creative_with_variant(db, "merge-ops-alpha-beta", "KS_KR-a.mp4"),
    )


def _block_hit(_source, _target, _db=None) -> list[GuardHit]:
    return [GuardHit(level="block", check="prior_ruling", message="既定裁决")]


class TestManualMerge:
    def test_moves_variants_and_logs(self, db_session: Session) -> None:
        source, target = _pair(db_session)
        hits = merge_ops.merge_creatives(
            db_session, Settings(), source.id, target.id, auto=False,
        )
        assert hits == []
        assert db_session.get(Creative, source.id) is None
        variants = db_session.scalars(
            select(CreativeVariant).where(
                CreativeVariant.creative_id == target.id
            )
        ).all()
        assert len(variants) == 2
        log = db_session.scalars(
            select(EditLog).where(EditLog.action == "merge")
        ).one()
        assert not log.new_value.startswith("auto:")
        assert "[forced:" not in log.new_value

    def test_block_requires_force_reason(
        self, db_session: Session, monkeypatch
    ) -> None:
        source, target = _pair(db_session)
        monkeypatch.setattr(merge_ops, "check_merge", _block_hit)
        with pytest.raises(ApiError):
            merge_ops.merge_creatives(
                db_session, Settings(), source.id, target.id, auto=False,
            )
        # 未执行：双方都在，无合并日志
        assert db_session.get(Creative, source.id) is not None
        assert db_session.get(Creative, target.id) is not None
        assert db_session.scalars(
            select(EditLog).where(EditLog.action == "merge")
        ).all() == []

    def test_block_with_force_reason_merges(
        self, db_session: Session, monkeypatch
    ) -> None:
        source, target = _pair(db_session)
        monkeypatch.setattr(merge_ops, "check_merge", _block_hit)
        merge_ops.merge_creatives(
            db_session, Settings(), source.id, target.id,
            auto=False, force_reason="人工确认同创意",
        )
        assert db_session.get(Creative, source.id) is None
        log = db_session.scalars(
            select(EditLog).where(EditLog.action == "merge")
        ).one()
        assert "[forced: 人工确认同创意]" in log.new_value


class TestAutoMerge:
    def test_block_never_executes(self, db_session: Session, monkeypatch) -> None:
        source, target = _pair(db_session)
        monkeypatch.setattr(merge_ops, "check_merge", _block_hit)
        with pytest.raises(merge_ops.MergeBlocked):
            merge_ops.merge_creatives(
                db_session, Settings(), source.id, target.id, auto=True,
            )
        assert db_session.get(Creative, source.id) is not None
        assert db_session.get(Creative, target.id) is not None

    def test_auto_log_prefix(self, db_session: Session) -> None:
        source, target = _pair(db_session)
        merge_ops.merge_creatives(
            db_session, Settings(), source.id, target.id, auto=True,
        )
        log = db_session.scalars(
            select(EditLog).where(EditLog.action == "merge")
        ).one()
        assert log.new_value.startswith("auto:")

    def test_same_id_rejected(self, db_session: Session) -> None:
        source, _target = _pair(db_session)
        with pytest.raises(ApiError):
            merge_ops.merge_creatives(
                db_session, Settings(), source.id, source.id, auto=False,
            )
