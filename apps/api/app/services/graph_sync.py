"""Neo4j synchronization helpers.

Neo4j is the relationship source of truth (contract §7), but the MVP must
keep working without it: every helper logs a warning and continues when
Neo4j is unavailable — the SQL mirror (graph_nodes / graph_edges) is
rebuilt from relational state afterwards and powers the /graph fallback.
"""

from __future__ import annotations

import logging
import time
from typing import Sequence

from graph import GraphRepository, NodeType
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Creative, CreativeAsset, CreativeDNA, CreativeVariant, Tag

logger = logging.getLogger(__name__)

_repository: GraphRepository | None = None


def get_graph_repository(settings: Settings) -> GraphRepository:
    """Process-wide lazily created repository (the driver connects lazily;
    failures surface on first query and are handled by callers)."""
    global _repository
    if _repository is None:
        _repository = GraphRepository(
            settings.neo4j_uri,
            settings.neo4j_user,
            settings.neo4j_password,
        )
    return _repository


# SIMILAR_TO 变化极少（仅人工建/拆观察对），但每次 recommendations / review
# 请求都同步查一次 Neo4j（实测单次 ~2.3s）。进程内短 TTL 缓存 + 写操作主动失效。
_SIMILAR_PAIRS_TTL_SECONDS = 300.0
_similar_pairs_cache: tuple[float, list[tuple[str, str]]] | None = None


def read_similar_pairs_cached(settings: Settings) -> list[tuple[str, str]]:
    """``read_similar_pairs`` with a short process-local TTL."""
    global _similar_pairs_cache
    now = time.monotonic()
    if _similar_pairs_cache is not None:
        cached_at, pairs = _similar_pairs_cache
        if now - cached_at < _SIMILAR_PAIRS_TTL_SECONDS:
            return pairs
    pairs = list(get_graph_repository(settings).read_similar_pairs())
    _similar_pairs_cache = (now, pairs)
    return pairs


def invalidate_similar_pairs_cache() -> None:
    """Drop the TTL cache (called after link_similar / unlink_similar)."""
    global _similar_pairs_cache
    _similar_pairs_cache = None


def sync_asset_subgraph(
    settings: Settings,
    *,
    creative: Creative,
    variant: CreativeVariant,
    asset: CreativeAsset,
    tags: Sequence[Tag],
) -> None:
    """Upsert the full Creative→Variant→Asset→Tag subgraph for one asset."""
    try:
        repo = get_graph_repository(settings)
        creative_node = repo.upsert_creative(creative.id, creative.name)
        variant_node = repo.upsert_variant(variant.id, variant.name)
        asset_node = repo.upsert_asset(asset.id, asset.filename)
        repo.create_edge(creative_node, variant_node, "HAS_VARIANT")
        repo.create_edge(variant_node, asset_node, "HAS_ASSET")
        # Replace (not append) tag edges so re-analysis can't leave stale
        # HAS_TAG edges behind after tags change.
        repo.replace_asset_tag_edges(asset.id, [(tag.id, tag.name) for tag in tags])
    except Exception as exc:  # noqa: BLE001 — Neo4j down must not break the flow
        logger.warning("Neo4j 同步失败，继续（SQL 镜像仍有效）: %s", exc)


def sync_creative_label(settings: Settings, creative: Creative) -> None:
    try:
        get_graph_repository(settings).upsert_creative(creative.id, creative.name)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Neo4j Creative 重命名同步失败: %s", exc)


def sync_dna_subgraph(
    settings: Settings, *, dna: CreativeDNA, creatives: Sequence[Creative]
) -> None:
    """Upsert a DNA family node and its HAS_CREATIVE edges (Pattern layer)."""
    try:
        repo = get_graph_repository(settings)
        dna_node = repo.upsert_dna(dna.id, f"{dna.code} {dna.name}")
        for creative in creatives:
            creative_node = repo.upsert_creative(creative.id, creative.name)
            repo.create_edge(dna_node, creative_node, "HAS_CREATIVE")
    except Exception as exc:  # noqa: BLE001 — Neo4j down must not break the flow
        logger.warning("Neo4j DNA 同步失败，继续（SQL 镜像仍有效）: %s", exc)


def sync_asset_tags(
    settings: Settings, asset: CreativeAsset, tags: Sequence[Tag]
) -> None:
    """Replace an asset's HAS_TAG edges after a human edit."""
    try:
        repo = get_graph_repository(settings)
        repo.upsert_asset(asset.id, asset.filename)
        repo.replace_asset_tag_edges(asset.id, [(tag.id, tag.name) for tag in tags])
    except Exception as exc:  # noqa: BLE001
        logger.warning("Neo4j 标签同步失败: %s", exc)


def delete_asset_subgraph(settings: Settings, asset_id: str) -> None:
    try:
        get_graph_repository(settings).delete_asset_subtree(asset_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Neo4j 删除 asset 子图失败: %s", exc)


def delete_creative_node(settings: Settings, creative_id: str) -> None:
    try:
        get_graph_repository(settings).delete_creative(creative_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Neo4j 删除 creative 节点失败: %s", exc)


def reconcile_graph(db: Session, settings: Settings) -> None:
    """Startup self-heal: reconcile Neo4j with PostgreSQL state.

    Neo4j drifts silently — every sync helper here swallows errors by design
    (Neo4j down must not break the flow), and several PG delete paths never
    propagate to Neo4j at all (family retire/merge, data resets, re-uploads).
    Since ``GET /graph`` reads Neo4j first, ghosts render as duplicate
    nodes. The reconciler prunes nodes whose ref_id is gone from PG,
    rebuilds HAS_CREATIVE edges from PG dna assignments (fixing ghost edges
    and missing edges in one pass), and drops orphan tags.
    """
    try:
        repo = get_graph_repository(settings)
        valid_ids: list[tuple[NodeType, list[str]]] = [
            ("dna", list(db.scalars(select(CreativeDNA.id)))),
            ("creative", list(db.scalars(select(Creative.id)))),
            ("variant", list(db.scalars(select(CreativeVariant.id)))),
            ("asset", list(db.scalars(select(CreativeAsset.id)))),
            ("tag", list(db.scalars(select(Tag.id)))),
        ]
        for node_type, ref_ids in valid_ids:
            deleted = repo.prune_ghost_nodes(node_type, ref_ids)
            if deleted:
                logger.info("Neo4j 对账：清理幽灵 %s 节点 %d 个", node_type, deleted)
        assignments = db.execute(
            select(Creative.dna_id, Creative.id).where(Creative.dna_id.is_not(None))
        ).all()
        edge_count = repo.replace_has_creative_edges(
            [(dna_id, creative_id) for dna_id, creative_id in assignments]
        )
        repo.prune_orphan_tags()
        logger.info(
            "Neo4j 对账完成：HAS_CREATIVE 边 %d 条（PG 归属 %d 个 creative）",
            edge_count,
            len(assignments),
        )
    except Exception as exc:  # noqa: BLE001 — Neo4j down must not block startup
        logger.warning("Neo4j 对账失败，继续（SQL 镜像仍有效）: %s", exc)
