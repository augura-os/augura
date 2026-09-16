"""add variant_derivations (DERIVED_FROM 裂变边 / lightweight experiment).

Postgres is the source of truth (P06: auditable, backup-able — unlike
SIMILAR_TO which lives only in Neo4j). Synced to Neo4j as
(:Variant)-[:DERIVED_FROM {factor}]->(:Variant) and to the SQL graph mirror.

Revision ID: 0007_variant_derivations
Revises: 0006_creative_dna
Create Date: 2026-07-22

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_variant_derivations"
down_revision: str | None = "0006_creative_dna"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "variant_derivations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("source_variant_id", sa.String(length=36), nullable=False),
        sa.Column("target_variant_id", sa.String(length=36), nullable=False),
        sa.Column("factor", sa.String(length=32), nullable=False, server_default="unknown"),
        sa.Column("verdict", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
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
        sa.ForeignKeyConstraint(
            ["source_variant_id"],
            ["creative_variants.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_variant_id"],
            ["creative_variants.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "source_variant_id", "target_variant_id", name="uq_derivation_pair"
        ),
        sa.CheckConstraint(
            "source_variant_id != target_variant_id", name="ck_derivation_not_self"
        ),
    )
    op.create_index(
        "ix_variant_derivations_source", "variant_derivations", ["source_variant_id"]
    )
    op.create_index(
        "ix_variant_derivations_target", "variant_derivations", ["target_variant_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_variant_derivations_target", table_name="variant_derivations")
    op.drop_index("ix_variant_derivations_source", table_name="variant_derivations")
    op.drop_table("variant_derivations")
