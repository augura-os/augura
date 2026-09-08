"""initial schema — all 11 tables (contract AGENT_SPEC §6)

No local database was available for autogenerate; this migration is
handwritten to match app.models exactly.

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-25

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        *_timestamps(),
    )

    op.create_table(
        "creatives",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("name", sa.String(length=512), nullable=False, server_default=""),
        sa.Column(
            "representative_embedding",
            postgresql.JSONB(),
            nullable=True,
        ),
        *_timestamps(),
    )

    op.create_table(
        "creative_assets",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("file_type", sa.String(length=16), nullable=False),
        sa.Column(
            "mime_type",
            sa.String(length=128),
            nullable=False,
            server_default="application/octet-stream",
        ),
        sa.Column("storage_key", sa.String(length=1024), nullable=False, unique=True),
        sa.Column("thumbnail_key", sa.String(length=1024), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "analysis_status",
            sa.String(length=16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("status_message", sa.Text(), nullable=False, server_default=""),
        *_timestamps(),
    )

    op.create_table(
        "creative_variants",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "creative_id",
            sa.String(length=36),
            sa.ForeignKey("creatives.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "asset_id",
            sa.String(length=36),
            sa.ForeignKey("creative_assets.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("name", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("embedding", postgresql.JSONB(), nullable=True),
        *_timestamps(),
    )
    op.create_index(
        "ix_creative_variants_creative_id", "creative_variants", ["creative_id"]
    )

    op.create_table(
        "performances",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "asset_id",
            sa.String(length=36),
            sa.ForeignKey("creative_assets.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "creative_name", sa.String(length=512), nullable=False, server_default=""
        ),
        sa.Column("date", sa.Date(), nullable=True),
        sa.Column("impressions", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("clicks", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("spend", sa.Float(), nullable=False, server_default="0"),
        sa.Column("installs", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("raw", postgresql.JSONB(), nullable=False, server_default="{}"),
        *_timestamps(),
    )
    op.create_index("ix_performances_asset_id", "performances", ["asset_id"])

    op.create_table(
        "tags",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False, unique=True),
        *_timestamps(),
    )

    op.create_table(
        "tag_assignments",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "asset_id",
            sa.String(length=36),
            sa.ForeignKey("creative_assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tag_id",
            sa.String(length=36),
            sa.ForeignKey("tags.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.UniqueConstraint("asset_id", "tag_id", name="uq_tag_assignment"),
        *_timestamps(),
    )
    op.create_index("ix_tag_assignments_asset_id", "tag_assignments", ["asset_id"])
    op.create_index("ix_tag_assignments_tag_id", "tag_assignments", ["tag_id"])

    op.create_table(
        "analysis_results",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "asset_id",
            sa.String(length=36),
            sa.ForeignKey("creative_assets.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("hook", sa.Text(), nullable=False, server_default=""),
        sa.Column("conflict", sa.Text(), nullable=False, server_default=""),
        sa.Column("gameplay", sa.Text(), nullable=False, server_default=""),
        sa.Column("reward", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "characters", postgresql.JSONB(), nullable=False, server_default="[]"
        ),
        sa.Column(
            "environment", postgresql.JSONB(), nullable=False, server_default="[]"
        ),
        sa.Column("emotion", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("tags", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column(
            "creative_name", sa.String(length=512), nullable=False, server_default=""
        ),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("embedding", postgresql.JSONB(), nullable=True),
        *_timestamps(),
    )

    op.create_table(
        "graph_nodes",
        sa.Column("id", sa.String(length=160), primary_key=True),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("label", sa.String(length=1024), nullable=False, server_default=""),
        sa.Column("ref_id", sa.String(length=36), nullable=False),
        *_timestamps(),
    )
    op.create_index("ix_graph_nodes_type", "graph_nodes", ["type"])
    op.create_index("ix_graph_nodes_ref_id", "graph_nodes", ["ref_id"])

    op.create_table(
        "graph_edges",
        sa.Column("id", sa.String(length=360), primary_key=True),
        sa.Column("source", sa.String(length=160), nullable=False),
        sa.Column("target", sa.String(length=160), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        *_timestamps(),
    )
    op.create_index("ix_graph_edges_source", "graph_edges", ["source"])
    op.create_index("ix_graph_edges_target", "graph_edges", ["target"])

    op.create_table(
        "settings",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("key", sa.String(length=128), nullable=False, unique=True),
        sa.Column("value", sa.Text(), nullable=False, server_default=""),
        *_timestamps(),
    )


def downgrade() -> None:
    op.drop_table("settings")
    op.drop_index("ix_graph_edges_target", table_name="graph_edges")
    op.drop_index("ix_graph_edges_source", table_name="graph_edges")
    op.drop_table("graph_edges")
    op.drop_index("ix_graph_nodes_ref_id", table_name="graph_nodes")
    op.drop_index("ix_graph_nodes_type", table_name="graph_nodes")
    op.drop_table("graph_nodes")
    op.drop_table("analysis_results")
    op.drop_index("ix_tag_assignments_tag_id", table_name="tag_assignments")
    op.drop_index("ix_tag_assignments_asset_id", table_name="tag_assignments")
    op.drop_table("tag_assignments")
    op.drop_table("tags")
    op.drop_index("ix_performances_asset_id", table_name="performances")
    op.drop_table("performances")
    op.drop_index(
        "ix_creative_variants_creative_id", table_name="creative_variants"
    )
    op.drop_table("creative_variants")
    op.drop_table("creative_assets")
    op.drop_table("creatives")
    op.drop_table("projects")
