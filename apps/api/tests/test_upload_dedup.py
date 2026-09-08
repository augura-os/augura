"""Tests for upload dedup (AssetRepository.existing_filenames)."""
from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models import CreativeAsset
from app.repositories.assets import AssetRepository


def _seed_asset(db: Session, filename: str) -> None:
    db.add(
        CreativeAsset(
            id=str(uuid.uuid4()),
            filename=filename,
            file_type="video",
            storage_key=f"test/{uuid.uuid4()}",
        )
    )
    db.flush()


def test_existing_filenames(db_session: Session) -> None:
    _seed_asset(db_session, "KS_EN-260701-a-竖.mp4")
    _seed_asset(db_session, "KS_EN-260702-b-竖.mp4")
    repo = AssetRepository(db_session)
    result = repo.existing_filenames(
        ["KS_EN-260701-a-竖.mp4", "KS_EN-260703-c-竖.mp4", "KS_EN-260702-b-竖.mp4"]
    )
    assert result == {"KS_EN-260701-a-竖.mp4", "KS_EN-260702-b-竖.mp4"}


def test_existing_filenames_empty_input(db_session: Session) -> None:
    assert AssetRepository(db_session).existing_filenames([]) == set()


def test_existing_filenames_substring_no_match(db_session: Session) -> None:
    _seed_asset(db_session, "KS_EN-260701-a-竖.mp4")
    repo = AssetRepository(db_session)
    # 精确匹配：子串/前缀不算重复
    assert repo.existing_filenames(["KS_EN-260701-a.mp4"]) == set()
