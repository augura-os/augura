"""Neo4j synchronization helpers.

Neo4j is the relationship source of truth (contract §7), but the MVP must
keep working without it: every helper logs a warning and continues when
Neo4j is unavailable — the SQL mirror (graph_nodes / graph_edges) is
rebuilt from relational state afterwards and powers the /graph fallback.
"""

from __future__ import annotations

import logging
from typing import Sequence

from graph import GraphRepository

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
