"""POST /upload — multipart multi-file upload (contract §3).

mp4/mov/png/jpg → MinIO + asset row (analysis_status=pending) + a durable
``analysis_jobs`` row picked up by the worker process (app.worker).
xlsx/xls → MinIO + asset row (analysis_status=none) + pandas parse into
Performance rows (synchronous).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, File, UploadFile

from app.api.deps import DbDep, SettingsDep, StorageDep
from app.api.presenters import to_asset_list_item
from app.exceptions import ApiError
from app.models import CreativeAsset
from app.repositories.assets import AssetRepository
from app.repositories.jobs import JobRepository
from app.repositories.performance import PerformanceRepository
from app.schemas.asset import SkippedFile, UploadResult, UploadWarning
from app.schemas.common import Envelope, ok
from app.services import daily_brief
from app.services.excel import detect_overlap, parse_excel

logger = logging.getLogger(__name__)

router = APIRouter()

# extension → (file_type, fallback mime)
_ALLOWED: dict[str, tuple[str, str]] = {
    ".mp4": ("video", "video/mp4"),
    ".mov": ("video", "video/quicktime"),
    ".qt": ("video", "video/quicktime"),
    ".png": ("image", "image/png"),
    ".jpg": ("image", "image/jpeg"),
    ".jpeg": ("image", "image/jpeg"),
    ".xlsx": (
        "excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ),
    ".xls": ("excel", "application/vnd.ms-excel"),
}


@router.post("/upload", response_model=Envelope[UploadResult])
def upload_files(
    db: DbDep,
    settings: SettingsDep,
    storage: StorageDep,
    files: Annotated[list[UploadFile], File(...)],
) -> Envelope[UploadResult]:
    if not files:
        raise ApiError(400, "未接收到文件（字段名应为 files）", code="upload_no_files")

    asset_repo = AssetRepository(db)
    job_repo = JobRepository(db)
    created: list[CreativeAsset] = []
    skipped: list[SkippedFile] = []
    warnings: list[UploadWarning] = []
    imported_excel = False

    # Upload dedup: exact filename match against the library and within the
    # batch itself — duplicates are skipped, never re-analyzed or re-stored.
    existing = asset_repo.existing_filenames(
        [upload.filename or "" for upload in files]
    )
    batch_seen: set[str] = set()

    for upload in files:
        filename = upload.filename or ""
        extension = Path(filename).suffix.lower()
        if extension not in _ALLOWED:
            raise ApiError(
                400,
                f"不支持的文件类型：{filename}（支持 mp4/mov/png/jpg/xlsx/xls）",
                code="upload_unsupported_type",
                params={"filename": filename},
            )
        if filename in existing or filename in batch_seen:
            skipped.append(
                SkippedFile(
                    filename=filename,
                    reason="库中已存在同名素材",  # legacy fallback
                    code="upload_duplicate",
                )
            )
            continue
        batch_seen.add(filename)

        file_type, fallback_mime = _ALLOWED[extension]
        data = upload.file.read()
        if not data:
            raise ApiError(
                400,
                f"文件为空：{filename}",
                code="upload_empty_file",
                params={"filename": filename},
            )
        if len(data) > settings.upload_max_bytes:
            limit = settings.upload_max_bytes
            limit_text = (
                f"{limit / 1024 ** 3:g} GB" if limit >= 1024**3 else f"{limit / 1024 ** 2:g} MB"
            )
            raise ApiError(
                413,
                f"文件超过大小上限：{filename}（上限 {limit_text}）",
                code="upload_too_large",
                # 原始字节数交由前端按界面语言格式化（GB/MB）
                params={"filename": filename, "limit_bytes": limit},
            )
        mime_type = upload.content_type or fallback_mime

        # Excel is parsed before anything is persisted, so an invalid file
        # fails the request cleanly instead of leaving half-created state.
        parsed_rows = None
        tmp_path: Path | None = None
        if file_type == "excel":
            tmp_path = (
                Path(settings.upload_dir) / f"excel-{uuid4().hex}{extension}"
            )
            tmp_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path.write_bytes(data)
            try:
                parsed_rows = parse_excel(str(tmp_path))
            finally:
                tmp_path.unlink(missing_ok=True)
            overlap = detect_overlap(db, parsed_rows)
            if overlap is not None:
                files_s = "、".join(f"《{name}》" for name in overlap.source_files)
                warnings.append(
                    UploadWarning(
                        code="upload_excel_overlap",
                        # legacy fallback：旧客户端直接渲染 message
                        message=(
                            f"{filename}：与 {files_s} 在 "
                            f"{overlap.date_min}~{overlap.date_max} 窗口重叠 "
                            f"{overlap.row_count} 行，请核查清理以免重复计数"
                        ),
                        params={
                            "filename": filename,
                            "files": list(overlap.source_files),
                            "date_min": str(overlap.date_min),
                            "date_max": str(overlap.date_max),
                            "rows": overlap.row_count,
                        },
                    )
                )

        storage_key = f"{uuid4().hex}{extension}"
        storage.put_bytes(storage_key, data, mime_type)

        asset = asset_repo.create(
            filename=filename,
            file_type=file_type,
            mime_type=mime_type,
            storage_key=storage_key,
            size_bytes=len(data),
            analysis_status="pending" if file_type in ("video", "image") else "none",
        )
        db.flush()

        if file_type == "excel" and parsed_rows is not None:
            PerformanceRepository(db).bulk_create(asset.id, parsed_rows)
            imported_excel = True
        elif file_type in ("video", "image"):
            # Durable queue row, same transaction as the asset: the worker
            # process picks it up — no in-process background task that could
            # starve the API or die with it.
            job_repo.enqueue(asset.id)

        created.append(asset)

    db.commit()

    # 投放数据（Excel）导入后立即刷新 creative score 并做生命周期自动
    # 流转——数据变化时是自动流转最正确的触发点（GET /creatives/
    # recommendations 已纯读化，不再顺带流转）
    if imported_excel:
        daily_brief.refresh_creative_states(db)
        db.commit()

    message = f"已上传 {len(created)} 个文件"
    if skipped:
        message += f"，跳过 {len(skipped)} 个重复"
    return ok(
        UploadResult(
            uploaded=[to_asset_list_item(asset, None) for asset in created],
            skipped=skipped,
            warnings=warnings,
        ),
        message=message,
    )
