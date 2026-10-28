"""DNA 家族停用/启用端点（P0a 契约 §1）：成员回散点、code 不复用、
active 默认过滤、merged 409 守卫、edit_log 留痕、镜像同步。"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routes.dnas import deactivate_dna, reactivate_dna
from app.config import Settings
from app.exceptions import ApiError
from app.models import Creative, CreativeDNA, EditLog, GraphNode
from app.repositories.dna import DnaRepository
from app.services import family_ops


def _make_dna(db: Session, code: str = "D01", name: str = "围栏防御") -> CreativeDNA:
    return DnaRepository(db).create(code=code, name=name, hook_prototype="H01")


def _make_creative(db: Session, name: str) -> Creative:
    creative = Creative(id=str(uuid.uuid4()), name=name)
    db.add(creative)
    db.flush()
    return creative


class TestDeactivate:
    def test_members_scatter_and_audit_logged(self, db_session: Session) -> None:
        repo = DnaRepository(db_session)
        dna = _make_dna(db_session)
        a, b = _make_creative(db_session, "素材甲"), _make_creative(db_session, "素材乙")
        repo.assign_creative(a, dna)
        repo.assign_creative(b, dna)

        result = deactivate_dna(dna.id, db=db_session, settings=Settings())

        assert result.success is True
        assert "2 个成员已回散点" in result.message
        db_session.expire_all()
        assert dna.status == "archived"
        assert a.dna_id is None  # 成员全部回散点，不删数据
        assert b.dna_id is None

        logs = db_session.scalars(
            select(EditLog).where(
                EditLog.entity_type == "dna", EditLog.field == "status"
            )
        ).all()
        assert len(logs) == 1
        assert logs[0].entity_id == dna.id
        assert logs[0].old_value == "active"
        assert "archived" in logs[0].new_value
        assert "2 个成员" in logs[0].new_value

    def test_archived_hidden_from_default_reads(self, db_session: Session) -> None:
        dna = _make_dna(db_session)
        deactivate_dna(dna.id, db=db_session, settings=Settings())

        repo = DnaRepository(db_session)
        assert repo.list_all() == []  # 默认只出 active（契约 §1.2.3）
        assert [d.id for d in repo.list_all(active_only=False)] == [dna.id]
        # SQL 镜像的 DNA 层同样不含 archived
        dna_nodes = db_session.scalars(
            select(GraphNode).where(GraphNode.type == "dna")
        ).all()
        assert dna_nodes == []

    def test_code_never_reused(self, db_session: Session) -> None:
        dna = _make_dna(db_session, code="D01")
        deactivate_dna(dna.id, db=db_session, settings=Settings())
        # archived 家族的 code 永久保留：发号看全部状态，新族拿 D02
        new_dna = family_ops.create_dna_numbered(db_session, name="新家族")
        assert new_dna.code == "D02"

    def test_unknown_dna_404(self, db_session: Session) -> None:
        with pytest.raises(ApiError) as exc_info:
            deactivate_dna(str(uuid.uuid4()), db=db_session, settings=Settings())
        assert exc_info.value.status_code == 404

    def test_already_archived_409(self, db_session: Session) -> None:
        dna = _make_dna(db_session)
        deactivate_dna(dna.id, db=db_session, settings=Settings())
        with pytest.raises(ApiError) as exc_info:
            deactivate_dna(dna.id, db=db_session, settings=Settings())
        assert exc_info.value.status_code == 409

    def test_merged_409(self, db_session: Session) -> None:
        dna = _make_dna(db_session)
        dna.status = "merged"
        db_session.flush()
        with pytest.raises(ApiError) as exc_info:
            deactivate_dna(dna.id, db=db_session, settings=Settings())
        assert exc_info.value.status_code == 409
        assert dna.status == "merged"


class TestReactivate:
    def test_reactivate_without_reattaching(self, db_session: Session) -> None:
        repo = DnaRepository(db_session)
        dna = _make_dna(db_session)
        a = _make_creative(db_session, "素材甲")
        repo.assign_creative(a, dna)
        deactivate_dna(dna.id, db=db_session, settings=Settings())

        result = reactivate_dna(dna.id, db=db_session, settings=Settings())

        assert result.success is True
        db_session.expire_all()
        assert dna.status == "active"
        assert a.dna_id is None  # 不自动回挂：散点走正常归族流程
        assert repo.list_all()[0].id == dna.id  # 重新出现在默认查询
        logs = db_session.scalars(
            select(EditLog).where(
                EditLog.entity_type == "dna", EditLog.field == "status"
            )
        ).all()
        assert len(logs) == 2
        assert logs[1].old_value == "archived"
        assert logs[1].new_value == "active"

    def test_reactivate_active_409(self, db_session: Session) -> None:
        dna = _make_dna(db_session)
        with pytest.raises(ApiError) as exc_info:
            reactivate_dna(dna.id, db=db_session, settings=Settings())
        assert exc_info.value.status_code == 409

    def test_reactivate_merged_409(self, db_session: Session) -> None:
        dna = _make_dna(db_session)
        dna.status = "merged"
        db_session.flush()
        with pytest.raises(ApiError) as exc_info:
            reactivate_dna(dna.id, db=db_session, settings=Settings())
        assert exc_info.value.status_code == 409

    def test_unknown_dna_404(self, db_session: Session) -> None:
        with pytest.raises(ApiError) as exc_info:
            reactivate_dna(str(uuid.uuid4()), db=db_session, settings=Settings())
        assert exc_info.value.status_code == 404
