"""Creative-level routes: GET /creatives/{id}/performance,
GET /creatives/recommendations, POST /creatives/recommendations/refresh.

Aggregates the Facebook delivery rows of every asset under a creative so
the graph creative panel can compare directions (spend-weighted metrics
are computed client-side from the row list). The recommendations report
is assembled read-only by services/daily_brief; the stateful part (score
→ lifecycle auto-transition) lives in POST .../refresh and the delivery
data import path, never in a GET.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import DbDep
from app.api.presenters import performance_to_out
from app.exceptions import ApiError
from app.models import Creative
from app.repositories.creatives import CreativeRepository
from app.schemas.asset import PerformanceOut
from app.schemas.common import Envelope, ok
from app.schemas.creative import LifecycleUpdate
from app.schemas.recommendation import RecommendationReport
from app.services import daily_brief
from app.services import lifecycle as lifecycle_service
from app.services import recommendation as rec

router = APIRouter()


@router.get("/creatives/recent-ids", response_model=Envelope[list[str]])
def recent_creative_ids(db: DbDep, hours: int = 48) -> Envelope[list[str]]:
    """Creative ids created within the window (graph NEW badge, default 48h)."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    ids = db.scalars(select(Creative.id).where(Creative.created_at >= since)).all()
    return ok(list(ids))


@router.get(
    "/creatives/recommendations",
    response_model=Envelope[RecommendationReport],
)
def creative_recommendations(db: DbDep) -> Envelope[RecommendationReport]:
    """推荐报表（纯读组装，见 services/daily_brief.recommendation_report）。

    Creative Score 为展示用计算值；lifecycle_state 读 DB 当前值——
    active→watch 自动流转只走 POST .../refresh 与投放数据导入路径。
    """
    return ok(daily_brief.recommendation_report(db))


@router.post("/creatives/recommendations/refresh", response_model=Envelope[dict])
def refresh_recommendations(db: DbDep) -> Envelope[dict]:
    """有状态刷新：重算 creative score、自动流转 lifecycle_state 并落决策快照。

    active→watch 自动标记（edit_logs 逐条留痕）；verdict 判定快照按
    content_hash 去重落库（services/verdict_snapshots）；建议归档只进
    收件箱（见 services/lifecycle / review.archive_suggestion_items）。
    看板拉取前 fire 一次，保持"打开即见最新 watch 状态"的体验。
    """
    stats = daily_brief.refresh_creative_states(db)
    db.commit()
    return ok(stats, message="已刷新")


@router.get(
    "/creatives/{creative_id}/performance",
    response_model=Envelope[list[PerformanceOut]],
)
def creative_performance(
    creative_id: str, db: DbDep
) -> Envelope[list[PerformanceOut]]:
    creative = CreativeRepository(db).get(creative_id)
    if creative is None:
        raise ApiError(404, f"Creative 不存在：{creative_id}")

    rows = [
        performance_to_out(row) for row in rec.collect_creative_performance(db, creative)
    ]
    return ok(rows)


@router.put("/creatives/{creative_id}/lifecycle", response_model=Envelope[dict])
def update_lifecycle(
    creative_id: str, payload: LifecycleUpdate, db: DbDep
) -> Envelope[dict]:
    """人工生命周期操作：确认归档 / 保留观察（watch）/ 恢复（active）。

    归档不删数据，随时可恢复；每次操作 edit_logs 留痕（Human > AI）。
    """
    creative = lifecycle_service.set_lifecycle(
        db, creative_id, payload.state, reason=payload.reason
    )
    if creative is None:
        raise ApiError(404, f"Creative 不存在：{creative_id}")
    db.commit()
    return ok(
        {"creative_id": creative.id, "lifecycle_state": creative.lifecycle_state},
        message="已更新",
    )
