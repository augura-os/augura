from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models import Creative, SplitRuling
from app.services import observation_lifecycle
from scripts.backtest_observation_resolution import replay_observation_pairs


def test_replay_is_aggregate_and_does_not_create_rulings(db_session: Session) -> None:
    left = Creative(id=str(uuid.uuid4()), name="demo-left")
    right = Creative(id=str(uuid.uuid4()), name="demo-right")
    db_session.add_all([left, right])
    db_session.flush()
    pair = observation_lifecycle.get_or_create_pair(db_session, left.id, right.id)
    pair.status = "observing"

    report = replay_observation_pairs(db_session)

    assert report["read_only"] is True
    assert report["policy_certified"] is False
    assert report["pairs"]["observing"] == 1
    assert report["actionable_per_100_open_pairs"] == 0.0
    assert report["certification"]["policy_id"] is None
    assert "demo-left" not in str(report)
    assert db_session.query(SplitRuling).count() == 0
