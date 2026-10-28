"""幂等批量端点（P0a 契约 §2）：confirm-families 空族护栏与部分失败隔离、
batch-assign/unassign 三态、Idempotency-Key 重放不重复执行、末尾一次 rebuild。"""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.routes.dnas import (
    BatchAssignPayload,
    BatchUnassignPayload,
    ConfirmFamiliesPayload,
    batch_assign_dna,
    batch_unassign_dna,
    confirm_families,
)
from app.config import Settings
from app.exceptions import ApiError
from app.models import Creative, CreativeDNA, EditLog, JudgeSuggestion
from app.repositories.dna import DnaRepository
from app.services import family_bootstrap


def _make_creative(db: Session, name: str) -> Creative:
    creative = Creative(id=str(uuid.uuid4()), name=name)
    db.add(creative)
    db.flush()
    return creative


def _make_dna(db: Session, code: str = "D01", name: str = "已有族") -> CreativeDNA:
    return DnaRepository(db).create(code=code, name=name, hook_prototype="H01")


def _make_suggestion(
    db: Session,
    member_ids: list[str],
    *,
    name: str = "围栏防御",
    existing_dna_code: str | None = None,
) -> JudgeSuggestion:
    payload: dict = {
        "name": name,
        "hook_prototype": "绝境求生",
        "core_mechanic": "建栏挡敌",
        "narrative_structure": "三幕反转",
        "keywords": ["围栏", "栅栏"],
        "member_ids": member_ids,
        "note": "",
    }
    if existing_dna_code:
        payload["existing_dna_code"] = existing_dna_code
    row = JudgeSuggestion(
        id=str(uuid.uuid4()),
        kind=family_bootstrap.KIND,
        left_id=str(uuid.uuid4()),
        right_id=None,
        verdict=name,
        votes=len(member_ids),
        reason=json.dumps(payload, ensure_ascii=False),
    )
    db.add(row)
    db.flush()
    return row


def _suggestion_count(db: Session) -> int:
    return int(db.scalar(select(func.count(JudgeSuggestion.id))) or 0)


def _dna_count(db: Session) -> int:
    return int(db.scalar(select(func.count(CreativeDNA.id))) or 0)


class TestConfirmFamilies:
    def test_batch_success(self, db_session: Session) -> None:
        a, b = _make_creative(db_session, "素材甲"), _make_creative(db_session, "素材乙")
        c = _make_creative(db_session, "素材丙")
        d = _make_creative(db_session, "素材丁")
        s1 = _make_suggestion(db_session, [a.id, b.id], name="围栏防御")
        s2 = _make_suggestion(db_session, [c.id, d.id], name="反复重试")

        result = confirm_families(
            ConfirmFamiliesPayload(suggestion_ids=[s1.id, s2.id]),
            db=db_session,
            settings=Settings(),
            idempotency_key="key-success",
        )

        assert result.success is True
        assert result.data is not None
        assert result.data.summary.success == 2
        assert result.data.summary.skipped == 0
        assert result.data.summary.failed == 0
        by_id = {item.id: item for item in result.data.results}
        assert by_id[s1.id].data["assigned_count"] == 2
        assert by_id[s2.id].data["assigned_count"] == 2

        db_session.expire_all()
        dnas = DnaRepository(db_session).list_all()
        assert [d.code for d in dnas] == ["D01", "D02"]
        for creative in (a, b, c, d):
            assert creative.dna_id is not None
        assert _suggestion_count(db_session) == 0  # 提案已删

    def test_empty_family_guard(self, db_session: Session) -> None:
        # 提案生成后成员已被其他流程归族 → 空族护栏：不建族、删建议、skipped
        existing = _make_dna(db_session)
        a = _make_creative(db_session, "素材甲")
        a.dna_id = existing.id
        ghost_id = str(uuid.uuid4())  # 不存在的成员
        db_session.flush()
        s = _make_suggestion(db_session, [a.id, ghost_id])

        result = confirm_families(
            ConfirmFamiliesPayload(suggestion_ids=[s.id]),
            db=db_session,
            settings=Settings(),
            idempotency_key="key-empty",
        )

        assert result.data is not None
        item = result.data.results[0]
        assert item.status == "skipped"
        assert item.reason == "empty_family"
        assert result.data.summary.skipped == 1
        assert _dna_count(db_session) == 1  # 只有预先存在的族，没有新建空族
        assert _suggestion_count(db_session) == 0  # 建议已删

    def test_partial_failure_isolation(self, db_session: Session) -> None:
        a, b = _make_creative(db_session, "素材甲"), _make_creative(db_session, "素材乙")
        good = _make_suggestion(db_session, [a.id, b.id])
        bogus_id = str(uuid.uuid4())

        result = confirm_families(
            ConfirmFamiliesPayload(suggestion_ids=[bogus_id, good.id]),
            db=db_session,
            settings=Settings(),
            idempotency_key="key-partial",
        )

        assert result.data is not None
        by_id = {item.id: item for item in result.data.results}
        assert by_id[bogus_id].status == "failed"
        assert by_id[bogus_id].reason == "not_found"
        assert by_id[good.id].status == "success"
        assert result.data.summary.success == 1
        assert result.data.summary.failed == 1
        # 失败项不影响成功项落库
        db_session.expire_all()
        assert a.dna_id is not None and b.dna_id is not None

    def test_rebuild_once_on_success(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[str] = []
        monkeypatch.setattr(
            "app.api.routes.dnas.rebuild_mirror",
            lambda db, **kw: calls.append("rebuild"),
        )
        a, b = _make_creative(db_session, "素材甲"), _make_creative(db_session, "素材乙")
        c, d = _make_creative(db_session, "素材丙"), _make_creative(db_session, "素材丁")
        s1 = _make_suggestion(db_session, [a.id, b.id], name="族一")
        s2 = _make_suggestion(db_session, [c.id, d.id], name="族二")

        confirm_families(
            ConfirmFamiliesPayload(suggestion_ids=[s1.id, s2.id]),
            db=db_session,
            settings=Settings(),
            idempotency_key="key-rebuild",
        )
        assert calls == ["rebuild"]  # 两个成功项，末尾只 rebuild 一次

    def test_no_rebuild_when_all_skipped(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[str] = []
        monkeypatch.setattr(
            "app.api.routes.dnas.rebuild_mirror",
            lambda db, **kw: calls.append("rebuild"),
        )
        s = _make_suggestion(db_session, [str(uuid.uuid4())])  # 成员不存在 → 空族

        confirm_families(
            ConfirmFamiliesPayload(suggestion_ids=[s.id]),
            db=db_session,
            settings=Settings(),
            idempotency_key="key-norebuild",
        )
        assert calls == []

    def test_missing_idempotency_key_400(self, db_session: Session) -> None:
        with pytest.raises(ApiError) as exc_info:
            confirm_families(
                ConfirmFamiliesPayload(suggestion_ids=[]),
                db=db_session,
                settings=Settings(),
                idempotency_key=None,
            )
        assert exc_info.value.status_code == 400

    def test_replay_returns_first_result_without_rerun(
        self, db_session: Session
    ) -> None:
        a, b = _make_creative(db_session, "素材甲"), _make_creative(db_session, "素材乙")
        s = _make_suggestion(db_session, [a.id, b.id])
        payload = ConfirmFamiliesPayload(suggestion_ids=[s.id])

        first = confirm_families(
            payload, db=db_session, settings=Settings(), idempotency_key="key-replay"
        )
        dnas_after_first = _dna_count(db_session)
        logs_after_first = int(
            db_session.scalar(select(func.count(EditLog.id))) or 0
        )

        replay = confirm_families(
            payload, db=db_session, settings=Settings(), idempotency_key="key-replay"
        )

        assert replay.data is not None and first.data is not None
        assert replay.data.model_dump() == first.data.model_dump()
        assert replay.message == first.message
        # 不重复执行：家族数 / edit_log 数不变
        assert _dna_count(db_session) == dnas_after_first
        assert int(db_session.scalar(select(func.count(EditLog.id))) or 0) == (
            logs_after_first
        )

    def test_key_reused_by_other_endpoint_409(self, db_session: Session) -> None:
        dna = _make_dna(db_session)
        creative = _make_creative(db_session, "素材甲")
        batch_assign_dna(
            BatchAssignPayload(creative_ids=[creative.id], dna_id=dna.id),
            db=db_session,
            settings=Settings(),
            idempotency_key="key-shared",
        )
        with pytest.raises(ApiError) as exc_info:
            confirm_families(
                ConfirmFamiliesPayload(suggestion_ids=[]),
                db=db_session,
                settings=Settings(),
                idempotency_key="key-shared",
            )
        assert exc_info.value.status_code == 409


class TestBatchAssign:
    def test_success_skipped_failed(self, db_session: Session) -> None:
        dna = _make_dna(db_session)
        in_family = _make_creative(db_session, "已在族")
        in_family.dna_id = dna.id
        scatter = _make_creative(db_session, "散点")
        db_session.flush()
        bogus_id = str(uuid.uuid4())

        result = batch_assign_dna(
            BatchAssignPayload(
                creative_ids=[scatter.id, in_family.id, bogus_id], dna_id=dna.id
            ),
            db=db_session,
            settings=Settings(),
            idempotency_key="key-assign",
        )

        assert result.data is not None
        assert result.data.summary.success == 1
        assert result.data.summary.skipped == 1
        assert result.data.summary.failed == 1
        by_id = {item.id: item for item in result.data.results}
        assert by_id[scatter.id].status == "success"
        assert by_id[scatter.id].data["dna_id"] == dna.id
        assert by_id[in_family.id].status == "skipped"
        assert by_id[in_family.id].reason == "already_done"
        assert by_id[bogus_id].status == "failed"
        assert by_id[bogus_id].reason == "not_found"

        db_session.expire_all()
        assert scatter.dna_id == dna.id
        logs = db_session.scalars(
            select(EditLog).where(
                EditLog.entity_type == "creative", EditLog.field == "dna_id"
            )
        ).all()
        assert len(logs) == 1  # 只有真正归族的那条留痕
        assert logs[0].new_value == f"{dna.code} {dna.name}"

    def test_replay_does_not_double_assign(self, db_session: Session) -> None:
        dna = _make_dna(db_session)
        creative = _make_creative(db_session, "散点")
        payload = BatchAssignPayload(creative_ids=[creative.id], dna_id=dna.id)

        first = batch_assign_dna(
            payload, db=db_session, settings=Settings(), idempotency_key="key-assign-2"
        )
        second = batch_assign_dna(
            payload, db=db_session, settings=Settings(), idempotency_key="key-assign-2"
        )

        assert first.data is not None and second.data is not None
        # 重放返回首次结果（success），而不是按现状变成 already_done
        assert second.data.results[0].status == "success"
        assert second.data.model_dump() == first.data.model_dump()
        logs = db_session.scalars(
            select(EditLog).where(
                EditLog.entity_type == "creative", EditLog.field == "dna_id"
            )
        ).all()
        assert len(logs) == 1  # 不重复执行 → 不重复留痕

    def test_unknown_dna_404(self, db_session: Session) -> None:
        creative = _make_creative(db_session, "散点")
        with pytest.raises(ApiError) as exc_info:
            batch_assign_dna(
                BatchAssignPayload(
                    creative_ids=[creative.id], dna_id=str(uuid.uuid4())
                ),
                db=db_session,
                settings=Settings(),
                idempotency_key="key-assign-404",
            )
        assert exc_info.value.status_code == 404

    def test_archived_dna_409(self, db_session: Session) -> None:
        dna = _make_dna(db_session)
        dna.status = "archived"
        db_session.flush()
        creative = _make_creative(db_session, "散点")
        with pytest.raises(ApiError) as exc_info:
            batch_assign_dna(
                BatchAssignPayload(creative_ids=[creative.id], dna_id=dna.id),
                db=db_session,
                settings=Settings(),
                idempotency_key="key-assign-409",
            )
        assert exc_info.value.status_code == 409


class TestBatchUnassign:
    def test_success_skipped_failed(self, db_session: Session) -> None:
        dna = _make_dna(db_session)
        in_family = _make_creative(db_session, "已在族")
        in_family.dna_id = dna.id
        scatter = _make_creative(db_session, "散点")
        db_session.flush()
        bogus_id = str(uuid.uuid4())

        result = batch_unassign_dna(
            BatchUnassignPayload(creative_ids=[in_family.id, scatter.id, bogus_id]),
            db=db_session,
            settings=Settings(),
            idempotency_key="key-unassign",
        )

        assert result.data is not None
        assert result.data.summary.success == 1
        assert result.data.summary.skipped == 1
        assert result.data.summary.failed == 1
        by_id = {item.id: item for item in result.data.results}
        assert by_id[in_family.id].status == "success"
        assert by_id[scatter.id].status == "skipped"
        assert by_id[scatter.id].reason == "already_done"
        assert by_id[bogus_id].status == "failed"
        assert by_id[bogus_id].reason == "not_found"

        db_session.expire_all()
        assert in_family.dna_id is None
        logs = db_session.scalars(
            select(EditLog).where(
                EditLog.entity_type == "creative", EditLog.field == "dna_id"
            )
        ).all()
        assert len(logs) == 1
        assert logs[0].old_value == f"{dna.code} {dna.name}"
        assert logs[0].new_value == ""
