"""SQL lifecycle for observation pairs; Neo4j remains the graph relation."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Creative, EditLog, ObservationPair
from app.services import graph_sync

OBSERVING = "observing"
ACTIONABLE = "actionable"
RESOLVED_SPLIT = "resolved_split"


def canonical_ids(left_id: str, right_id: str) -> tuple[str, str]:
    return tuple(sorted((left_id, right_id)))  # type: ignore[return-value]


def get_or_create_pair(
    db: Session,
    left_id: str,
    right_id: str,
    *,
    registered_at: datetime | None = None,
) -> ObservationPair:
    left_id, right_id = canonical_ids(left_id, right_id)
    pair = db.scalar(
        select(ObservationPair).where(
            ObservationPair.creative_id_a == left_id,
            ObservationPair.creative_id_b == right_id,
        )
    )
    if pair is not None:
        return pair
    pair = ObservationPair(
        creative_id_a=left_id,
        creative_id_b=right_id,
        registered_at=registered_at,
        evidence_start_at=registered_at.date() if registered_at else None,
    )
    db.add(pair)
    db.flush()
    return pair


def _registration_times(db: Session) -> list[EditLog]:
    return list(
        db.scalars(
            select(EditLog)
            .where(EditLog.field == "observation_pair")
            .order_by(EditLog.created_at)
        ).all()
    )


def sync_graph_pairs(db: Session, settings: Settings) -> dict[str, int]:
    pairs = graph_sync.read_similar_pairs_cached(settings)
    creatives = {
        creative.id: creative
        for creative in db.scalars(select(Creative)).all()
    }
    logs = _registration_times(db)
    added = 0
    missing = 0
    for left_id, right_id in pairs:
        left = creatives.get(left_id)
        right = creatives.get(right_id)
        if left is None or right is None:
            missing += 1
            continue
        left_id, right_id = canonical_ids(left_id, right_id)
        existing = db.scalar(
            select(ObservationPair).where(
                ObservationPair.creative_id_a == left_id,
                ObservationPair.creative_id_b == right_id,
            )
        )
        if existing is not None:
            continue
        registered_at = next(
            (
                log.created_at
                for log in logs
                if log.entity_id in (left_id, right_id)
                and "维持拆分登记" in (log.new_value or "")
                and left.name in (log.new_value or "")
                and right.name in (log.new_value or "")
            ),
            None,
        )
        get_or_create_pair(db, left_id, right_id, registered_at=registered_at)
        added += 1

    graph_pairs = {frozenset(pair) for pair in pairs}
    unresolved = db.scalars(
        select(ObservationPair).where(ObservationPair.status != RESOLVED_SPLIT)
    ).all()
    missing_edges = 0
    for pair in unresolved:
        if frozenset((pair.creative_id_a, pair.creative_id_b)) not in graph_pairs:
            pair.status = ACTIONABLE
            pair.waiting_reason = "graph_relation_missing"
            pair.next_evaluation_at = None
            missing_edges += 1
    return {
        "added": added,
        "missing_endpoints": missing,
        "missing_edges": missing_edges,
        "open_edges": len(pairs),
    }


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
