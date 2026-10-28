"""Read-only observation replay; prints aggregates and never writes rulings.

Usage: python -m scripts.backtest_observation_resolution
Run after Alembic migration and the initial graph-to-SQL lifecycle sync.
"""

from __future__ import annotations

import json
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Creative, ObservationPair, Performance, SplitRuling
from app.services import markets
from app.services.observation_calibration import certify_policy
from app.services.observation_policy import (
    DEFAULT_POLICY_CONFIG,
    ObservationPolicyConfig,
    evaluate_observation_policy,
)
from app.services.recommendation import collect_creative_performance


def replay_observation_pairs(
    db: Session,
    *,
    config: ObservationPolicyConfig = DEFAULT_POLICY_CONFIG,
) -> dict[str, object]:
    pairs = list(db.scalars(select(ObservationPair)).all())
    creatives = {creative.id: creative for creative in db.scalars(select(Creative)).all()}
    performances = list(db.scalars(select(Performance)).all())
    as_of = max((row.date for row in performances if row.date), default=None)
    if as_of is None:
        from datetime import datetime, timezone

        as_of = datetime.now(timezone.utc).date()
    prefixes = markets.resolve_market_prefixes(db)
    outputs: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    performance_rule_passed = 0
    evaluated = 0

    for pair in pairs:
        if pair.status == "resolved_split":
            outputs["resolved_split"] += 1
            continue
        source = creatives.get(pair.creative_id_a)
        target = creatives.get(pair.creative_id_b)
        if source is None or target is None:
            outputs["missing_endpoint"] += 1
            continue
        result = evaluate_observation_policy(
            creative_id_a=source.id,
            creative_id_b=target.id,
            rows_a=collect_creative_performance(db, source, all_performances=performances),
            rows_b=collect_creative_performance(db, target, all_performances=performances),
            registered_at=pair.registered_at,
            evidence_start_at=pair.evidence_start_at,
            as_of=as_of,
            config=config,
            market_prefixes=prefixes,
        )
        evaluated += 1
        outputs[result.output] += 1
        reasons[result.waiting_reason or "certified_auto_split"] += 1
        performance_rule_passed += int(
            result.evidence.get("performance_rule_passed") is True
        )

    open_total = sum(outputs.values()) - outputs["resolved_split"]
    actionable_per_100 = 100.0 * outputs["actionable"] / open_total if open_total else 0.0
    certification = certify_policy([])
    ruling_types = Counter(
        ruling.ruling_type for ruling in db.scalars(select(SplitRuling)).all()
    )
    return {
        "read_only": True,
        "rule_version": config.rule_version,
        "policy_certified": config.certified,
        "data_cutoff": as_of.isoformat(),
        "pairs": dict(outputs),
        "waiting_reasons": dict(reasons),
        "performance_rule_passed": performance_rule_passed,
        "evaluated": evaluated,
        "actionable_per_100_open_pairs": round(actionable_per_100, 2),
        "split_rulings_by_type": dict(ruling_types),
        "certification": {
            "policy_id": certification.certified_policy_id,
            "reason": "independent_observation_labels_required",
            "risk_limit": certification.risk_limit,
            "confidence": certification.confidence,
        },
    }


def main() -> None:
    with SessionLocal() as db:
        report = replay_observation_pairs(db)
        db.rollback()
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
