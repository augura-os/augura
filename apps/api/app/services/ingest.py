"""上传 ingest 核心：/upload 与 /lab/references 共用的逐文件入库逻辑。

mp4/mov/png/jpg → MinIO + asset 行（analysis_status=pending）+ 同事务写
durable ``analysis_jobs`` 行（由 worker 进程消费）；xlsx/xls → MinIO +
asset 行（analysis_status=none）+ pandas 同步解析进 Performance 行。
投放数据（Excel）导入后立即刷新 creative score 并做生命周期自动流转。

路由层只负责把 :class:`IngestOutcome` 翻译成响应 DTO——行为契约（白名单、
库内+批内去重、大小校验、同事务 enqueue、commit）全部收敛在本模块。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.config import Settings
from app.exceptions import ApiError
from app.models import CreativeAsset
from app.repositories.assets import AssetRepository
from app.repositories.jobs import JobRepository
from app.repositories.performance import PerformanceRepository
from app.schemas.asset import SkippedFile, UploadWarning
from app.services import daily_brief
from app.services.excel import detect_overlap, parse_excel
from app.services.storage import StorageService

logger = logging.getLogger(__name__)

# extension → (file_type, fallback mime)
ALLOWED_EXTENSIONS: dict[str, tuple[str, str]] = {
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


@dataclass
class IngestOutcome:
    """一批文件的 ingest 结果（路由层据此组装响应）。"""

    created: list[CreativeAsset] = field(default_factory=list)
    skipped: list[SkippedFile] = field(default_factory=list)
    warnings: list[UploadWarning] = field(default_factory=list)
    imported_excel: bool = False


def ingest_files(
    db: Session,
    settings: Settings,
    storage: StorageService,
    files: list[UploadFile],
) -> IngestOutcome:
    """逐文件 ingest：校验 → 去重 → 存储 → asset + 队列（同事务 commit）。

    校验失败（类型不支持/空文件/超限）直接抛 ApiError，整批不落库；
    重复文件只进 skipped，不中断批次。
    """
    if not files:
        raise ApiError(400, "未接收到文件（字段名应为 files）", code="upload_no_files")

    asset_repo = AssetRepository(db)
    job_repo = JobRepository(db)
    outcome = IngestOutcome()

    # Upload dedup: exact filename match against the library and within the
    # batch itself — duplicates are skipped, never re-analyzed or re-stored.
    existing = asset_repo.existing_filenames(
        [upload.filename or "" for upload in files]
    )
    batch_seen: set[str] = set()

    for upload in files:
        filename = upload.filename or ""
        extension = Path(filename).suffix.lower()
        if extension not in ALLOWED_EXTENSIONS:
            raise ApiError(
                400,
                f"不支持的文件类型：{filename}（支持 mp4/mov/png/jpg/xlsx/xls）",
                code="upload_unsupported_type",
                params={"filename": filename},
            )
        if filename in existing or filename in batch_seen:
            outcome.skipped.append(
                SkippedFile(
                    filename=filename,
                    reason="库中已存在同名素材",  # legacy fallback
                    code="upload_duplicate",
                )
            )
            continue
        batch_seen.add(filename)

        file_type, fallback_mime = ALLOWED_EXTENSIONS[extension]
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
                outcome.warnings.append(
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
            outcome.imported_excel = True
        elif file_type in ("video", "image"):
            # Durable queue row, same transaction as the asset: the worker
            # process picks it up — no in-process background task that could
            # starve the API or die with it.
            job_repo.enqueue(asset.id)

        outcome.created.append(asset)

    db.commit()

    # 投放数据（Excel）导入后立即刷新 creative score 并做生命周期自动
    # 流转——数据变化时是自动流转最正确的触发点（GET /creatives/
    # recommendations 已纯读化，不再顺带流转）
    if outcome.imported_excel:
        daily_brief.refresh_creative_states(db)
        db.commit()

    return outcome
