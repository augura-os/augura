"""Asset DTOs (contract §3 endpoints)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.analysis import AnalysisPayload

FileType = Literal["video", "image", "excel"]
AnalysisStatus = Literal["pending", "processing", "completed", "failed", "none"]


class AssetListItem(BaseModel):
    """Item shape of ``GET /assets`` and the ``POST /upload`` response."""

    id: str
    filename: str
    file_type: FileType
    thumbnail_url: str | None
    created_at: datetime
    analysis_status: AnalysisStatus
    creative_name: str | None
    # None when no analysis exists yet; < 0.7 means human review (§4 AI Spec).
    confidence: float | None = None
    # 所属 creative 的生命周期（active/watch/archived）；未归族素材为 None
    lifecycle_state: str | None = None


class AssetInfo(BaseModel):
    id: str
    filename: str
    file_type: FileType
    mime_type: str
    size_bytes: int
    media_url: str
    thumbnail_url: str | None
    analysis_status: AnalysisStatus
    status_message: str
    created_at: datetime


class CreativeRef(BaseModel):
    id: str
    name: str
    variant_id: str
    variant_name: str


class PerformanceOut(BaseModel):
    id: str
    creative_name: str
    date: date | None
    impressions: int
    clicks: int
    spend: float
    installs: int
    # UA priority metrics, normalized from the raw Excel row (None when the
    # export has no such column). cost_per_payer = spend / payers.
    payers: int | None = None
    cost_per_payer: float | None = None
    d1_roas: float | None = None
    d3_roas: float | None = None
    d1_retention: float | None = None
    cpi: float | None = None
    ipm: float | None = None


class AssetDetail(BaseModel):
    """Response data of ``GET /assets/{id}`` and ``PUT /assets/{id}``."""

    asset: AssetInfo
    analysis: AnalysisPayload | None
    tags: list[str] = Field(default_factory=list)
    creative: CreativeRef | None
    performance: list[PerformanceOut] = Field(default_factory=list)
    # Which engine produced the analysis ("" when none yet).
    engine_version: str | None = None


class SkippedFile(BaseModel):
    filename: str
    reason: str


class UploadResult(BaseModel):
    uploaded: list[AssetListItem] = Field(default_factory=list)
    skipped: list[SkippedFile] = Field(default_factory=list)
    # 滚动 Excel 的窗口重叠警告（新表与库内既有数据同名同日的行数）
    warnings: list[str] = Field(default_factory=list)


class AssetUpdatePayload(AnalysisPayload):
    """Body of ``PUT /assets/{id}``: all §4 fields + ``creative_name``
    (``creative_name`` is already part of the §4 schema)."""
