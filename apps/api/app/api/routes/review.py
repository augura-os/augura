"""Review queue route: GET /review/queue."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import DbDep
from app.models import Performance
from app.schemas.common import Envelope, ok
from app.schemas.review import (
    HubSkew,
    InterventionDensity,
    InterventionWeek,
    ReviewQueue,
)
from app.services import intervention as intervention_service
from app.services import judge_calibration as judge_calibration_service
from app.services import review as review_service

router = APIRouter()


@router.get("/review/queue", response_model=Envelope[ReviewQueue])
def review_queue(db: DbDep) -> Envelope[ReviewQueue]:
    max_date = db.scalar(select(Performance.date).order_by(Performance.date.desc()).limit(1))
    # Preload performances once — per-creative matching then happens in
    # memory instead of a full table scan per creative (N+1).
    all_performances = list(db.scalars(select(Performance)).all())
    density_weeks = [
        InterventionWeek(
            week_start=week.week_start,
            human_rulings=week.human_rulings,
            new_creatives=week.new_creatives,
            density=week.density,
        )
        for week in intervention_service.intervention_density(db)
    ]
    skew = intervention_service.attach_skew(db)
    return ok(
        ReviewQueue(
            generated_at=datetime.now(timezone.utc),
            low_confidence=review_service.low_confidence_items(db),
            dna_unassigned=review_service.dna_unassigned_items(db),
            merge_candidates=review_service.merge_candidate_items(db),
            observation_pairs=review_service.observation_pair_items(
                db, max_date=max_date, all_performances=all_performances
            ),
            pending_verdicts=review_service.pending_verdict_items(
                db, all_performances=all_performances
            ),
            derivation_reviews=review_service.derivation_review_items(db),
            archive_suggestions=review_service.archive_suggestion_items(db),
            market_conflicts=review_service.market_conflict_items(db),
            threshold_calibrations=review_service.threshold_calibration_items(db),
            market_detects=review_service.market_detect_items(db),
            family_bootstraps=review_service.family_bootstrap_items(db),
            auto_brakes=review_service.auto_brake_items(db),
            rule_keywords=review_service.rule_keyword_items(db),
            family_bootstrap_hint=review_service.family_bootstrap_hint(db),
            judge_stats=judge_calibration_service.judge_stats(db),
            intervention_density=InterventionDensity(
                weeks=density_weeks,
                current=density_weeks[-1] if density_weeks else None,
            ),
            hub_skew=HubSkew(
                attach_total=skew.attach_total,
                creatives_with_attaches=skew.creatives_with_attaches,
                top_creative_name=skew.top_creative_name,
                top_count=skew.top_count,
                top_share=skew.top_share,
            ),
        )
    )
