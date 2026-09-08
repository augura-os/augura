"""Graph routes: GET /graph, POST /graph/merge, POST /graph/split (§3/§7).

/graph reads Neo4j first (relationship source of truth) and falls back to
the SQL mirror (graph_nodes / graph_edges) when Neo4j is unavailable.
Merge/split return the full updated graph so the frontend can re-render.
"""

from __future__ import annotations

import logging
import uuid
from typing import cast

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import DbDep, SettingsDep
from app.config import Settings
from app.exceptions import ApiError
from app.models import Creative, SplitRuling
from app.repositories.creatives import CreativeRepository, VariantRepository
from app.repositories.edit_logs import EditLogRepository
from app.repositories.graph_mirror import load_mirror, rebuild_mirror
from app.schemas.common import Envelope, ok
from app.schemas.graph import (
    GraphEdgeOut,
    GraphEdgeType,
    GraphNodeOut,
    GraphNodeType,
    GraphOut,
    MergeRequest,
    SimilarCloseRequest,
    SimilarRequest,
    SplitRequest,
)
from app.services import graph_sync, merge_ops
from app.services.pipeline import cleanup_creative_if_empty

logger = logging.getLogger(__name__)

router = APIRouter()


def _mirror_graph(db: Session) -> GraphOut:
    nodes, edges = load_mirror(db)
    return GraphOut(
        nodes=[
            GraphNodeOut(
                id=node.id,
                type=cast(GraphNodeType, node.type),
                label=node.label,
                ref_id=node.ref_id,
            )
            for node in nodes
        ],
        edges=[
            GraphEdgeOut(
                id=edge.id,
                source=edge.source,
                target=edge.target,
                type=cast(GraphEdgeType, edge.type),
            )
            for edge in edges
        ],
    )


def _annotate_lifecycle(db: Session, graph: GraphOut) -> GraphOut:
    """给 creative 节点标注 lifecycle_state（图谱按它过滤已归档节点）。

    Neo4j/镜像都不存这个字段（状态是 SQL 属性，不是图关系），出口统一补。
    """
    states = dict(
        db.execute(select(Creative.id, Creative.lifecycle_state)).all()
    )
    for node in graph.nodes:
        if node.type == "creative":
            node.lifecycle_state = states.get(node.ref_id, "active")
    return graph


def _current_graph(db: Session, settings: Settings) -> GraphOut:
    """Neo4j first; SQL mirror fallback (contract §6)."""
    try:
        data = graph_sync.get_graph_repository(settings).read_graph()
        return _annotate_lifecycle(
            db,
            GraphOut(
                nodes=[GraphNodeOut(**node) for node in data["nodes"]],
                edges=[GraphEdgeOut(**edge) for edge in data["edges"]],
            ),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Neo4j 读取失败，回退 SQL 镜像: %s", exc)
        return _annotate_lifecycle(db, _mirror_graph(db))


@router.get("/graph", response_model=Envelope[GraphOut])
def get_graph(db: DbDep, settings: SettingsDep) -> Envelope[GraphOut]:
    return ok(_current_graph(db, settings))


@router.post("/graph/merge", response_model=Envelope[GraphOut])
def merge_creatives(
    payload: MergeRequest, db: DbDep, settings: SettingsDep
) -> Envelope[GraphOut]:
    # 执行体在 services/merge_ops（judge_pipeline 自动合并共用同一实现）
    hits = merge_ops.merge_creatives(
        db,
        settings,
        payload.source_creative_id,
        payload.target_creative_id,
        auto=False,
        force_reason=payload.force_reason,
    )

    message = "已合并"
    if any(hit.level == "block" for hit in hits):
        message = f"已合并（强制：{payload.force_reason}）"
    elif hits:
        message = "已合并（警告：" + "；".join(hit.message for hit in hits) + "）"
    return ok(_current_graph(db, settings), message=message)


@router.post("/graph/similar", response_model=Envelope[GraphOut])
def link_observation_pair(
    payload: SimilarRequest, db: DbDep, settings: SettingsDep
) -> Envelope[GraphOut]:
    """Register an observation pair (维持拆分) between two creatives."""
    if payload.source_creative_id == payload.target_creative_id:
        raise ApiError(400, "不能与自身建立观察对")
    creative_repo = CreativeRepository(db)
    source = creative_repo.get(payload.source_creative_id)
    if source is None:
        raise ApiError(404, f"Creative 不存在：{payload.source_creative_id}")
    target = creative_repo.get(payload.target_creative_id)
    if target is None:
        raise ApiError(404, f"Creative 不存在：{payload.target_creative_id}")

    try:
        graph_sync.get_graph_repository(settings).link_similar(
            source.id, target.id, payload.reason
        )
    except Exception as exc:  # noqa: BLE001
        raise ApiError(502, f"Neo4j 观察对建立失败：{exc}") from exc

    EditLogRepository(db).record(
        entity_type="creative",
        entity_id=target.id,
        action="update",
        field="observation_pair",
        old_value="",
        new_value=f"{source.name} ↔ {target.name} 维持拆分登记（{payload.reason}）",
    )
    db.commit()
    return ok(_current_graph(db, settings), message="已登记观察对")


@router.post("/graph/similar/close", response_model=Envelope[GraphOut])
def close_observation_pair(
    payload: SimilarCloseRequest, db: DbDep, settings: SettingsDep
) -> Envelope[GraphOut]:
    """Close an observation pair (结案维持拆分) — removes the SIMILAR_TO edge."""
    creative_repo = CreativeRepository(db)
    source = creative_repo.get(payload.source_creative_id)
    if source is None:
        raise ApiError(404, f"Creative 不存在：{payload.source_creative_id}")
    target = creative_repo.get(payload.target_creative_id)
    if target is None:
        raise ApiError(404, f"Creative 不存在：{payload.target_creative_id}")

    try:
        graph_sync.get_graph_repository(settings).unlink_similar(source.id, target.id)
    except Exception as exc:  # noqa: BLE001
        raise ApiError(502, f"Neo4j 观察对结案失败：{exc}") from exc

    # 结案 = 维持拆分裁决：写入 split_rulings（幂等），否则这对会重新
    # 浮回疑似待合并候选（只删边不留裁决 = 跑步机式循环）。
    low, high = sorted((source.name, target.name))
    existing = db.scalar(
        select(SplitRuling).where(
            SplitRuling.name_a == low, SplitRuling.name_b == high
        )
    )
    if existing is None:
        db.add(
            SplitRuling(
                id=str(uuid.uuid4()),
                name_a=low,
                name_b=high,
                reason=payload.reason.strip(),
                source="inbox_close",
            )
        )

    EditLogRepository(db).record(
        entity_type="creative",
        entity_id=target.id,
        action="update",
        field="observation_pair",
        old_value=f"{source.name} ↔ {target.name}",
        new_value="结案（维持拆分）",
    )
    db.commit()
    return ok(_current_graph(db, settings), message="已结案")


@router.post("/graph/split", response_model=Envelope[GraphOut])
def split_variants(
    payload: SplitRequest, db: DbDep, settings: SettingsDep
) -> Envelope[GraphOut]:
    if not payload.variant_ids:
        raise ApiError(400, "variant_ids 不能为空")
    creative_repo = CreativeRepository(db)
    creative = creative_repo.get(payload.creative_id)
    if creative is None:
        raise ApiError(404, f"Creative 不存在：{payload.creative_id}")

    variant_repo = VariantRepository(db)
    variants = variant_repo.get_many(payload.variant_ids)
    if len(variants) != len(set(payload.variant_ids)):
        raise ApiError(404, "部分 Variant 不存在")
    for variant in variants:
        if variant.creative_id != creative.id:
            raise ApiError(400, f"Variant {variant.id} 不属于该 Creative")

    new_creative = creative_repo.create(
        name=variants[0].name or "Split Creative",
        representative_embedding=variants[0].embedding,
    )
    for variant in variants:
        variant_repo.move_to_creative(variant, new_creative.id)
    EditLogRepository(db).record(
        entity_type="creative",
        entity_id=creative.id,
        action="split",
        old_value=f"{creative.name}: {len(variants)} variant(s)",
        new_value=f"{new_creative.name} ({new_creative.id})",
    )
    db.commit()

    try:
        graph_sync.get_graph_repository(settings).split_variants(
            new_creative.id,
            new_creative.name,
            [variant.id for variant in variants],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Neo4j split 同步失败: %s", exc)

    # All variants moved out → the old creative disappears.
    cleanup_creative_if_empty(db, settings, creative.id)
    rebuild_mirror(db)

    return ok(_current_graph(db, settings), message="已拆分")
