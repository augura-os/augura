"""POST /upload 的单文件大小上限（Settings.upload_max_bytes，默认 1 GiB）。

直接调路由函数：超限在扩展名/去重检查之后、落存储之前抛出 413，
不写库也不写 MinIO。
"""
from __future__ import annotations

import io

import pytest
from fastapi import BackgroundTasks, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.routes.upload import upload_files
from app.config import Settings
from app.exceptions import ApiError
from app.models import CreativeAsset


class _FakeStorage:
    def __init__(self) -> None:
        self.writes: list[str] = []

    def put_bytes(self, key: str, *_args) -> None:  # noqa: ANN002
        self.writes.append(key)


def _upload(name: str, data: bytes) -> UploadFile:
    return UploadFile(file=io.BytesIO(data), filename=name)


def _asset_count(db: Session) -> int:
    return int(db.scalar(select(func.count()).select_from(CreativeAsset)) or 0)


def test_oversize_file_rejected_413(db_session: Session) -> None:
    settings = Settings(upload_max_bytes=8)
    storage = _FakeStorage()
    with pytest.raises(ApiError) as excinfo:
        upload_files(
            BackgroundTasks(), db_session, settings, storage,  # type: ignore[arg-type]
            [_upload("big.mp4", b"x" * 9)],
        )
    assert excinfo.value.status_code == 413
    assert "big.mp4" in excinfo.value.message
    # 超限即抛：不落存储、不写 asset 行
    assert storage.writes == []
    assert _asset_count(db_session) == 0


def test_file_at_limit_passes(db_session: Session) -> None:
    """边界：恰好等于上限的文件正常入库存储。"""
    settings = Settings(upload_max_bytes=8, upload_dir="/tmp")
    storage = _FakeStorage()
    result = upload_files(
        BackgroundTasks(), db_session, settings, storage,  # type: ignore[arg-type]
        [_upload("ok.mp4", b"x" * 8)],
    )
    assert result.success is True
    assert len(storage.writes) == 1
    assert _asset_count(db_session) == 1


def test_default_limit_is_1_gib() -> None:
    assert Settings().upload_max_bytes == 1024 * 1024 * 1024
