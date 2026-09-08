"""Assets routes: list / detail / media streaming / edit / delete (§3)."""

from __future__ import annotations

import json
import logging
from typing import Iterator

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from urllib3.response import BaseHTTPResponse

from app.api.deps import DbDep, SettingsDep, StorageDep
from app.api.presenters import to_asset_detail, to_asset_list_item
from app.exceptions import ApiError
from app.models import CreativeAsset
from app.repositories.analysis import AnalysisRepository
from app.repositories.assets import AssetRepository
from app.repositories.creatives import CreativeRepository, VariantRepository
from app.repositories.edit_logs import EditLogRepository
from app.repositories.graph_mirror import rebuild_mirror
from app.repositories.performance import PerformanceRepository
from app.repositories.tags import TagRepository
from app.schemas.asset import AssetDetail, AssetListItem, AssetUpdatePayload
from app.schemas.common import Envelope, ok
from app.services import graph_sync
from app.services.analysis import AnalysisService
from app.services.media import ensure_video_contact_sheet, ensure_video_thumbnail
from app.services.pipeline import cleanup_creative_if_empty
from app.services.settings import resolve_ai_config
from app.services.storage import StorageService

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_asset_or_404(db: Session, asset_id: str) -> CreativeAsset:
    asset = AssetRepository(db).get(asset_id)
    if asset is None:
        raise ApiError(404, f"素材不存在：{asset_id}")
    return asset


# ----------------------------------------------------------------------
# List / detail
# ----------------------------------------------------------------------
@router.get("/assets", response_model=Envelope[list[AssetListItem]])
def list_assets(
    db: DbDep, search: str | None = None
) -> Envelope[list[AssetListItem]]:
    rows = AssetRepository(db).list_with_creative(search)
    return ok(
        [
            to_asset_list_item(asset, creative_name, confidence, lifecycle_state)
            for asset, creative_name, confidence, lifecycle_state in rows
        ]
    )


@router.get("/assets/{asset_id}", response_model=Envelope[AssetDetail])
def get_asset(db: DbDep, asset_id: str) -> Envelope[AssetDetail]:
    return ok(to_asset_detail(db, _get_asset_or_404(db, asset_id)))


# ----------------------------------------------------------------------
# Media streaming (video player + thumbnails)
# ----------------------------------------------------------------------
def _stream_minio(
    response: BaseHTTPResponse,
    *,
    content_type: str,
    status_code: int,
    content_length: int,
    extra_headers: dict[str, str] | None = None,
) -> StreamingResponse:
    def body() -> Iterator[bytes]:
        try:
            for chunk in response.stream(64 * 1024):
                yield chunk
        finally:
            response.close()
            response.release_conn()

    headers = {"Content-Length": str(content_length), "Accept-Ranges": "bytes"}
    if extra_headers:
        headers.update(extra_headers)
    return StreamingResponse(
        body(), status_code=status_code, media_type=content_type, headers=headers
    )


def _parse_range(header: str | None, total: int) -> tuple[int, int] | None:
    """Parse a single ``bytes=start-end`` Range header; None if invalid."""
    if not header or not header.startswith("bytes=") or total <= 0:
        return None
    spec = header[len("bytes=") :].split(",")[0].strip()
    if "-" not in spec:
        return None
    start_text, end_text = spec.split("-", 1)
    try:
        if start_text == "":
            suffix = int(end_text)
            if suffix <= 0:
                return None
            return max(total - suffix, 0), total - 1
        start = int(start_text)
        end = int(end_text) if end_text else total - 1
    except ValueError:
        return None
    end = min(end, total - 1)
    if start > end or start >= total:
        return None
    return start, end


def _stream_key(
    request: Request,
    storage: StorageService,
    *,
    key: str,
    content_type: str,
) -> Response:
    stat = storage.stat(key)
    if stat is None:
        raise ApiError(404, "文件不存在或已被删除")
    total = int(stat.size)
    byte_range = _parse_range(request.headers.get("range"), total)
    if byte_range is not None:
        start, end = byte_range
        length = end - start + 1
        response = storage.open(key, offset=start, length=length)
        return _stream_minio(
            response,
            content_type=content_type,
            status_code=206,
            content_length=length,
            extra_headers={"Content-Range": f"bytes {start}-{end}/{total}"},
        )
    response = storage.open(key)
    return _stream_minio(
        response,
        content_type=content_type,
        status_code=200,
        content_length=total,
    )


@router.get("/assets/{asset_id}/media")
def get_asset_media(
    request: Request,
    db: DbDep,
    settings: SettingsDep,
    storage: StorageDep,
    asset_id: str,
    variant: str | None = None,
) -> Response:
    asset = _get_asset_or_404(db, asset_id)
    if variant == "thumb":
        if asset.file_type == "excel":
            raise ApiError(404, "Excel 素材没有缩略图")
        if asset.file_type == "image":
            return _stream_key(
                request, storage, key=asset.storage_key, content_type=asset.mime_type
            )
        thumb_key = ensure_video_thumbnail(db, settings, storage, asset)
        return _stream_key(
            request, storage, key=thumb_key, content_type="image/jpeg"
        )
    if variant == "contact":
        if asset.file_type == "excel":
            raise ApiError(404, "Excel 素材没有关键帧矩阵")
        if asset.file_type == "image":
            return _stream_key(
                request, storage, key=asset.storage_key, content_type=asset.mime_type
            )
        contact_key = ensure_video_contact_sheet(db, settings, storage, asset)
        return _stream_key(
            request, storage, key=contact_key, content_type="image/jpeg"
        )
    return _stream_key(
        request, storage, key=asset.storage_key, content_type=asset.mime_type
    )


# ----------------------------------------------------------------------
# Human edit (§3 PUT /assets/{id})
# ----------------------------------------------------------------------
_ANALYSIS_FIELDS = (
    "summary",
    "hook",
    "conflict",
    "gameplay",
    "reward",
    "creative_name",
)
_ANALYSIS_LIST_FIELDS = ("characters", "environment", "emotion", "variant_factors")


def _log_human_edits(
    db: Session,
    asset_id: str,
    old_analysis: object,
    payload: AssetUpdatePayload,
    old_tags: list[str],
) -> None:
    """Record every human correction (AI Constitution §5 — overrides are
    learning material). ``old_analysis`` is the pre-edit AnalysisResult or
    None when the asset had none."""
    log_repo = EditLogRepository(db)

    def old_scalar(field: str) -> str:
        return str(getattr(old_analysis, field, "") or "") if old_analysis else ""

    def old_list(field: str) -> list[str]:
        if not old_analysis:
            return []
        return list(getattr(old_analysis, field, []) or [])

    for field in _ANALYSIS_FIELDS:
        old_value = old_scalar(field)
        new_value = str(getattr(payload, field) or "")
        if new_value != old_value:
            log_repo.record(
                entity_type="asset",
                entity_id=asset_id,
                action="update",
                field=field,
                old_value=old_value,
                new_value=new_value,
            )
    for field in _ANALYSIS_LIST_FIELDS:
        old_value = old_list(field)
        new_value = list(getattr(payload, field) or [])
        if new_value != old_value:
            log_repo.record(
                entity_type="asset",
                entity_id=asset_id,
                action="update",
                field=field,
                old_value=json.dumps(old_value, ensure_ascii=False),
                new_value=json.dumps(new_value, ensure_ascii=False),
            )
    new_tags = list(payload.tags or [])
    # Tags are a set: order differences (repo returns them alphabetically)
    # are not edits.
    if sorted(new_tags) != sorted(old_tags):
        log_repo.record(
            entity_type="asset",
            entity_id=asset_id,
            action="update",
            field="tags",
            old_value=json.dumps(old_tags, ensure_ascii=False),
            new_value=json.dumps(new_tags, ensure_ascii=False),
        )


@router.put("/assets/{asset_id}", response_model=Envelope[AssetDetail])
def update_asset(
    asset_id: str,
    payload: AssetUpdatePayload,
    db: DbDep,
    settings: SettingsDep,
) -> Envelope[AssetDetail]:
    asset = _get_asset_or_404(db, asset_id)
    if asset.file_type == "excel":
        raise ApiError(400, "Excel 素材没有可编辑的 AI 分析结果")

    analysis_repo = AnalysisRepository(db)
    old_analysis = analysis_repo.get_by_asset(asset.id)
    old_tags = [tag.name for tag in TagRepository(db).get_asset_tags(asset.id)]
    _log_human_edits(db, asset.id, old_analysis, payload, old_tags)

    analysis = analysis_repo.upsert(asset.id, payload)
    tags = TagRepository(db).set_asset_tags(asset.id, payload.tags)

    variant = VariantRepository(db).get_by_asset(asset.id)
    creative = (
        CreativeRepository(db).get(variant.creative_id)
        if variant is not None
        else None
    )
    if creative is not None and payload.creative_name.strip():
        creative.name = payload.creative_name.strip()

    # Re-embed summary+tags when a key is configured; saving must not fail
    # just because the key is missing or the provider has no embeddings API.
    config = resolve_ai_config(db, settings)
    if config.api_key and config.embedding_model:
        try:
            service = AnalysisService(config)
            embedding = service.embed(f"{payload.summary} {' '.join(payload.tags)}")
            AnalysisRepository(db).set_embedding(analysis, embedding)
            if variant is not None:
                variant.embedding = embedding
            if creative is not None:
                creative.representative_embedding = embedding
        except Exception as exc:  # noqa: BLE001
            logger.warning("重新计算 embedding 失败（保存继续）: %s", exc)
    if creative is not None:
        creative.representative_text = (
            f"{payload.creative_name} {' '.join(payload.tags)}"
        )

    db.commit()

    if creative is not None:
        graph_sync.sync_creative_label(settings, creative)
    graph_sync.sync_asset_tags(settings, asset, tags)
    rebuild_mirror(db)

    return ok(to_asset_detail(db, asset), message="已保存")


# ----------------------------------------------------------------------
# Delete (§3 DELETE /assets/{id})
# ----------------------------------------------------------------------
@router.delete("/assets/{asset_id}", response_model=Envelope[dict[str, str]])
def delete_asset(
    asset_id: str,
    db: DbDep,
    settings: SettingsDep,
    storage: StorageDep,
) -> Envelope[dict[str, str]]:
    asset = _get_asset_or_404(db, asset_id)

    # 1) MinIO objects (original + cached thumbnail) — best effort.
    candidate_keys = {asset.storage_key, f"{asset.storage_key}.thumb.jpg"}
    if asset.thumbnail_key:
        candidate_keys.add(asset.thumbnail_key)
    for key in candidate_keys:
        try:
            storage.remove(key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("MinIO 删除对象失败 key=%s: %s", key, exc)

    # 2) Neo4j subtree (asset → variant → empty creative).
    graph_sync.delete_asset_subgraph(settings, asset.id)

    # 3) SQL rows.
    variant = VariantRepository(db).get_by_asset(asset.id)
    creative_id = variant.creative_id if variant is not None else None
    AnalysisRepository(db).delete_for_asset(asset.id)
    tag_repo = TagRepository(db)
    tag_repo.delete_for_asset(asset.id)
    tag_repo.prune_orphans()
    PerformanceRepository(db).delete_for_asset(asset.id)
    if variant is not None:
        VariantRepository(db).delete(variant)
    AssetRepository(db).delete(asset)
    db.commit()

    if creative_id is not None:
        cleanup_creative_if_empty(db, settings, creative_id)
    rebuild_mirror(db)

    return ok({"id": asset_id}, message="已删除")
