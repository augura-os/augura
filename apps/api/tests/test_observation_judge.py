"""Tests for observation-pair pre-adjudication (services/observation_judge)."""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy.orm import Session

from app.models import Creative, CreativeAsset, CreativeVariant, Performance
from app.services.observation_judge import judge_observation_pair
from app.services.settings import AIConfig


class _FakeConfig(AIConfig):
    def __init__(self, *, with_key: bool = True) -> None:
        super().__init__(
            api_key="sk-fake" if with_key else "",
            base_url="",
            vision_model="",
            embedding_model="",
        )


def _seed_pair(db: Session) -> tuple[Creative, Creative]:
    creatives = []
    for name, spend, payers in (("obs-left", 800.0, 10), ("obs-right", 300.0, 2)):
        creative = Creative(id=str(uuid.uuid4()), name=name)
        asset = CreativeAsset(
            id=str(uuid.uuid4()), filename=f"KS_EN-{name}-tail-segment-竖.mp4",
            file_type="video", storage_key=f"test/{uuid.uuid4()}",
        )
        db.add_all([creative, asset])
        db.flush()
        db.add(
            CreativeVariant(
                id=str(uuid.uuid4()), creative_id=creative.id,
                asset_id=asset.id, name="v1",
            )
        )
        db.add(
            Performance(
                id=str(uuid.uuid4()),
                creative_name=f"ks_en-{name}-tail-segment",
                date=date(2026, 7, 20),
                spend=spend,
                installs=100,
                raw={"付费人数": payers, "D1_Roas": 0.02},
            )
        )
        creatives.append(creative)
    db.flush()
    return creatives[0], creatives[1]


class TestJudgeObservationPair:
    def test_no_api_key_returns_none(self, db_session: Session) -> None:
        source, target = _seed_pair(db_session)
        assert (
            judge_observation_pair(
                db_session, _FakeConfig(with_key=False), source, target,
                max_date=date(2026, 7, 21),
            )
            is None
        )

    def test_majority_verdict_returned(
        self, db_session: Session, monkeypatch
    ) -> None:
        source, target = _seed_pair(db_session)
        monkeypatch.setattr(
            "app.services.observation_judge.vote_json",
            lambda config, *, system, user, key, n: ("split", 2, ["成本差 67%"]),
        )
        judgement = judge_observation_pair(
            db_session, _FakeConfig(), source, target, max_date=date(2026, 7, 21)
        )
        assert judgement is not None
        assert judgement.verdict == "split"
        assert judgement.votes == 2
        assert judgement.reason

    def test_low_votes_or_invalid_verdict_returns_none(
        self, db_session: Session, monkeypatch
    ) -> None:
        source, target = _seed_pair(db_session)
        monkeypatch.setattr(
            "app.services.observation_judge.vote_json",
            lambda config, *, system, user, key, n: ("merge", 1, []),
        )
        assert (
            judge_observation_pair(
                db_session, _FakeConfig(), source, target,
                max_date=date(2026, 7, 21),
            )
            is None
        )
        monkeypatch.setattr(
            "app.services.observation_judge.vote_json",
            lambda config, *, system, user, key, n: ("explode", 3, []),
        )
        assert (
            judge_observation_pair(
                db_session, _FakeConfig(), source, target,
                max_date=date(2026, 7, 21),
            )
            is None
        )
