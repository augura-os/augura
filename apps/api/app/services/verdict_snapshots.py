"""Verdict 决策快照落库：把规则引擎每次判定的输入/输出写进 verdict_snapshots。

只在有状态刷新流里调用（daily_brief.refresh_creative_states，触发点 =
POST /creatives/recommendations/refresh 与投放数据导入）；GET 读路径保持
纯读，不写快照。

去重：content_hash 对 {action, reason_code, params, rules_version,
thresholds, metrics, labels} 做 canonical JSON（sort_keys + default=str）的
sha256；同一 creative 最新快照指纹相同则跳过——只有内容变化才产生新行，
历史可对比且表不膨胀。reasons / supplementary / priority_dollars /
confidence 不参与指纹：它们是同一输入的确定推导物，变化必然伴随
action/params/metrics 之一变化。labels（赢家标签）参与指纹：基准或窗口
数据变化可能只改标签而不改 action/metrics 口径内的值。
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Sequence

from sqlalchemy.orm import Session

from app.models import VerdictSnapshot
from app.repositories.verdict_snapshots import VerdictSnapshotRepository
from app.services.recommendation_rules import RULES_VERSION, profile_thresholds
from app.services.settings import resolve_thresholds

if TYPE_CHECKING:
    from app.services.recommendation import CreativeMetrics, Verdict


def _content_hash(payload: dict[str, object]) -> str:
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def write_snapshots(
    db: Session, items: Sequence[tuple["CreativeMetrics", "Verdict"]]
) -> int:
    """为一次 refresh 的 (metrics, verdict) 列表写决策快照；返回实际插入条数。

    阈值解析与 build_report 内部同函数同口径（按 main_market 分市场解析 +
    优化方式 profile 乘数叠加，带 memo）；不 commit，由调用方统一提交（与
    refresh_creative_states 的事务边界一致）。
    """
    if not items:
        return 0
    repo = VerdictSnapshotRepository(db)
    latest = repo.latest_hashes()
    threshold_cache: dict[str | None, dict[str, float]] = {}
    now = datetime.now(timezone.utc)

    snapshots: list[VerdictSnapshot] = []
    for metrics, verdict in items:
        market = metrics.main_market or None
        if market not in threshold_cache:
            threshold_cache[market] = resolve_thresholds(db, market)
        # 与 build_report 同口径叠加优化方式 profile（vo 红线 ×2 等）——profile
        # 差异经 thresholds 进 content_hash；缓存 dict 只读，有乘数时返回副本
        thresholds = profile_thresholds(
            threshold_cache[market], metrics.optimization_type or None
        )
        metrics_dict = dataclasses.asdict(metrics)
        digest = _content_hash(
            {
                "action": verdict.action,
                "reason_code": verdict.reason_code,
                "params": verdict.params,
                "rules_version": RULES_VERSION,
                "thresholds": thresholds,
                "metrics": metrics_dict,
                "labels": list(verdict.labels),
            }
        )
        previous = latest.get(metrics.creative_id)
        if previous is not None and previous[1] == digest:
            continue
        # 单调性护栏：func.now() 是事务开始时刻，同一事务内的多次 refresh
        # （如测试的单事务）会得到相同 computed_at——latest 回接靠
        # max(computed_at)，必须保证同 creative 严格递增
        computed_at = now
        if previous is not None and previous[0] >= computed_at:
            computed_at = previous[0] + timedelta(microseconds=1)
        snapshots.append(
            VerdictSnapshot(
                creative_id=metrics.creative_id,
                computed_at=computed_at,
                action=verdict.action,
                reason_code=verdict.reason_code,
                params=verdict.params,
                reasons=list(verdict.reasons),
                supplementary=[
                    dataclasses.asdict(bit) for bit in verdict.supplementary
                ],
                priority_dollars=verdict.priority_dollars,
                confidence=verdict.confidence,
                rules_version=RULES_VERSION,
                thresholds=thresholds,
                metrics=metrics_dict,
                labels=list(verdict.labels),
                content_hash=digest,
            )
        )
        # 同一次 refresh 内同 creative 不重复插入（防 items 异常重复）
        latest[metrics.creative_id] = (computed_at, digest)

    repo.bulk_insert(snapshots)
    return len(snapshots)
