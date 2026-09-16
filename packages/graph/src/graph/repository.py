"""Synchronous Neo4j repository for the Augura creative graph.

Graph shape (contract AGENT_SPEC §7):

    (CreativeDNA)-[:HAS_CREATIVE]->(Creative)-[:HAS_VARIANT]->(Variant)
        -[:HAS_ASSET]->(Asset)-[:HAS_TAG]->(Tag)
    (Creative)-[:SIMILAR_TO]->(Creative)   -- observation pairs (boundary-rules §4.1)

Every node carries:
    id      -- stable external id, ``"<type>:<ref_id>"`` (see ``make_node_id``)
    type    -- "creative" | "variant" | "asset" | "tag"
    label   -- human readable label
    ref_id  -- primary key of the mirrored row in PostgreSQL
"""

from __future__ import annotations

from typing import Literal, Sequence, TypedDict, cast

from neo4j import Driver, GraphDatabase

NodeType = Literal["creative", "variant", "asset", "tag", "dna"]
EdgeType = Literal["HAS_VARIANT", "HAS_ASSET", "HAS_TAG", "SIMILAR_TO", "HAS_CREATIVE", "DERIVED_FROM"]

_NODE_LABELS: dict[NodeType, str] = {
    "creative": "Creative",
    "variant": "Variant",
    "asset": "Asset",
    "tag": "Tag",
    "dna": "CreativeDNA",
}

_EDGE_TYPES: tuple[str, ...] = (
    "HAS_VARIANT",
    "HAS_ASSET",
    "HAS_TAG",
    "SIMILAR_TO",
    "HAS_CREATIVE",
    "DERIVED_FROM",
)


class GraphNodeDict(TypedDict):
    """Node DTO consumed by the ``GET /graph`` endpoint / React Flow."""

    id: str
    type: str
    label: str
    ref_id: str


class GraphEdgeDict(TypedDict):
    """Edge DTO consumed by the ``GET /graph`` endpoint / React Flow."""

    id: str
    source: str
    target: str
    type: str


class GraphDict(TypedDict):
    nodes: list[GraphNodeDict]
    edges: list[GraphEdgeDict]


def make_node_id(node_type: NodeType, ref_id: str) -> str:
    """Stable node id shared between Neo4j, the SQL mirror and the API DTO."""
    return f"{node_type}:{ref_id}"


def make_edge_id(source_id: str, edge_type: EdgeType, target_id: str) -> str:
    """Deterministic edge id (React Flow requires a string id per edge)."""
    return f"{source_id}|{edge_type}|{target_id}"


class GraphRepository:
    """Thin synchronous wrapper around the Neo4j python driver.

    All methods are idempotent (MERGE based) so failed pipelines can be
    retried safely. The driver itself is lazy: no connection is opened until
    the first query, and callers are expected to handle connection errors
    (the API logs a warning and falls back to the SQL mirror).
    """

    def __init__(
        self,
        uri: str,
        user: str,
        password: str,
        connection_timeout: float = 5.0,
    ) -> None:
        self._driver: Driver = GraphDatabase.driver(
            uri,
            auth=(user, password),
            connection_timeout=connection_timeout,
        )

    def close(self) -> None:
        self._driver.close()

    def verify_connectivity(self) -> None:
        self._driver.verify_connectivity()

    # ------------------------------------------------------------------
    # Node upserts
    # ------------------------------------------------------------------
    def upsert_node(self, node_type: NodeType, ref_id: str, label: str) -> str:
        """Create or update a node; returns its stable node id."""
        node_id = make_node_id(node_type, ref_id)
        neo4j_label = _NODE_LABELS[node_type]  # static label, safe to inline
        query = (
            f"MERGE (n:`{neo4j_label}` {{id: $id}}) "
            "SET n.ref_id = $ref_id, n.label = $label, n.type = $type "
            "RETURN n.id AS id"
        )
        with self._driver.session() as session:
            record = session.run(
                query, id=node_id, ref_id=ref_id, label=label, type=node_type
            ).single()
        if record is None:
            raise RuntimeError(f"failed to upsert {node_type} node {ref_id}")
        return cast(str, record["id"])

    def upsert_creative(self, ref_id: str, label: str) -> str:
        return self.upsert_node("creative", ref_id, label)

    def upsert_variant(self, ref_id: str, label: str) -> str:
        return self.upsert_node("variant", ref_id, label)

    def upsert_asset(self, ref_id: str, label: str) -> str:
        return self.upsert_node("asset", ref_id, label)

    def upsert_tag(self, ref_id: str, label: str) -> str:
        return self.upsert_node("tag", ref_id, label)

    def upsert_dna(self, ref_id: str, label: str) -> str:
        return self.upsert_node("dna", ref_id, label)

    def delete_dna(self, dna_ref_id: str) -> None:
        """Delete a DNA family node and its edges (family retired/merged)."""
        with self._driver.session() as session:
            session.run(
                "MATCH (d:CreativeDNA {ref_id: $ref_id}) DETACH DELETE d",
                ref_id=dna_ref_id,
            )

    # ------------------------------------------------------------------
    # Edges
    # ------------------------------------------------------------------
    def create_edge(self, source_id: str, target_id: str, edge_type: EdgeType) -> str:
        """Idempotently link two nodes; returns the deterministic edge id."""
        if edge_type not in _EDGE_TYPES:
            raise ValueError(f"unsupported edge type: {edge_type}")
        query = (
            f"MATCH (a {{id: $source}}), (b {{id: $target}}) "
            f"MERGE (a)-[r:`{edge_type}`]->(b) "
            "RETURN type(r) AS rel_type"
        )
        with self._driver.session() as session:
            record = session.run(query, source=source_id, target=target_id).single()
        if record is None:
            raise RuntimeError(
                f"cannot create {edge_type}: missing node {source_id} or {target_id}"
            )
        return make_edge_id(source_id, edge_type, target_id)

    def link_similar(self, creative_ref_a: str, creative_ref_b: str, reason: str = "") -> str:
        """Link two creatives as an observation pair (``SIMILAR_TO``).

        Observation pairs (boundary-rules §4.1) are suspected same-DNA
        creatives kept split for lack of evidence. The edge is semantically
        undirected; it is stored in a single canonical direction (smaller
        ref_id first) so repeated calls stay idempotent.
        """
        source_ref, target_ref = sorted((creative_ref_a, creative_ref_b))
        query = (
            "MATCH (a:Creative {ref_id: $source}), (b:Creative {ref_id: $target}) "
            "MERGE (a)-[r:SIMILAR_TO]->(b) "
            "SET r.reason = $reason "
            "RETURN type(r) AS rel_type"
        )
        with self._driver.session() as session:
            record = session.run(
                query, source=source_ref, target=target_ref, reason=reason
            ).single()
        if record is None:
            raise RuntimeError(
                f"cannot create SIMILAR_TO: missing creative {source_ref} or {target_ref}"
            )
        return make_edge_id(
            make_node_id("creative", source_ref),
            "SIMILAR_TO",
            make_node_id("creative", target_ref),
        )

    def unlink_similar(self, creative_ref_a: str, creative_ref_b: str) -> None:
        """Remove the observation-pair edge between two creatives (if any)."""
        with self._driver.session() as session:
            session.run(
                "MATCH (a:Creative {ref_id: $a})-[r:SIMILAR_TO]-(b:Creative {ref_id: $b}) "
                "DELETE r",
                a=creative_ref_a,
                b=creative_ref_b,
            )

    def link_derivation(
        self, source_variant_ref: str, target_variant_ref: str, factor: str = "unknown"
    ) -> str:
        """Link two variants as a derivation (``DERIVED_FROM`` with factor)."""
        source_id = make_node_id("variant", source_variant_ref)
        target_id = make_node_id("variant", target_variant_ref)
        query = (
            "MATCH (a:Variant {id: $source}), (b:Variant {id: $target}) "
            "MERGE (a)-[r:DERIVED_FROM]->(b) "
            "SET r.factor = $factor "
            "RETURN type(r) AS rel_type"
        )
        with self._driver.session() as session:
            record = session.run(
                query, source=source_id, target=target_id, factor=factor
            ).single()
        if record is None:
            raise RuntimeError(
                f"cannot create DERIVED_FROM: missing variant "
                f"{source_variant_ref} or {target_variant_ref}"
            )
        return make_edge_id(source_id, "DERIVED_FROM", target_id)

    def unlink_derivation(self, source_variant_ref: str, target_variant_ref: str) -> None:
        """Remove the DERIVED_FROM edge between two variants (if any)."""
        with self._driver.session() as session:
            session.run(
                "MATCH (a:Variant {ref_id: $source})-[r:DERIVED_FROM]->"
                "(b:Variant {ref_id: $target}) DELETE r",
                source=source_variant_ref,
                target=target_variant_ref,
            )

    def read_similar_pairs(self) -> list[tuple[str, str]]:
        """All observation pairs as ``(source_ref_id, target_ref_id)`` tuples."""
        with self._driver.session() as session:
            records = session.run(
                "MATCH (a:Creative)-[:SIMILAR_TO]->(b:Creative) "
                "RETURN a.ref_id AS source, b.ref_id AS target"
            )
            return [
                (cast(str, record["source"]), cast(str, record["target"]))
                for record in records
            ]

    def replace_asset_tag_edges(
        self, asset_ref_id: str, tags: Sequence[tuple[str, str]]
    ) -> None:
        """Replace an asset's HAS_TAG edges with exactly ``tags``.

        ``tags`` items are ``(tag_ref_id, tag_label)`` pairs; tag nodes are
        upserted as needed. Used by the human-edit flow (PUT /assets/{id}).
        """
        with self._driver.session() as session:
            session.run(
                "MATCH (a:Asset {ref_id: $ref_id})-[r:HAS_TAG]->(:Tag) DELETE r",
                ref_id=asset_ref_id,
            )
        for tag_ref_id, tag_label in tags:
            self.upsert_tag(tag_ref_id, tag_label)
            self.create_edge(
                make_node_id("asset", asset_ref_id),
                make_node_id("tag", tag_ref_id),
                "HAS_TAG",
            )
        self.prune_orphan_tags()

    def prune_orphan_tags(self) -> None:
        """Delete Tag nodes no asset points to anymore.

        Tag edges use replace semantics, so re-analysis or human edits can
        leave unconnected Tag nodes behind; they would otherwise render as
        floating nodes in /graph (which reads Neo4j first).
        """
        with self._driver.session() as session:
            session.run(
                "MATCH (t:Tag) WHERE NOT (t)<-[:HAS_TAG]-() DELETE t"
            )

    # ------------------------------------------------------------------
    # Deletes / structural operations
    # ------------------------------------------------------------------
    def delete_asset_subtree(self, asset_ref_id: str) -> list[str]:
        """Delete an asset node, its wrapping variant, and the creative if
        it becomes empty. Returns the ids of all deleted nodes."""
        deleted: list[str] = []
        with self._driver.session() as session:
            variant_ids = [
                cast(str, record["ref_id"])
                for record in session.run(
                    "MATCH (v:Variant)-[:HAS_ASSET]->(a:Asset {ref_id: $ref_id}) "
                    "RETURN v.ref_id AS ref_id",
                    ref_id=asset_ref_id,
                )
            ]
            creative_ids: list[str] = []
            if variant_ids:
                creative_ids = [
                    cast(str, record["ref_id"])
                    for record in session.run(
                        "MATCH (c:Creative)-[:HAS_VARIANT]->(v:Variant) "
                        "WHERE v.ref_id IN $variant_ids "
                        "RETURN DISTINCT c.ref_id AS ref_id",
                        variant_ids=variant_ids,
                    )
                ]
            session.run(
                "MATCH (a:Asset {ref_id: $ref_id}) DETACH DELETE a",
                ref_id=asset_ref_id,
            )
            deleted.append(make_node_id("asset", asset_ref_id))
            for variant_id in variant_ids:
                session.run(
                    "MATCH (v:Variant {ref_id: $ref_id}) DETACH DELETE v",
                    ref_id=variant_id,
                )
                deleted.append(make_node_id("variant", variant_id))
            for creative_id in creative_ids:
                record = session.run(
                    "MATCH (c:Creative {ref_id: $ref_id})-[:HAS_VARIANT]->(v:Variant) "
                    "RETURN count(v) AS remaining",
                    ref_id=creative_id,
                ).single()
                remaining = cast(int, record["remaining"]) if record is not None else 0
                if remaining == 0:
                    session.run(
                        "MATCH (c:Creative {ref_id: $ref_id}) DETACH DELETE c",
                        ref_id=creative_id,
                    )
                    deleted.append(make_node_id("creative", creative_id))
        self.prune_orphan_tags()
        return deleted

    def delete_creative(self, creative_ref_id: str) -> None:
        """Delete a creative node (used when it becomes empty after moves)."""
        with self._driver.session() as session:
            session.run(
                "MATCH (c:Creative {ref_id: $ref_id}) DETACH DELETE c",
                ref_id=creative_ref_id,
            )

    def merge_creatives(self, source_ref_id: str, target_ref_id: str) -> None:
        """Move all variants from the source creative to the target, then
        delete the source creative node."""
        with self._driver.session() as session:
            session.run(
                "MATCH (s:Creative {ref_id: $source})-[r:HAS_VARIANT]->(v:Variant) "
                "MATCH (t:Creative {ref_id: $target}) "
                "DELETE r "
                "MERGE (t)-[:HAS_VARIANT]->(v)",
                source=source_ref_id,
                target=target_ref_id,
            )
            session.run(
                "MATCH (s:Creative {ref_id: $source}) DETACH DELETE s",
                source=source_ref_id,
            )

    def split_variants(
        self,
        new_creative_ref_id: str,
        new_creative_label: str,
        variant_ref_ids: Sequence[str],
    ) -> None:
        """Move the given variants onto a (newly created) creative node."""
        self.upsert_creative(new_creative_ref_id, new_creative_label)
        with self._driver.session() as session:
            for variant_ref_id in variant_ref_ids:
                session.run(
                    "MATCH (:Creative)-[r:HAS_VARIANT]->(v:Variant {ref_id: $ref_id}) "
                    "DELETE r",
                    ref_id=variant_ref_id,
                )
                session.run(
                    "MATCH (c:Creative {ref_id: $creative_id}), "
                    "(v:Variant {ref_id: $variant_id}) "
                    "MERGE (c)-[:HAS_VARIANT]->(v)",
                    creative_id=new_creative_ref_id,
                    variant_id=variant_ref_id,
                )

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    def read_graph(self) -> GraphDict:
        """Read the full graph as ``{nodes, edges}`` for React Flow."""
        nodes: list[GraphNodeDict] = []
        edges: list[GraphEdgeDict] = []
        with self._driver.session() as session:
            node_records = session.run(
                "MATCH (n) "
                "WHERE n:Creative OR n:Variant OR n:Asset OR n:Tag OR n:CreativeDNA "
                "RETURN n.id AS id, n.type AS type, n.label AS label, "
                "n.ref_id AS ref_id"
            )
            for record in node_records:
                nodes.append(
                    GraphNodeDict(
                        id=cast(str, record["id"]),
                        type=cast(str, record["type"]),
                        label=cast(str, record["label"] or ""),
                        ref_id=cast(str, record["ref_id"]),
                    )
                )
            edge_records = session.run(
                "MATCH (a)-[r:HAS_VARIANT|HAS_ASSET|HAS_TAG|SIMILAR_TO|HAS_CREATIVE|DERIVED_FROM]->(b) "
                "RETURN a.id AS source, b.id AS target, type(r) AS type"
            )
            for record in edge_records:
                source = cast(str, record["source"])
                target = cast(str, record["target"])
                edge_type = cast(str, record["type"])
                edges.append(
                    GraphEdgeDict(
                        id=make_edge_id(source, cast(EdgeType, edge_type), target),
                        source=source,
                        target=target,
                        type=edge_type,
                    )
                )
        return GraphDict(nodes=nodes, edges=edges)
