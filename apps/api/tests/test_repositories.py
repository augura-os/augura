"""Integration tests for TagRepository / PerformanceRepository (test DB)."""
from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models import CreativeAsset, Performance
from app.repositories.performance import PerformanceRepository
from app.repositories.tags import TagRepository


def _make_asset(db: Session, filename: str = "KS_EN-a.mp4") -> CreativeAsset:
    asset = CreativeAsset(
        id=str(uuid.uuid4()),
        filename=filename,
        file_type="video",
        storage_key=f"test/{uuid.uuid4()}",
    )
    db.add(asset)
    db.flush()
    return asset


class TestTagRepository:
    def test_get_or_create_idempotent(self, db_session: Session) -> None:
        repo = TagRepository(db_session)
        first = repo.get_or_create("hook-x")
        second = repo.get_or_create("hook-x")
        assert first.id == second.id
        assert first.layer is None  # new tags start unlayered

    def test_set_asset_tags_normalizes_and_replaces(self, db_session: Session) -> None:
        asset = _make_asset(db_session)
        repo = TagRepository(db_session)
        repo.set_asset_tags(asset.id, [" a ", "a", "", "b"])
        assert [tag.name for tag in repo.get_asset_tags(asset.id)] == ["a", "b"]
        repo.set_asset_tags(asset.id, ["c"])
        assert [tag.name for tag in repo.get_asset_tags(asset.id)] == ["c"]

    def test_prune_orphans(self, db_session: Session) -> None:
        asset = _make_asset(db_session)
        repo = TagRepository(db_session)
        repo.set_asset_tags(asset.id, ["doomed"])
        repo.set_asset_tags(asset.id, [])
        assert repo.get_asset_tags(asset.id) == []
        # "doomed" was pruned: re-creating yields a different id.
        assert repo.get_or_create("doomed").name == "doomed"


class TestPerformanceRepository:
    def test_list_for_creative_name_two_directional(self, db_session: Session) -> None:
        asset = _make_asset(db_session)
        stem = "ks_en-260715-58-制作人丙-模拟经营-ai-田-丛林买枪-制作人乙"
        rows = [
            # Excel dropped the -竖 suffix → match.
            Performance(asset_id=asset.id, creative_name=stem, spend=10.0, raw={}),
            # Exact name with suffix → match.
            Performance(
                asset_id=asset.id, creative_name=stem + "-竖", spend=5.0, raw={}
            ),
            # Different creative sharing the boilerplate → no match.
            Performance(
                asset_id=asset.id,
                creative_name="ks_en-260715-58-制作人丙-模拟经营-ai-田-别的素材",
                spend=99.0,
                raw={},
            ),
        ]
        db_session.add_all(rows)
        db_session.flush()

        matched = PerformanceRepository(db_session).list_for_creative_name(stem + "-竖")
        assert sorted(row.spend for row in matched) == [5.0, 10.0]
