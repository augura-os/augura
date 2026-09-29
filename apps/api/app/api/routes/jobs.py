"""POST /jobs/{asset_id}/retry — 把终结态分析任务重置回 queued。

failed / dead / waiting_user / cancelled 才可重试；queued / running / done
一律 409。worker（app.worker）轮询到 queued 行后自动重跑。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

from app.api.deps import DbDep
from app.exceptions import ApiError
from app.models.job import JOB_RETRYABLE_STATUSES
from app.repositories.assets import AssetRepository
from app.repositories.jobs import JobRepository, utcnow
from app.schemas.common import Envelope, ok
from app.schemas.job import JobInfo

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/jobs/{asset_id}/retry", response_model=Envelope[JobInfo])
def retry_analysis_job(asset_id: str, db: DbDep) -> Envelope[JobInfo]:
    asset = AssetRepository(db).get(asset_id)
    if asset is None:
        raise ApiError(404, f"素材不存在：{asset_id}")
    repo = JobRepository(db)
    job = repo.get_by_asset(asset_id)
    if job is None:
        raise ApiError(404, "该素材没有分析任务（Excel 不进入分析队列）")
    if job.status not in JOB_RETRYABLE_STATUSES:
        raise ApiError(409, f"当前任务状态不允许重试：{job.status}")

    job.status = "queued"
    job.attempt = 0
    job.stage = None
    job.error_code = None
    job.error_message = None
    job.lease_until = None
    job.worker_id = None
    job.available_at = utcnow()
    AssetRepository(db).set_status(asset, "pending", "")
    db.commit()
    logger.info("分析任务重试 asset=%s job=%s", asset_id, job.id)
    return ok(
        JobInfo(
            id=job.id,
            asset_id=job.asset_id,
            status=job.status,
            attempt=job.attempt,
            stage=job.stage,
            error_code=job.error_code,
            error_message=job.error_message,
            available_at=job.available_at,
        ),
        message="已重新排队，worker 将自动重试",
    )
