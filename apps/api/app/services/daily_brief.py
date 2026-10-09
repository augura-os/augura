"""决策简报（recommendations）的组装与有状态刷新。

- ``recommendation_report``：纯读组装——build_report → creative_scores →
  RecommendationItem，不写库；lifecycle_state 直接读 DB 当前值（GET 语义：
  看板读取不得改变素材状态）。
- ``refresh_creative_states``：有状态刷新——build_report → creative_scores
  → lifecycle.apply_auto_transitions（active→watch 自动标记，逐条写
  edit_logs 留痕）→ verdict_snapshots.write_snapshots（判定快照落库，
  content_hash 去重）；不 commit，由调用方（POST .../refresh 路由、投放
  数据导入）统一 commit。返回统计（{"transitions": n, "snapshots": m}），
  供端点响应与排障复用。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.repositories.creatives import CreativeRepository
from app.schemas.recommendation import (
    MetricsOut,
    ReasonBitModel,
    RecommendationItem,
    RecommendationReport,
)
from app.services import lifecycle as lifecycle_service
from app.services import recommendation as rec
from app.services import review as review_service
from app.services import verdict_snapshots as snapshot_service
from app.services.settings import resolve_score_config


def recommendation_report(db: Session) -> RecommendationReport:
    """组装推荐报表（纯读）。

    score 为展示用计算值（计算即得，不落库）；lifecycle_state 是 DB
    当前值——自动流转只在 refresh_creative_states 里发生。
    """
    creatives = CreativeRepository(db).list_all()
    report = rec.build_report(db, creatives)
    scores = review_service.creative_scores(db, report)
    states = {c.id: c.lifecycle_state for c in creatives}

    order = {"KEEP": 0, "ITERATE": 1, "PAUSE": 2, "ARCHIVE": 3}
    items = [
        RecommendationItem(
            creative_id=metrics.creative_id,
            creative_name=metrics.creative_name,
            dna_code=metrics.dna_code,
            dna_name=metrics.dna_name,
            action=verdict.action,
            reasons=verdict.reasons,
            reason_code=verdict.reason_code,
            reason_params=verdict.params,
            reason_bits=[
                ReasonBitModel(code=bit.code, params=bit.params)
                for bit in verdict.supplementary
            ],
            labels=list(verdict.labels),
            priority_dollars=verdict.priority_dollars,
            confidence=verdict.confidence,
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
        for metrics, verdict in report.items
    ]
    items.sort(key=lambda item: (order[item.action], -item.priority_dollars))
    return RecommendationReport(
        generated_at=report.generated_at,
        date_min=report.date_min,
        date_max=report.date_max,
        items=items,
    )


def refresh_creative_states(db: Session) -> dict[str, int]:
    """重算 creative score、自动流转 lifecycle_state 并落 verdict 决策快照。

    触发点：POST /creatives/recommendations/refresh、投放数据（Excel）
    导入完成后——数据变化时流转，而不是看板读取时。archived 是人工
    领地，永不自动流转（见 services/lifecycle）。快照按 content_hash
    去重：判定内容变化的 creative 才新增行（见 services/verdict_snapshots）。
    """
    creatives = CreativeRepository(db).list_all()
    report = rec.build_report(db, creatives)
    config = resolve_score_config(db)
    scores = review_service.creative_scores(db, report)
    transitions = lifecycle_service.apply_auto_transitions(
        db,
        {
            cid: (score.total, metrics.days_idle)
            for cid, (score, metrics) in scores.items()
        },
        config,
    )
    snapshots = snapshot_service.write_snapshots(db, report.items)
    return {"transitions": transitions, "snapshots": snapshots}
