from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Creative, ObservationEvaluation, ObservationPair, SplitRuling
from app.repositories.settings import SettingsRepository
from app.services import observation_lifecycle, observation_runner
from app.services.observation_policy import ObservationPolicyConfig, PolicyResult


def _pair(db: Session) -> tuple[Creative, Creative, ObservationPair]:
    left = Creative(id=str(uuid.uuid4()), name="demo-left")
    right = Creative(id=str(uuid.uuid4()), name="demo-right")
    db.add_all([left, right])
    db.flush()
    now = observation_lifecycle.utcnow()
    pair = observation_lifecycle.get_or_create_pair(
        db, left.id, right.id, registered_at=now
    )
    pair.next_evaluation_at = now
    return left, right, pair


class _Graph:
    def __init__(self, left: str, right: str) -> None:
        self.pair = (left, right)
        self.linked = []
        self.unlinked = []

    def read_similar_pairs(self) -> list[tuple[str, str]]:
        return [self.pair]

    def unlink_similar(self, left: str, right: str) -> None:
        self.unlinked.append((left, right))

    def link_similar(self, left: str, right: str, _reason: str) -> None:
        self.linked.append((left, right))


def _stub_graph(monkeypatch, graph: _Graph) -> None:
    from app.services import graph_sync

    monkeypatch.setattr(graph_sync, "get_graph_repository", lambda _settings: graph)


def _auto_policy(monkeypatch) -> None:
    config = ObservationPolicyConfig(
        certification_reference="synthetic-test-certification",
        certified_precision_lower_bound=0.98,
    )
    monkeypatch.setattr(observation_runner, "load_policy_config", lambda _db: config)
    monkeypatch.setattr(
        observation_runner,
        "evaluate_observation_policy",
        lambda **_kwargs: PolicyResult(
            "auto_split", "", "test-v1", {"input_hash": "abc123"}
        ),
    )


def test_default_evaluation_is_shadow_only(db_session: Session) -> None:
    _, _, pair = _pair(db_session)

    result = observation_runner.run_observation_evaluations(db_session, get_settings())

    assert result["observing"] == 1
    evaluation = db_session.scalar(select(ObservationEvaluation))
    assert evaluation is not None and evaluation.shadow is True
    assert pair.status == "observing"
    assert db_session.query(SplitRuling).count() == 0


def test_switch_off_prevents_certified_auto_close(db_session: Session, monkeypatch) -> None:
    _, _, pair = _pair(db_session)
    _auto_policy(monkeypatch)

    result = observation_runner.run_observation_evaluations(db_session, get_settings())

    assert result["auto_split"] == 1
    assert pair.status == "observing"
    assert pair.waiting_reason == "auto_close_disabled"
    assert db_session.query(SplitRuling).count() == 0


def test_certified_auto_close_writes_one_evidence_record(
    db_session: Session, monkeypatch
) -> None:
    left, right, pair = _pair(db_session)
    graph = _Graph(left.id, right.id)
    _stub_graph(monkeypatch, graph)
    _auto_policy(monkeypatch)
    SettingsRepository(db_session).set("observation_auto_close_enabled", "true")

    result = observation_runner.run_observation_evaluations(db_session, get_settings())

    assert result["closed"] == 1
    assert pair.status == "resolved_split"
    assert db_session.query(SplitRuling).count() == 1
    evaluation = db_session.scalar(select(ObservationEvaluation))
    assert evaluation is not None and evaluation.shadow is False
    assert graph.unlinked and not graph.linked


def test_commit_failure_restores_graph_and_rolls_back_ruling(
    test_db_url: str, monkeypatch
) -> None:
    from sqlalchemy import create_engine

    engine = create_engine(test_db_url)
    db = Session(engine)
    try:
        left, right, _ = _pair(db)
        graph = _Graph(left.id, right.id)
        _stub_graph(monkeypatch, graph)
        _auto_policy(monkeypatch)
        SettingsRepository(db).set("observation_auto_close_enabled", "true")

        def fail_commit() -> None:
            raise RuntimeError("synthetic commit failure")

        monkeypatch.setattr(db, "commit", fail_commit)
        result = observation_runner.run_observation_evaluations(db, get_settings())

        assert result["errors"] == 1
        assert graph.unlinked and graph.linked
        assert db.query(SplitRuling).count() == 0
        assert db.query(ObservationEvaluation).count() == 0
    finally:
        db.close()
        engine.dispose()
