"""POST /graph/similar/bulk（P0b 批量登记观察对）：success / already_done /
conflict / not_found 四态、Idempotency-Key 重放不重复执行、缺 key 400、
key 跨 endpoint 409、末尾一次 rebuild_mirror（无成功项不 rebuild）。"""
from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.routes.graph import bulk_link_observation_pairs
from app.config import Settings
from app.exceptions import ApiError
from app.models import Creative, EditLog, SplitRuling
from app.schemas.graph import SimilarBulkRequest, SimilarPairIn
from app.services import graph_sync


class _FakeGraphRepo:
    """进程内 SIMILAR_TO 存根：link/read 语义同 GraphRepository（无序对）。"""

    def __init__(self) -> None:
        self.pairs: list[tuple[str, str]] = []
        self.link_calls: list[tuple[str, str]] = []

    def read_similar_pairs(self) -> list[tuple[str, str]]:
        return list(self.pairs)

    def link_similar(self, ref_a: str, ref_b: str, reason: str = "") -> str:
        pair = tuple(sorted((ref_a, ref_b)))
        if pair not in self.pairs:
            self.pairs.append(pair)
        self.link_calls.append((ref_a, ref_b))
        return "edge-id"


@pytest.fixture()
def fake_repo(monkeypatch: pytest.MonkeyPatch) -> _FakeGraphRepo:
    repo = _FakeGraphRepo()
    monkeypatch.setattr(graph_sync, "get_graph_repository", lambda _s: repo)
    return repo


def _make_creative(db: Session, name: str) -> Creative:
    creative = Creative(id=str(uuid.uuid4()), name=name)
    db.add(creative)
    db.flush()
    return creative


def _payload(*pairs: tuple[str, str]) -> SimilarBulkRequest:
    return SimilarBulkRequest(
        pairs=[
            SimilarPairIn(creative_id=a, related_creative_id=b) for a, b in pairs
        ]
    )


def _edit_log_count(db: Session) -> int:
    return int(
        db.scalar(
            select(func.count(EditLog.id)).where(EditLog.field == "observation_pair")
        )
        or 0
    )


def _run(
    db: Session, payload: SimilarBulkRequest, key: str
):  # noqa: ANN202
    return bulk_link_observation_pairs(
        payload, db=db, settings=Settings(), idempotency_key=key
    )


class TestBulkSimilar:
    def test_success(self, db_session: Session, fake_repo: _FakeGraphRepo) -> None:
        a = _make_creative(db_session, "素材甲")
        b = _make_creative(db_session, "素材乙")

        result = _run(db_session, _payload((a.id, b.id)), key="k-success")

        assert result.success is True
        assert result.data is not None
        assert result.data.summary.success == 1
        item = result.data.results[0]
        assert item.id == f"{a.id}:{b.id}"
        assert item.status == "success"
        assert fake_repo.link_calls == [(a.id, b.id)]
        assert _edit_log_count(db_session) == 1

    def test_skipped_failed_states(
        self, db_session: Session, fake_repo: _FakeGraphRepo
    ) -> None:
        a = _make_creative(db_session, "素材甲")
        b = _make_creative(db_session, "素材乙")
        c = _make_creative(db_session, "素材丙")
        d = _make_creative(db_session, "素材丁")
        fake_repo.pairs.append(tuple(sorted((a.id, b.id))))  # 已是观察对
        low, high = sorted((c.name, d.name))
        db_session.add(
            SplitRuling(
                id=str(uuid.uuid4()), name_a=low, name_b=high,
                reason="结案", source="inbox_close",
            )
        )
        db_session.flush()
        bogus = str(uuid.uuid4())

        result = _run(
            db_session,
            _payload((a.id, b.id), (c.id, d.id), (bogus, a.id)),
            key="k-states",
        )

        assert result.data is not None
        assert result.data.summary.success == 0
        assert result.data.summary.skipped == 2
        assert result.data.summary.failed == 1
        by_id = {item.id: item for item in result.data.results}
        assert by_id[f"{a.id}:{b.id}"].status == "skipped"
        assert by_id[f"{a.id}:{b.id}"].reason == "already_done"
        assert by_id[f"{c.id}:{d.id}"].status == "skipped"
        assert by_id[f"{c.id}:{d.id}"].reason == "conflict"
        assert by_id[f"{bogus}:{a.id}"].status == "failed"
        assert by_id[f"{bogus}:{a.id}"].reason == "not_found"
        # 三项都不应调 Neo4j、不留痕
        assert fake_repo.link_calls == []
        assert _edit_log_count(db_session) == 0

    def test_partial_failure_isolation(
        self, db_session: Session, fake_repo: _FakeGraphRepo
    ) -> None:
        a = _make_creative(db_session, "素材甲")
        b = _make_creative(db_session, "素材乙")
        bogus = str(uuid.uuid4())

        result = _run(
            db_session, _payload((bogus, a.id), (a.id, b.id)), key="k-partial"
        )

        assert result.data is not None
        assert result.data.summary.success == 1
        assert result.data.summary.failed == 1
        # 失败项不影响成功项登记与留痕
        assert fake_repo.link_calls == [(a.id, b.id)]
        assert _edit_log_count(db_session) == 1

    def test_self_pair_failed(
        self, db_session: Session, fake_repo: _FakeGraphRepo
    ) -> None:
        a = _make_creative(db_session, "素材甲")

        result = _run(db_session, _payload((a.id, a.id)), key="k-self")

        assert result.data is not None
        item = result.data.results[0]
        assert item.status == "failed"
        assert item.reason == "error"
        assert fake_repo.link_calls == []

    def test_duplicate_pair_in_batch_already_done(
        self, db_session: Session, fake_repo: _FakeGraphRepo
    ) -> None:
        a = _make_creative(db_session, "素材甲")
        b = _make_creative(db_session, "素材乙")

        result = _run(
            db_session, _payload((a.id, b.id), (b.id, a.id)), key="k-dup"
        )

        assert result.data is not None
        assert result.data.summary.success == 1
        assert result.data.summary.skipped == 1
        assert result.data.results[1].reason == "already_done"
        assert len(fake_repo.link_calls) == 1
        assert _edit_log_count(db_session) == 1

    def test_missing_idempotency_key_400(self, db_session: Session) -> None:
        with pytest.raises(ApiError) as exc_info:
            bulk_link_observation_pairs(
                _payload(), db=db_session, settings=Settings(), idempotency_key=None
            )
        assert exc_info.value.status_code == 400

    def test_replay_returns_first_result_without_rerun(
        self, db_session: Session, fake_repo: _FakeGraphRepo
    ) -> None:
        a = _make_creative(db_session, "素材甲")
        b = _make_creative(db_session, "素材乙")
        payload = _payload((a.id, b.id))

        first = _run(db_session, payload, key="k-replay")
        replay = _run(db_session, payload, key="k-replay")

        assert first.data is not None and replay.data is not None
        # 重放返回首次结果（success），而不是按现状变成 already_done
        assert replay.data.model_dump() == first.data.model_dump()
        assert replay.message == first.message
        # 不重复执行：Neo4j 调用与留痕都不翻倍
        assert len(fake_repo.link_calls) == 1
        assert _edit_log_count(db_session) == 1

    def test_key_reused_by_other_endpoint_409(
        self, db_session: Session, fake_repo: _FakeGraphRepo
    ) -> None:
        from app.api.routes.dnas import BatchUnassignPayload, batch_unassign_dna

        batch_unassign_dna(
            BatchUnassignPayload(creative_ids=[]),
            db=db_session,
            settings=Settings(),
            idempotency_key="k-shared",
        )
        with pytest.raises(ApiError) as exc_info:
            _run(db_session, _payload(), key="k-shared")
        assert exc_info.value.status_code == 409

    def test_rebuild_once_on_success(
        self,
        db_session: Session,
        fake_repo: _FakeGraphRepo,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls: list[str] = []
        monkeypatch.setattr(
            "app.api.routes.graph.rebuild_mirror",
            lambda db, **kw: calls.append("rebuild"),
        )
        a = _make_creative(db_session, "素材甲")
        b = _make_creative(db_session, "素材乙")
        c = _make_creative(db_session, "素材丙")
        d = _make_creative(db_session, "素材丁")

        _run(db_session, _payload((a.id, b.id), (c.id, d.id)), key="k-rebuild")

        assert calls == ["rebuild"]  # 两个成功项，末尾只 rebuild 一次

    def test_no_rebuild_when_no_success(
        self,
        db_session: Session,
        fake_repo: _FakeGraphRepo,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls: list[str] = []
        monkeypatch.setattr(
            "app.api.routes.graph.rebuild_mirror",
            lambda db, **kw: calls.append("rebuild"),
        )
        a = _make_creative(db_session, "素材甲")
        bogus = str(uuid.uuid4())

        _run(db_session, _payload((bogus, a.id)), key="k-norebuild")

        assert calls == []

    def test_over_100_pairs_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _payload(
                *[(str(uuid.uuid4()), str(uuid.uuid4())) for _ in range(101)]
            )
