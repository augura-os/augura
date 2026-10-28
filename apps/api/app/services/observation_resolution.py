"""Shared manual/automatic observation resolution; callers own the transaction."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Creative, SplitRuling
from app.repositories.edit_logs import EditLogRepository
from app.services import graph_sync, observation_lifecycle


def close_observation_pair_tx(
    db: Session,
    settings: Settings,
    source: Creative,
    target: Creative,
    *,
    reason: str,
    source_kind: str = "inbox_close",
    ruling_type: str = "content_split",
    evidence: dict[str, object] | None = None,
    rule_version: str = "",
) -> None:
    pair = observation_lifecycle.get_or_create_pair(
        db, source.id, target.id, registered_at=observation_lifecycle.utcnow()
    )
    graph_sync.get_graph_repository(settings).unlink_similar(source.id, target.id)
    graph_sync.invalidate_similar_pairs_cache()

    left_id, right_id = observation_lifecycle.canonical_ids(source.id, target.id)
    existing = db.scalar(
        select(SplitRuling).where(
            SplitRuling.creative_id_a == left_id,
            SplitRuling.creative_id_b == right_id,
        )
    )
    low, high = sorted((source.name, target.name))
    if existing is None:
        existing = db.scalar(
            select(SplitRuling).where(
                SplitRuling.name_a == low, SplitRuling.name_b == high
            )
        )
    if existing is None:
        existing = SplitRuling(
            id=str(uuid.uuid4()),
            creative_id_a=left_id,
            creative_id_b=right_id,
            name_a=low,
            name_b=high,
            reason=reason.strip(),
            source=source_kind,
            ruling_type=ruling_type,
        )
        db.add(existing)
    else:
        existing.creative_id_a = existing.creative_id_a or left_id
        existing.creative_id_b = existing.creative_id_b or right_id

    pair.status = observation_lifecycle.RESOLVED_SPLIT
    pair.resolution_type = ruling_type
    pair.evidence = evidence or {}
    pair.rule_version = rule_version
    pair.next_evaluation_at = None
    automatic = source_kind.startswith("auto")
    EditLogRepository(db).record(
        entity_type="creative",
        entity_id=target.id,
        action="update",
        field="observation_pair",
        old_value=f"{source.name} ↔ {target.name}",
        new_value=("auto: " if automatic else "") + "结案（维持拆分）",
    )


def reopen_observation_pair_tx(
    db: Session,
    settings: Settings,
    source: Creative,
    target: Creative,
    *,
    reason: str,
) -> None:
    from app.exceptions import ApiError
    from app.services import merge_guard

    if not reason.strip():
        raise ApiError(400, "撤销自动结案必须填写理由")
    ruling = merge_guard.find_db_ruling(db, source.name, target.name, source.id, target.id)
    if ruling is None:
        raise ApiError(404, "没有找到该观察对的结案裁决")
    if ruling.source != "auto_observation":
        raise ApiError(409, "只能撤销自动观察结案")

    now = observation_lifecycle.utcnow()
    pair = observation_lifecycle.get_or_create_pair(db, source.id, target.id)
    previous_evidence = dict(pair.evidence)
    previous_rule_version = pair.rule_version
    graph_sync.get_graph_repository(settings).link_similar(
        source.id, target.id, f"manual reopen: {reason.strip()}"
    )
    graph_sync.invalidate_similar_pairs_cache()
    db.delete(ruling)

    pair.status = observation_lifecycle.OBSERVING
    pair.registered_at = now
    pair.evidence_start_at = now.date()
    pair.last_evaluated_at = None
    pair.next_evaluation_at = now
    pair.waiting_reason = "manual_reopen"
    pair.resolution_type = None
    pair.auto_disabled = True
    pair.evidence = {
        "reopen_reason": reason.strip(),
        "previous_rule_version": previous_rule_version,
        "previous_evidence": previous_evidence,
    }
    EditLogRepository(db).record(
        entity_type="creative",
        entity_id=target.id,
        action="update",
        field="observation_pair",
        old_value="auto: 结案（维持拆分）",
        new_value=f"人工撤销自动结案，禁止自动重试（{reason.strip()}）",
    )
