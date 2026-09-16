"""Tests for POST /dnas — manual DNA family creation (code server-assigned)."""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routes.dnas import DnaCreatePayload, create_dna
from app.config import Settings
from app.exceptions import ApiError
from app.models import CreativeDNA, EditLog


def _create(db: Session, name: str = "雪地求生", **fields: str):
    return create_dna(
        DnaCreatePayload(name=name, **fields),
        db=db,
        settings=Settings(),
    )


class TestCreateDna:
    def test_create_success(self, db_session: Session) -> None:
        result = _create(
            db_session,
            hook_prototype="绝境求生",
            core_mechanic="资源管理",
            narrative_structure="三幕反转",
            description="测试家族",
        )
        assert result.success is True
        assert result.data is not None
        assert result.data.code == "D01"
        assert result.data.name == "雪地求生"
        assert result.message == "已创建家族 D01 雪地求生"

        dna = db_session.scalar(
            select(CreativeDNA).where(CreativeDNA.id == result.data.id)
        )
        assert dna is not None
        assert dna.code == "D01"
        assert dna.hook_prototype == "绝境求生"
        assert dna.core_mechanic == "资源管理"
        assert dna.narrative_structure == "三幕反转"
        assert dna.description == "测试家族"
        assert dna.status == "active"

    def test_code_auto_increments(self, db_session: Session) -> None:
        first = _create(db_session, name="家族甲")
        second = _create(db_session, name="家族乙")
        assert first.data is not None and second.data is not None
        assert first.data.code == "D01"
        assert second.data.code == "D02"

    def test_empty_name_rejected(self, db_session: Session) -> None:
        with pytest.raises(ApiError) as exc_info:
            _create(db_session, name="   ")
        assert exc_info.value.status_code == 400
        assert db_session.scalars(select(CreativeDNA)).all() == []

    def test_create_audit_logged(self, db_session: Session) -> None:
        result = _create(db_session)
        assert result.data is not None
        logs = db_session.scalars(
            select(EditLog).where(
                EditLog.entity_type == "dna", EditLog.action == "create"
            )
        ).all()
        assert len(logs) == 1
        log = logs[0]
        assert log.entity_id == result.data.id
        assert log.field == "code"
        assert log.new_value == "D01 雪地求生"
