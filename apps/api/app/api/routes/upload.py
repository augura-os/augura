"""POST /upload — multipart multi-file upload (contract §3).

mp4/mov/png/jpg → MinIO + asset row (analysis_status=pending) + a durable
``analysis_jobs`` row picked up by the worker process (app.worker).
xlsx/xls → MinIO + asset row (analysis_status=none) + pandas parse into
Performance rows (synchronous).

逐文件 ingest 核心在 app.services.ingest（/lab/references 复用同一份）；
本路由只负责把 IngestOutcome 翻译成响应 DTO。
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, File, UploadFile

from app.api.deps import DbDep, SettingsDep, StorageDep
from app.api.presenters import to_asset_list_item
from app.schemas.asset import UploadResult
from app.schemas.common import Envelope, ok
from app.services.ingest import ingest_files

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/upload", response_model=Envelope[UploadResult])
def upload_files(
    db: DbDep,
    settings: SettingsDep,
    storage: StorageDep,
    files: Annotated[list[UploadFile], File(...)],
) -> Envelope[UploadResult]:
    outcome = ingest_files(db, settings, storage, files)
    message = f"已上传 {len(outcome.created)} 个文件"
    if outcome.skipped:
        message += f"，跳过 {len(outcome.skipped)} 个重复"
    return ok(
        UploadResult(
            uploaded=[to_asset_list_item(asset, None) for asset in outcome.created],
            skipped=outcome.skipped,
            warnings=outcome.warnings,
        ),
        message=message,
    )
