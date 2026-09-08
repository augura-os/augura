"""Pydantic v2 schemas — strict shapes matching contract §3/§4."""

from app.schemas.analysis import AnalysisPayload, AnalysisRequest
from app.schemas.asset import (
    AnalysisStatus,
    AssetDetail,
    AssetInfo,
    AssetListItem,
    AssetUpdatePayload,
    CreativeRef,
    FileType,
    PerformanceOut,
)
from app.schemas.common import Envelope, ok
from app.schemas.graph import (
    GraphEdgeOut,
    GraphEdgeType,
    GraphNodeOut,
    GraphNodeType,
    GraphOut,
    MergeRequest,
    SplitRequest,
)
from app.schemas.settings import SettingsInfo, SettingsUpdate

__all__ = [
    "AnalysisPayload",
    "AnalysisRequest",
    "AnalysisStatus",
    "AssetDetail",
    "AssetInfo",
    "AssetListItem",
    "AssetUpdatePayload",
    "CreativeRef",
    "Envelope",
    "FileType",
    "GraphEdgeOut",
    "GraphEdgeType",
    "GraphNodeOut",
    "GraphNodeType",
    "GraphOut",
    "MergeRequest",
    "PerformanceOut",
    "SettingsInfo",
    "SettingsUpdate",
    "SplitRequest",
    "ok",
]
