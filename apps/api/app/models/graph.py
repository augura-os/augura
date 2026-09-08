"""GraphNode / GraphEdge — SQL mirror of the Neo4j graph (contract §6).

Neo4j is the relationship source of truth; these tables power the
``GET /graph`` fallback when Neo4j is unavailable. Node ids use the same
``"<type>:<ref_id>"`` scheme as packages/graph so both sources are
interchangeable for the frontend.
"""

from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class GraphNode(TimestampMixin, Base):
    __tablename__ = "graph_nodes"

    # e.g. "creative:<uuid>" — mirrors packages/graph make_node_id().
    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    # "creative" | "variant" | "asset" | "tag"
    type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    ref_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)


class GraphEdge(TimestampMixin, Base):
    __tablename__ = "graph_edges"

    # e.g. "creative:<uuid>|HAS_VARIANT|variant:<uuid>"
    id: Mapped[str] = mapped_column(String(360), primary_key=True)
    source: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    target: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    # "HAS_VARIANT" | "HAS_ASSET" | "HAS_TAG"
    type: Mapped[str] = mapped_column(String(32), nullable=False)
