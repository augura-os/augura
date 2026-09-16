"""GraphNode / GraphEdge SQL mirror (contract §6).

The mirror is rebuilt from the relational tables after every graph mutation,
so it stays consistent whether or not Neo4j was reachable at write time —
its content is structurally identical to what Neo4j holds, since both are
derived from the same creative/variant/asset/tag rows.
"""

from __future__ import annotations

from graph import make_edge_id, make_node_id
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.models import (
    Creative,
    CreativeAsset,
    CreativeDNA,
    CreativeVariant,
    GraphEdge,
    GraphNode,
    Tag,
    TagAssignment,
    VariantDerivation,
)

# Serializes concurrent rebuilds across sessions/threads/processes. Without
# it, two parallel analysis pipelines can both pass the DELETE (each sees a
# statement-time snapshot that hides the other's uncommitted inserts) and
# then collide on the deterministic edge ids at INSERT time — the batch
# upload of 2026-07-22 hit exactly this (graph_edges_pkey UniqueViolation).
_MIRROR_LOCK_KEY = 721_603


def rebuild_mirror(db: Session) -> None:
    """Rewrite the whole mirror from relational state and commit."""
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _MIRROR_LOCK_KEY})
    dnas_by_id = {d.id: d for d in db.scalars(select(CreativeDNA)).all()}
    creatives = list(db.scalars(select(Creative)).all())
    variants = list(db.scalars(select(CreativeVariant)).all())
    assets_by_id = {a.id: a for a in db.scalars(select(CreativeAsset)).all()}
    tags_by_id = {t.id: t for t in db.scalars(select(Tag)).all()}
    assignments = list(db.scalars(select(TagAssignment)).all())

    db.execute(delete(GraphEdge))
    db.execute(delete(GraphNode))

    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    seen_node_ids: set[str] = set()

    def add_node(node: GraphNode) -> None:
        if node.id not in seen_node_ids:
            seen_node_ids.add(node.id)
            nodes.append(node)

    for creative in creatives:
        creative_node_id = make_node_id("creative", creative.id)
        add_node(
            GraphNode(
                id=creative_node_id,
                type="creative",
                label=creative.name,
                ref_id=creative.id,
            )
        )
        dna = dnas_by_id.get(creative.dna_id) if creative.dna_id else None
        if dna is not None:
            dna_node_id = make_node_id("dna", dna.id)
            add_node(
                GraphNode(
                    id=dna_node_id,
                    type="dna",
                    label=f"{dna.code} {dna.name}",
                    ref_id=dna.id,
                )
            )
            edges.append(
                GraphEdge(
                    id=make_edge_id(dna_node_id, "HAS_CREATIVE", creative_node_id),
                    source=dna_node_id,
                    target=creative_node_id,
                    type="HAS_CREATIVE",
                )
            )

    for variant in variants:
        variant_node_id = make_node_id("variant", variant.id)
        creative_node_id = make_node_id("creative", variant.creative_id)
        add_node(
            GraphNode(
                id=variant_node_id,
                type="variant",
                label=variant.name,
                ref_id=variant.id,
            )
        )
        edges.append(
            GraphEdge(
                id=make_edge_id(creative_node_id, "HAS_VARIANT", variant_node_id),
                source=creative_node_id,
                target=variant_node_id,
                type="HAS_VARIANT",
            )
        )
        asset = assets_by_id.get(variant.asset_id)
        if asset is not None:
            asset_node_id = make_node_id("asset", asset.id)
            add_node(
                GraphNode(
                    id=asset_node_id,
                    type="asset",
                    label=asset.filename,
                    ref_id=asset.id,
                )
            )
            edges.append(
                GraphEdge(
                    id=make_edge_id(variant_node_id, "HAS_ASSET", asset_node_id),
                    source=variant_node_id,
                    target=asset_node_id,
                    type="HAS_ASSET",
                )
            )

    for assignment in assignments:
        asset = assets_by_id.get(assignment.asset_id)
        tag = tags_by_id.get(assignment.tag_id)
        if asset is None or tag is None:
            continue
        # Only assets that are part of the graph (wrapped by a variant).
        asset_node_id = make_node_id("asset", asset.id)
        if asset_node_id not in seen_node_ids:
            continue
        tag_node_id = make_node_id("tag", tag.id)
        add_node(
            GraphNode(id=tag_node_id, type="tag", label=tag.name, ref_id=tag.id)
        )
        edges.append(
            GraphEdge(
                id=make_edge_id(asset_node_id, "HAS_TAG", tag_node_id),
                source=asset_node_id,
                target=tag_node_id,
                type="HAS_TAG",
            )
        )

    for derivation in db.scalars(select(VariantDerivation)).all():
        source_id = make_node_id("variant", derivation.source_variant_id)
        target_id = make_node_id("variant", derivation.target_variant_id)
        edges.append(
            GraphEdge(
                id=make_edge_id(source_id, "DERIVED_FROM", target_id),
                source=source_id,
                target=target_id,
                type="DERIVED_FROM",
            )
        )

    db.add_all(nodes)
    db.add_all(edges)
    db.commit()


def load_mirror(db: Session) -> tuple[list[GraphNode], list[GraphEdge]]:
    nodes = list(db.scalars(select(GraphNode)).all())
    edges = list(db.scalars(select(GraphEdge)).all())
    return nodes, edges
