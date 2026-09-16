"""Creative-level routes: GET /creatives/{id}/performance,
GET /creatives/recommendations.

Aggregates the Facebook delivery rows of every asset under a creative so
the graph creative panel can compare directions (spend-weighted metrics
are computed client-side from the row list). The recommendations endpoint
runs the rule-based engine (services/recommendation) over every creative.
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
from app.schemas.recommendation import (
    MetricsOut,
    RecommendationItem,
    RecommendationReport,
)
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
    creatives = CreativeRepository(db).list_all()
    report = rec.build_report(db, creatives)

    # Creative Score + 生命周期自动流转（计算即得；active→watch 自动标记，
    # 建议归档只进收件箱——见 services/lifecycle / review.archive_suggestion_items）
    from app.services import review as review_service
    from app.services.settings import resolve_score_config

    config = resolve_score_config(db)
    scores = review_service.creative_scores(db, report)
    lifecycle_service.apply_auto_transitions(
        db,
        {cid: (score.total, metrics.days_idle) for cid, (score, metrics) in scores.items()},
        config,
    )
    db.commit()
    states = {c.id: c.lifecycle_state for c in creatives}

    order = {"KEEP": 0, "ITERATE": 1, "PAUSE": 2, "ARCHIVE": 3}
    items = [
        RecommendationItem(
            creative_id=metrics.creative_id,
            creative_name=metrics.creative_name,
            dna_code=metrics.dna_code,
            dna_name=metrics.dna_name,
            action=action,
            reasons=reasons,
            metrics=MetricsOut(
                spend=metrics.spend,
                payers=metrics.payers,
                installs=metrics.installs,
                cpp=metrics.cpp,
                roas=metrics.roas,
                cpi=metrics.cpi,
                ipm=metrics.ipm,
                days_idle=metrics.days_idle,
                recent_spend=metrics.recent_spend,
                recent_cpp=metrics.recent_cpp,
                variant_count=metrics.variant_count,
                derivation_count=metrics.derivation_count,
                judged_count=metrics.judged_count,
                positive_count=metrics.positive_count,
                d3_roas=metrics.d3_roas,
                d1_retention=metrics.d1_retention,
            ),
            score=scores[metrics.creative_id][0].total,
            score_breakdown={
                "performance": scores[metrics.creative_id][0].performance,
                "freshness": scores[metrics.creative_id][0].freshness,
                "evolution": scores[metrics.creative_id][0].evolution,
                "confidence": scores[metrics.creative_id][0].confidence,
            },
            lifecycle_state=states.get(metrics.creative_id, "active"),
        )
        for metrics, action, reasons in report.items
    ]
    items.sort(key=lambda item: (order[item.action], -item.metrics.spend))
    return ok(
        RecommendationReport(
            generated_at=report.generated_at,
            date_min=report.date_min,
            date_max=report.date_max,
            items=items,
        )
    )


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
