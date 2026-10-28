"""观察对（维持拆分登记）的服务层。

POST /graph/similar 单条与 POST /graph/similar/bulk 批量共用同一登记语义
（Neo4j SIMILAR_TO 边 + SQL 后台观察状态 + edit_log 留痕），路由
只做参数解析与 commit / rebuild_mirror 收口。本模块一律不 commit。
"""

from __future__ import annotations

import logging
from typing import Sequence

from sqlalchemy.orm import Session

from app.config import Settings
from app.exceptions import ApiError
from app.models import Creative
from app.repositories.creatives import CreativeRepository
from app.repositories.edit_logs import EditLogRepository
from app.schemas.batch import BatchItemResult, BatchResult, collect
from app.services import graph_sync, merge_guard, observation_lifecycle

logger = logging.getLogger(__name__)


def get_pair(db: Session, source_id: str, target_id: str) -> tuple[Creative, Creative]:
    """自身成对 / 存在性校验（单条与批量共用的前置闸）。"""
    if source_id == target_id:
        raise ApiError(400, "不能与自身建立观察对")
    creative_repo = CreativeRepository(db)
    source = creative_repo.get(source_id)
    if source is None:
        raise ApiError(404, f"Creative 不存在：{source_id}")
    target = creative_repo.get(target_id)
    if target is None:
        raise ApiError(404, f"Creative 不存在：{target_id}")
    return source, target


def link_observation_pair_tx(
    db: Session,
    settings: Settings,
    source: Creative,
    target: Creative,
    reason: str = "",
) -> None:
    """Neo4j 关系 + SQL 后台观察状态 + edit_log；调用方统一提交。"""
    now = observation_lifecycle.utcnow()
    pair = observation_lifecycle.get_or_create_pair(
        db, source.id, target.id, registered_at=now
    )
    if pair.status == observation_lifecycle.RESOLVED_SPLIT:
        raise ApiError(409, "该对已结案维持拆分")
    try:
        graph_sync.get_graph_repository(settings).link_similar(
            source.id, target.id, reason
        )
        graph_sync.invalidate_similar_pairs_cache()
    except Exception as exc:  # noqa: BLE001
        raise ApiError(502, f"Neo4j 观察对建立失败：{exc}") from exc

    pair.status = observation_lifecycle.OBSERVING
    pair.registered_at = pair.registered_at or now
    pair.evidence_start_at = pair.evidence_start_at or now.date()
    pair.waiting_reason = "awaiting_evaluation"
    pair.next_evaluation_at = now
    EditLogRepository(db).record(
        entity_type="creative",
        entity_id=target.id,
        action="update",
        field="observation_pair",
        old_value="",
        new_value=f"{source.name} ↔ {target.name} 维持拆分登记（{reason}）",
    )


def _failed(item_id: str, exc: Exception) -> BatchItemResult:
    if isinstance(exc, ApiError):
        reason = {404: "not_found", 409: "conflict"}.get(exc.status_code, "error")
        return BatchItemResult(
            id=item_id, status="failed", reason=reason, message=exc.message
        )
    logger.warning("批量观察对 %s 登记失败: %s", item_id, exc)
    return BatchItemResult(id=item_id, status="failed", reason="error",
                           message=str(exc))


def run_bulk_similar(
    db: Session,
    settings: Settings,
    pairs: Sequence[tuple[str, str]],
    reason: str = "",
) -> BatchResult:
    """批量登记观察对（P0b）：逐项 savepoint 隔离，单项失败不拖垮整批。

    per-item 语义：success=登记成功；skipped(already_done)=已是观察对；
    skipped(conflict)=已存在结案裁决（split_rulings 命中）；failed(not_found)
    =creative 不存在。登记是非破坏动作：不阻塞未来合并，只把对排除出候选。
    """
    try:
        observed = {
            frozenset(pair)
            for pair in graph_sync.read_similar_pairs_cached(settings)
        }
    except Exception:  # noqa: BLE001 — Neo4j 不可用时各项会在登记处按失败处理
        observed = set()
    items: list[BatchItemResult] = []
    for source_id, target_id in pairs:
        item_id = f"{source_id}:{target_id}"
        try:
            with db.begin_nested():
                source, target = get_pair(db, source_id, target_id)
                pair = frozenset((source.id, target.id))
                if pair in observed:
                    item = BatchItemResult(
                        id=item_id, status="skipped", reason="already_done",
                        message="已是观察对",
                    )
                else:
                    ruling = merge_guard.find_db_ruling(
                        db, source.name, target.name, source.id, target.id
                    )
                    if ruling is not None:
                        item = BatchItemResult(
                            id=item_id, status="skipped", reason="conflict",
                            message=(
                                "该对已结案维持拆分"
                                f"（{ruling.reason or '无理由记录'}）"
                            ),
                        )
                    else:
                        link_observation_pair_tx(
                            db, settings, source, target, reason
                        )
                        # 同批内的重复对（含反向）按 already_done 处理
                        observed.add(pair)
                        item = BatchItemResult(id=item_id, status="success")
        except Exception as exc:  # noqa: BLE001 — 单项失败仅回滚该项
            item = _failed(item_id, exc)
        items.append(item)
    return collect(items)
