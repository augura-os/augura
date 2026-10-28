from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Creative, EditLog, ObservationPair, SplitRuling
from app.services import observation_lifecycle, observation_ops, observation_resolution
from app.services.review import observation_pair_items


def _creative(db: Session, name: str) -> Creative:
    creative = Creative(id=str(uuid.uuid4()), name=name)
    db.add(creative)
    db.flush()
    return creative


class _Graph:
    def __init__(self, pairs: list[tuple[str, str]] | None = None) -> None:
        self.pairs = pairs or []
        self.linked: list[tuple[str, str]] = []
        self.unlinked: list[tuple[str, str]] = []

    def link_similar(self, left: str, right: str, _reason: str) -> None:
        self.linked.append((left, right))

    def unlink_similar(self, left: str, right: str) -> None:
        self.unlinked.append((left, right))


def _stub_graph(monkeypatch, graph: _Graph) -> None:
    from app.services import graph_sync

    monkeypatch.setattr(graph_sync, "get_graph_repository", lambda _settings: graph)
    monkeypatch.setattr(graph_sync, "read_similar_pairs_cached", lambda _settings: graph.pairs)


def test_observing_pair_is_not_actionable(db_session: Session, monkeypatch) -> None:
    left = _creative(db_session, "demo-left")
    right = _creative(db_session, "demo-right")
    graph = _Graph()
    _stub_graph(monkeypatch, graph)

    observation_ops.link_observation_pair_tx(
        db_session, get_settings(), left, right, "test"
    )

    assert observation_pair_items(db_session, max_date=None) == []
    waiting = observation_pair_items(db_session, max_date=None, status="observing")
    assert len(waiting) == 1
    assert waiting[0].observation_status == "observing"
    assert waiting[0].observation_waiting_reason == "awaiting_evaluation"


def test_actionable_pair_is_the_only_default_item(db_session: Session) -> None:
    left = _creative(db_session, "demo-left")
    right = _creative(db_session, "demo-right")
    pair = observation_lifecycle.get_or_create_pair(db_session, left.id, right.id)
    pair.status = observation_lifecycle.ACTIONABLE
    pair.waiting_reason = "policy_conflict"

    items = observation_pair_items(db_session, max_date=None)

    assert len(items) == 1
    assert items[0].reason_code == "observation_open"
    assert items[0].observation_status == "actionable"


def test_graph_backfill_recovers_unique_registration_log(
    db_session: Session, monkeypatch
) -> None:
    left = _creative(db_session, "demo-left")
    right = _creative(db_session, "demo-right")
    registered_at = datetime(2026, 10, 1, tzinfo=timezone.utc)
    db_session.add(
        EditLog(
            id=str(uuid.uuid4()),
            entity_type="creative",
            entity_id=right.id,
            action="update",
            field="observation_pair",
            old_value="",
            new_value=f"{left.name} ↔ {right.name} 维持拆分登记（test）",
            created_at=registered_at,
        )
    )
    _stub_graph(monkeypatch, _Graph([(left.id, right.id)]))

    result = observation_lifecycle.sync_graph_pairs(db_session, get_settings())

    assert result["added"] == 1
    pair = db_session.scalar(select(ObservationPair))
    assert pair is not None
    assert pair.registered_at == registered_at
    assert pair.evidence_start_at == registered_at.date()
    assert observation_lifecycle.sync_graph_pairs(db_session, get_settings())["added"] == 0


def test_auto_resolution_has_stable_ids_and_separate_type(
    db_session: Session, monkeypatch
) -> None:
    left = _creative(db_session, "demo-left")
    right = _creative(db_session, "demo-right")
    graph = _Graph()
    _stub_graph(monkeypatch, graph)
    observation_ops.link_observation_pair_tx(db_session, get_settings(), left, right)

    observation_resolution.close_observation_pair_tx(
        db_session,
        get_settings(),
        left,
        right,
        reason="certified test rule",
        source_kind="auto_observation",
        ruling_type="performance_split",
        evidence={"rule": "demo"},
        rule_version="test-v1",
    )

    pair = db_session.scalar(select(ObservationPair))
    ruling = db_session.scalar(select(SplitRuling))
    logs = list(
        db_session.scalars(
            select(EditLog).where(EditLog.field == "observation_pair")
        ).all()
    )
    assert pair is not None and pair.status == "resolved_split"
    assert ruling is not None
    assert ruling.source == "auto_observation"
    assert ruling.ruling_type == "performance_split"
    assert {ruling.creative_id_a, ruling.creative_id_b} == {left.id, right.id}
    assert any(log.new_value.startswith("auto:") for log in logs)
    assert graph.unlinked


def test_missing_graph_edge_becomes_actionable(db_session: Session, monkeypatch) -> None:
    left = _creative(db_session, "demo-left")
    right = _creative(db_session, "demo-right")
    pair = observation_lifecycle.get_or_create_pair(db_session, left.id, right.id)
    pair.status = observation_lifecycle.OBSERVING
    _stub_graph(monkeypatch, _Graph([]))

    result = observation_lifecycle.sync_graph_pairs(db_session, get_settings())

    assert result["missing_edges"] == 1
    assert pair.status == observation_lifecycle.ACTIONABLE
    assert pair.waiting_reason == "graph_relation_missing"
