"""POST /analysis — synchronous (re-)run of the AI pipeline (contract §3)."""

from __future__ import annotations

import logging

from fastapi import APIRouter

from app.api.deps import DbDep
from app.api.presenters import analysis_to_payload
from app.exceptions import ApiError
from app.repositories.analysis import AnalysisRepository
from app.repositories.assets import AssetRepository
from app.schemas.analysis import AnalysisPayload, AnalysisRequest
from app.schemas.common import Envelope, ok
from app.services.pipeline import run_analysis_pipeline

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/analysis", response_model=Envelope[AnalysisPayload])
def run_analysis(
    payload: AnalysisRequest, db: DbDep
) -> Envelope[AnalysisPayload]:
    asset = AssetRepository(db).get(payload.asset_id)
    if asset is None:
        raise ApiError(404, f"素材不存在：{payload.asset_id}")
    if asset.file_type == "excel":
        raise ApiError(400, "Excel 素材不进行 AI 分析")

    run_analysis_pipeline(asset.id)

    # The pipeline commits in its own session; refresh this session's view.
    db.expire_all()
    asset = AssetRepository(db).get(payload.asset_id)
    if asset is None:
        raise ApiError(404, f"素材不存在：{payload.asset_id}")
    if asset.analysis_status == "failed":
        raise ApiError(500, asset.status_message or "分析失败")

    analysis = AnalysisRepository(db).get_by_asset(asset.id)
    if analysis is None:
        raise ApiError(500, "分析结果缺失（状态已更新但无 AnalysisResult）")
    return ok(analysis_to_payload(analysis), message="分析完成")
