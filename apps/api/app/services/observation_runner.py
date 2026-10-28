"""Scheduled observation evaluation with shadow-by-default execution."""

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Creative, ObservationEvaluation, ObservationPair, Performance
from app.repositories.settings import SettingsRepository
from app.services import (
    graph_sync,
    judge_calibration,
    markets,
    observation_lifecycle,
    observation_resolution,
)
from app.services.observation_policy import (
    DEFAULT_POLICY_CONFIG,
    ObservationPolicyConfig,
    evaluate_observation_policy,
)
from app.services.recommendation import collect_creative_performance

logger = logging.getLogger(__name__)
EVALUATION_INTERVAL = timedelta(hours=6)


def load_policy_config(db: Session) -> ObservationPolicyConfig:
    raw = SettingsRepository(db).get("observation_policy_config")
    if not raw:
        return DEFAULT_POLICY_CONFIG
    try:
        return ObservationPolicyConfig(**json.loads(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        logger.warning("观察对策略配置无效，回退到未认证 shadow 策略")
        return DEFAULT_POLICY_CONFIG


def run_observation_evaluations(
    db: Session,
    settings: Settings,
    *,
    limit: int = 200,
) -> dict[str, int]:
    now = observation_lifecycle.utcnow()
    pairs = list(
        db.scalars(
            select(ObservationPair)
            .where(
                ObservationPair.status.in_(
                    [observation_lifecycle.OBSERVING, observation_lifecycle.ACTIONABLE]
                ),
                (ObservationPair.next_evaluation_at.is_(None))
                | (ObservationPair.next_evaluation_at <= now),
            )
            .order_by(ObservationPair.created_at, ObservationPair.id)
            .limit(limit)
        ).all()
    )
    performances = list(db.scalars(select(Performance)).all())
    creatives = {creative.id: creative for creative in db.scalars(select(Creative)).all()}
    as_of = max((row.date for row in performances if row.date), default=now.date())
    config = load_policy_config(db)
    prefixes = markets.resolve_market_prefixes(db)
    counts: Counter[str] = Counter()

    for candidate in pairs:
        pair = db.scalar(
            select(ObservationPair)
            .where(ObservationPair.id == candidate.id)
            .with_for_update()
        )
        if pair is None or pair.status == observation_lifecycle.RESOLVED_SPLIT:
            counts["skipped"] += 1
            continue
        source = creatives.get(pair.creative_id_a)
        target = creatives.get(pair.creative_id_b)
        if source is None or target is None:
            counts["skipped"] += 1
            continue
        rows_a = collect_creative_performance(db, source, all_performances=performances)
        rows_b = collect_creative_performance(db, target, all_performances=performances)
        result = evaluate_observation_policy(
            creative_id_a=source.id,
            creative_id_b=target.id,
            rows_a=rows_a,
            rows_b=rows_b,
            registered_at=pair.registered_at,
            evidence_start_at=pair.evidence_start_at,
            as_of=as_of,
            config=config,
            market_prefixes=prefixes,
        )
        auto_allowed = (
            result.output == "auto_split"
            and config.certified
            and not pair.auto_disabled
            and SettingsRepository(db).get("observation_auto_close_enabled") == "true"
            and judge_calibration.judge_auto_allowed(db, "observation_pair")
        )
        closed = False
        try:
            if auto_allowed:
                open_pairs = graph_sync.get_graph_repository(settings).read_similar_pairs()
                if frozenset((source.id, target.id)) not in {
                    frozenset(pair_ids) for pair_ids in open_pairs
                }:
                    raise RuntimeError("observation edge changed before automatic close")
                observation_resolution.close_observation_pair_tx(
                    db,
                    settings,
                    source,
                    target,
                    reason="Certified observation performance rule",
                    source_kind="auto_observation",
                    ruling_type="performance_split",
                    evidence=result.evidence,
                    rule_version=result.rule_version,
                )
                closed = True
            else:
                pair.status = (
                    observation_lifecycle.ACTIONABLE
                    if result.output == "actionable"
                    else observation_lifecycle.OBSERVING
                )
                pair.waiting_reason = (
                    "auto_close_disabled"
                    if result.output == "auto_split"
                    else result.waiting_reason
                )
                pair.rule_version = result.rule_version
                pair.evidence = result.evidence
                pair.next_evaluation_at = now + EVALUATION_INTERVAL

            pair.last_evaluated_at = now
            db.add(
                ObservationEvaluation(
                    pair_id=pair.id,
                    data_cutoff=as_of,
                    input_hash=str(result.evidence.get("input_hash", "")),
                    output=result.output,
                    shadow=not auto_allowed,
                    rule_version=result.rule_version,
                    evidence=result.evidence,
                )
            )
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
            logger.warning("观察对评估/结案失败 pair=%s", pair.id, exc_info=True)
            if auto_allowed:
                try:
                    graph_sync.get_graph_repository(settings).link_similar(
                        source.id, target.id, "automatic close rollback"
                    )
                    graph_sync.invalidate_similar_pairs_cache()
                except Exception:  # noqa: BLE001
                    logger.error("观察对自动结案图补偿失败 pair=%s", pair.id, exc_info=True)
            counts["errors"] += 1
            continue
        counts["closed" if closed else result.output] += 1
    return dict(counts)
