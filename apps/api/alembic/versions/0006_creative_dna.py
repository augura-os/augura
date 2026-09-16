"""add creative_dnas table + creatives.dna_id (Pattern layer).

Lands the DNA layer (creative-dna-registry.md §4 draft) as a real table:
DNA = 钩子原型 × 核心机制 × 叙事结构, the family above Creative
(design-principles §3: Pattern layer). Backfill of the 12 families and
21 creative assignments is done by ``scripts/backfill_dnas.py``.

Revision ID: 0006_creative_dna
Revises: 0005_variant_factors
Create Date: 2026-07-22

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_creative_dna"
down_revision: str | None = "0005_variant_factors"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "creative_dnas",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("code", sa.String(length=8), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("hook_prototype", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("core_mechanic", sa.String(length=64), nullable=False, server_default=""),
        sa.Column(
            "narrative_structure", sa.String(length=128), nullable=False, server_default=""
        ),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
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
        sa.UniqueConstraint("code", name="uq_creative_dnas_code"),
    )
    op.add_column("creatives", sa.Column("dna_id", sa.String(length=36), nullable=True))
    op.create_foreign_key(
        "fk_creatives_dna_id",
        "creatives",
        "creative_dnas",
        ["dna_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_creatives_dna_id", "creatives", type_="foreignkey")
    op.drop_column("creatives", "dna_id")
    op.drop_table("creative_dnas")
