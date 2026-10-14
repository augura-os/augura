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
from graph import make_node_id
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import DbDep, SettingsDep
from app.config import Settings
from app.exceptions import ApiError
from app.models import Creative, CreativeVariant, SplitRuling, Tag, TagAssignment
from app.repositories.creatives import CreativeRepository, VariantRepository
from app.repositories.edit_logs import EditLogRepository
from app.repositories.graph_mirror import load_mirror, rebuild_mirror
from app.repositories.tags import COMPETITOR_REFERENCE_TAG, TagRepository
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
from app.services.clustering import mean_embeddings
from app.services.embedding import recompute_creative_representative
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


def _filter_competitor(db: Session, graph: GraphOut) -> GraphOut:
    """从 GraphOut 剔除竞品参考子图（GET /graph 默认视图）。

    Lab 竞品参考素材（sticky 标签 competitor-reference）与自有素材走同一条
    ingest 管线进图，但只服务诊断对照，不应污染图谱主视图：删其
    asset/variant 节点与任一端相连的边；variant 被删光的 creative 一并删；
    competitor-reference Tag 节点若因此无边可连也删。
    库内无竞品参考时整体跳过（零开销）。
    """
    tagged = db.execute(
        select(Tag.id, TagAssignment.asset_id)
        .join(TagAssignment, TagAssignment.tag_id == Tag.id)
        .where(Tag.name == COMPETITOR_REFERENCE_TAG)
    ).all()
    if not tagged:
        return graph
    tag_id = tagged[0].id  # Tag.name 唯一，所有行同属一个 Tag
    asset_ids = {row.asset_id for row in tagged}

    variant_rows = db.execute(
        select(CreativeVariant.id, CreativeVariant.creative_id).where(
            CreativeVariant.asset_id.in_(asset_ids)
        )
    ).all()
    removed_variant_ids = {row.id for row in variant_rows}
    removed_node_ids = {make_node_id("asset", asset_id) for asset_id in asset_ids}
    removed_node_ids |= {
        make_node_id("variant", variant_id) for variant_id in removed_variant_ids
    }

    # variant 全被删光的 creative 一并剔除（混族时保留仍有自有 variant 的族）
    affected_creative_ids = {row.creative_id for row in variant_rows}
    if affected_creative_ids:
        surviving_creative_ids = {
            row[0]
            for row in db.execute(
                select(CreativeVariant.creative_id).where(
                    CreativeVariant.creative_id.in_(affected_creative_ids),
                    CreativeVariant.id.notin_(removed_variant_ids),
                )
            ).all()
        }
        removed_node_ids |= {
            make_node_id("creative", creative_id)
            for creative_id in affected_creative_ids - surviving_creative_ids
        }

    graph.nodes = [node for node in graph.nodes if node.id not in removed_node_ids]
    graph.edges = [
        edge
        for edge in graph.edges
        if edge.source not in removed_node_ids and edge.target not in removed_node_ids
    ]
    # 竞品 Tag 节点的 HAS_TAG 边全随 asset 删除后即成孤儿，一并剔除
    tag_node_id = make_node_id("tag", tag_id)
    if not any(
        edge.source == tag_node_id or edge.target == tag_node_id
        for edge in graph.edges
    ):
        graph.nodes = [node for node in graph.nodes if node.id != tag_node_id]
    return graph


def _current_graph(
    db: Session, settings: Settings, *, include_competitor: bool = False
) -> GraphOut:
    """Neo4j first; SQL mirror fallback (contract §6).

    两条读取路径在出口汇聚：统一补 lifecycle_state；默认剔除竞品参考子图
    （``include_competitor=True`` 时全量返回，供 Lab 对照视图用）。
    """
    try:
        data = graph_sync.get_graph_repository(settings).read_graph()
        graph = GraphOut(
            nodes=[GraphNodeOut(**node) for node in data["nodes"]],
            edges=[GraphEdgeOut(**edge) for edge in data["edges"]],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Neo4j 读取失败，回退 SQL 镜像: %s", exc)
        graph = _mirror_graph(db)
    graph = _annotate_lifecycle(db, graph)
    if not include_competitor:
        graph = _filter_competitor(db, graph)
    return graph


@router.get("/graph", response_model=Envelope[GraphOut])
def get_graph(
    db: DbDep, settings: SettingsDep, include_competitor: bool = False
) -> Envelope[GraphOut]:
    return ok(_current_graph(db, settings, include_competitor=include_competitor))


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
        graph_sync.invalidate_similar_pairs_cache()
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
        graph_sync.invalidate_similar_pairs_cache()
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
        raise ApiError(400, "variant_ids 不能为空", code="split_no_variants")
    creative_repo = CreativeRepository(db)
    creative = creative_repo.get(payload.creative_id)
    if creative is None:
        raise ApiError(
            404,
            f"Creative 不存在：{payload.creative_id}",
            code="creative_not_found",
            params={"id": payload.creative_id},
        )

    variant_repo = VariantRepository(db)
    variants = variant_repo.get_many(payload.variant_ids)
    if len(variants) != len(set(payload.variant_ids)):
        raise ApiError(404, "部分 Variant 不存在", code="split_variants_not_found")
    for variant in variants:
        if variant.creative_id != creative.id:
            raise ApiError(
                400,
                f"Variant {variant.id} 不属于该 Creative",
                code="split_variant_mismatch",
                params={"variant_id": variant.id},
            )

    # P0-3 口径：新族代表向量 = 有向量成员的真实均值（不再拿
    # variants[0].embedding 直接顶包），embedding_count = 参与均值的条数
    member_vectors = [v.embedding for v in variants if v.embedding]
    # 新族名从源族派生（slug 约定一致、可溯源），不沿用 variant 名——
    # variant 名 = 上传文件名 stem，顶包会把生产文件名带进 Creative 层。
    base_name = f"{creative.name}-split" if creative.name else "Split Creative"
    new_name = base_name
    suffix = 2
    while creative_repo.name_exists(new_name):
        new_name = f"{base_name}-{suffix}"
        suffix += 1
    # 文本签名与上传管线同格式（name + tags），否则无向量时的文本聚类
    # 兜底对拆分出的新族不可见。
    tag_repo = TagRepository(db)
    split_tag_names = sorted(
        {
            tag.name
            for variant in variants
            for tag in tag_repo.get_asset_tags(variant.asset_id)
        }
    )
    new_creative = creative_repo.create(
        name=new_name,
        representative_embedding=mean_embeddings(member_vectors),
        embedding_count=len(member_vectors),
        representative_text=" ".join([new_name, *split_tag_names]),
    )
    for variant in variants:
        variant_repo.move_to_creative(variant, new_creative.id)
    # P0-3 残留：成员被拆走后，源族代表向量/计数按剩余成员重算
    recompute_creative_representative(db, creative)
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
