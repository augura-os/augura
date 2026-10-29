from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.exceptions import ApiError
from app.models import Creative, EditLog, ObservationPair, SplitRuling
from app.repositories.settings import SettingsRepository
from app.services import graph_sync, observation_resolution
from app.services.judge_calibration import run_calibration


class _Graph:
    def __init__(self) -> None:
        self.linked = []
        self.unlinked = []

    def link_similar(self, left: str, right: str, _reason: str) -> None:
        self.linked.append((left, right))

    def unlink_similar(self, left: str, right: str) -> None:
        self.unlinked.append((left, right))


def _pair(db: Session) -> tuple[Creative, Creative]:
    left = Creative(id=str(uuid.uuid4()), name="demo-left")
    right = Creative(id=str(uuid.uuid4()), name="demo-right")
    db.add_all([left, right])
    db.flush()
    return left, right


def test_reopen_auto_close_disables_immediate_retry(
    db_session: Session, monkeypatch
) -> None:
    left, right = _pair(db_session)
    graph = _Graph()
    monkeypatch.setattr(graph_sync, "get_graph_repository", lambda _settings: graph)
    observation_resolution.close_observation_pair_tx(
        db_session,
        get_settings(),
        left,
        right,
        reason="synthetic auto test",
        source_kind="auto_observation",
        ruling_type="performance_split",
    )

    observation_resolution.reopen_observation_pair_tx(
        db_session, get_settings(), left, right, reason="human disagreement"
    )

    pair = db_session.scalar(select(ObservationPair))
    assert pair is not None and pair.status == "observing"
    assert pair.auto_disabled is True
    assert pair.waiting_reason == "manual_reopen"
    assert db_session.query(SplitRuling).count() == 0
    assert graph.linked


def test_manual_ruling_cannot_be_reopened_as_auto(db_session: Session, monkeypatch) -> None:
    left, right = _pair(db_session)
    monkeypatch.setattr(graph_sync, "get_graph_repository", lambda _settings: _Graph())
    observation_resolution.close_observation_pair_tx(
        db_session, get_settings(), left, right, reason="human ruling"
    )

    with pytest.raises(ApiError) as error:
        observation_resolution.reopen_observation_pair_tx(
            db_session, get_settings(), left, right, reason="retry"
        )

    assert error.value.status_code == 409
    assert db_session.query(SplitRuling).count() == 1


def test_observation_brake_uses_three_percent_and_does_not_auto_restore(
    db_session: Session,
) -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    target_ids = [str(uuid.uuid4()) for _ in range(30)]
    for index, target_id in enumerate(target_ids):
        db_session.add(
            EditLog(
                id=str(uuid.uuid4()),
                entity_type="creative",
                entity_id=target_id,
                action="update",
                field="observation_pair",
                new_value="auto: close",
                created_at=start + timedelta(seconds=index),
            )
        )
    override = EditLog(
        id=str(uuid.uuid4()),
        entity_type="creative",
        entity_id=target_ids[0],
        action="update",
        field="observation_pair",
        new_value="human reopen",
        created_at=start + timedelta(seconds=31),
    )
    db_session.add(override)
    db_session.flush()

    run_calibration(db_session)

    repo = SettingsRepository(db_session)
    key = "judge_auto_enabled:observation_pair"
    assert repo.get(key) == "false"
    assert repo.get("judge_auto_enabled") != "false"

    db_session.delete(override)
    db_session.flush()
    run_calibration(db_session)
    assert repo.get(key) == "false"
