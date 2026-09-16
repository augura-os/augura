"""ORM → DTO converters for the API layer."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import cast

from sqlalchemy.orm import Session

from app.models import AnalysisResult, CreativeAsset, Performance
from app.repositories.analysis import AnalysisRepository
from app.repositories.creatives import CreativeRepository, VariantRepository
from app.repositories.performance import PerformanceRepository
from app.repositories.tags import TagRepository
from app.schemas.analysis import AnalysisPayload
from app.schemas.asset import (
    AnalysisStatus,
    AssetDetail,
    AssetInfo,
    AssetListItem,
    CreativeRef,
    FileType,
    PerformanceOut,
)
from app.services.excel import metrics_from_raw


def media_url_for(asset_id: str) -> str:
    return f"/assets/{asset_id}/media"


def thumbnail_url_for(asset: CreativeAsset) -> str | None:
    if asset.file_type == "excel":
        return None
    return f"/assets/{asset.id}/media?variant=thumb"


def to_asset_list_item(
    asset: CreativeAsset,
    creative_name: str | None,
    confidence: float | None = None,
    lifecycle_state: str | None = None,
) -> AssetListItem:
    return AssetListItem(
        id=asset.id,
        filename=asset.filename,
        file_type=cast(FileType, asset.file_type),
        thumbnail_url=thumbnail_url_for(asset),
        created_at=asset.created_at,
        analysis_status=cast(AnalysisStatus, asset.analysis_status),
        creative_name=creative_name,
        confidence=confidence,
        lifecycle_state=lifecycle_state,
    )


def analysis_to_payload(analysis: AnalysisResult) -> AnalysisPayload:
    return AnalysisPayload(
        summary=analysis.summary,
        hook=analysis.hook,
        conflict=analysis.conflict,
        gameplay=analysis.gameplay,
        reward=analysis.reward,
        characters=list(analysis.characters),
        environment=list(analysis.environment),
        emotion=list(analysis.emotion),
        tags=list(analysis.tags),
        variant_factors=list(analysis.variant_factors),
        creative_name=analysis.creative_name,
        confidence=analysis.confidence,
    )


def performance_to_out(row: Performance) -> PerformanceOut:
    metrics = metrics_from_raw(row.raw or {})
    payers = cast(int | None, metrics["payers"])
    return PerformanceOut(
        id=row.id,
        creative_name=row.creative_name,
        date=row.date,
        impressions=row.impressions,
        clicks=row.clicks,
        spend=row.spend,
        installs=row.installs,
        payers=payers,
        cost_per_payer=(row.spend / payers) if payers else None,
        d1_roas=cast(float | None, metrics["d1_roas"]),
        d3_roas=cast(float | None, metrics["d3_roas"]),
        d1_retention=cast(float | None, metrics["d1_retention"]),
        cpi=cast(float | None, metrics["cpi"]),
        ipm=cast(float | None, metrics["ipm"]),
    )


def to_asset_detail(db: Session, asset: CreativeAsset) -> AssetDetail:
    analysis = AnalysisRepository(db).get_by_asset(asset.id)
    tags = TagRepository(db).get_asset_tags(asset.id)
    variant = VariantRepository(db).get_by_asset(asset.id)
    creative = (
        CreativeRepository(db).get(variant.creative_id)
        if variant is not None
        else None
    )
    performance = (
        PerformanceRepository(db).list_for_asset(asset.id)
        if asset.file_type == "excel"
        # Video/image assets surface Facebook delivery rows whose
        # creative_name matches the file stem (incl. ad-copy suffixes).
        else PerformanceRepository(db).list_for_creative_name(
            PurePosixPath(asset.filename).stem
        )
    )

    info = AssetInfo(
        id=asset.id,
        filename=asset.filename,
        file_type=cast(FileType, asset.file_type),
        mime_type=asset.mime_type,
        size_bytes=asset.size_bytes,
        media_url=media_url_for(asset.id),
        thumbnail_url=thumbnail_url_for(asset),
        analysis_status=cast(AnalysisStatus, asset.analysis_status),
        status_message=asset.status_message,
        created_at=asset.created_at,
    )
    creative_ref = (
        CreativeRef(
            id=creative.id,
            name=creative.name,
            variant_id=variant.id,
            variant_name=variant.name,
        )
        if creative is not None and variant is not None
        else None
    )
    return AssetDetail(
        asset=info,
        analysis=analysis_to_payload(analysis) if analysis is not None else None,
        tags=[tag.name for tag in tags],
        creative=creative_ref,
        performance=[performance_to_out(row) for row in performance],
        engine_version=analysis.engine_version if analysis is not None else None,
    )
