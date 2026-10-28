"""Add observation-pair lifecycle, evaluation evidence, and stable ruling IDs.

Revision ID: 0025_observation_lifecycle
Revises: 0024_judge_reason_params
Create Date: 2026-10-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0025_observation_lifecycle"
down_revision: str | None = "0024_judge_reason_params"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "observation_pairs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("creative_id_a", sa.String(length=36), nullable=False),
        sa.Column("creative_id_b", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="observing", nullable=False),
        sa.Column("registered_at", sa.DateTime(timezone=True)),
        sa.Column("evidence_start_at", sa.Date()),
        sa.Column("last_evaluated_at", sa.DateTime(timezone=True)),
        sa.Column("next_evaluation_at", sa.DateTime(timezone=True)),
        sa.Column(
            "waiting_reason",
            sa.String(length=64),
            server_default="awaiting_evaluation",
            nullable=False,
        ),
        sa.Column("rule_version", sa.String(length=64), server_default="", nullable=False),
        sa.Column("resolution_type", sa.String(length=32)),
        sa.Column("evidence", JSONB, server_default="{}", nullable=False),
        sa.Column("auto_disabled", sa.Boolean(), server_default="false", nullable=False),
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
        sa.ForeignKeyConstraint(["creative_id_a"], ["creatives.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["creative_id_b"], ["creatives.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("creative_id_a", "creative_id_b", name="uq_observation_pair"),
    )
    op.create_index("ix_observation_pairs_creative_id_a", "observation_pairs", ["creative_id_a"])
    op.create_index("ix_observation_pairs_creative_id_b", "observation_pairs", ["creative_id_b"])
    op.create_index("ix_observation_pairs_status", "observation_pairs", ["status"])
    op.create_index(
        "ix_observation_pairs_next_evaluation_at",
        "observation_pairs",
        ["next_evaluation_at"],
    )

    op.create_table(
        "observation_evaluations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("pair_id", sa.String(length=36), nullable=False),
        sa.Column("data_cutoff", sa.Date()),
        sa.Column("input_hash", sa.String(length=64), server_default="", nullable=False),
        sa.Column("output", sa.String(length=24), nullable=False),
        sa.Column("shadow", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("rule_version", sa.String(length=64), server_default="", nullable=False),
        sa.Column("evidence", JSONB, server_default="{}", nullable=False),
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
        sa.ForeignKeyConstraint(["pair_id"], ["observation_pairs.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_observation_evaluations_pair_id", "observation_evaluations", ["pair_id"])

    op.add_column("split_rulings", sa.Column("creative_id_a", sa.String(length=36)))
    op.add_column("split_rulings", sa.Column("creative_id_b", sa.String(length=36)))
    op.add_column(
        "split_rulings",
        sa.Column("ruling_type", sa.String(length=32), server_default="legacy", nullable=False),
    )
    op.create_foreign_key(
        "fk_split_rulings_creative_a",
        "split_rulings",
        "creatives",
        ["creative_id_a"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_split_rulings_creative_b",
        "split_rulings",
        "creatives",
        ["creative_id_b"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_split_rulings_creative_b", "split_rulings", type_="foreignkey")
    op.drop_constraint("fk_split_rulings_creative_a", "split_rulings", type_="foreignkey")
    op.drop_column("split_rulings", "ruling_type")
    op.drop_column("split_rulings", "creative_id_b")
    op.drop_column("split_rulings", "creative_id_a")
    op.drop_table("observation_evaluations")
    op.drop_table("observation_pairs")
