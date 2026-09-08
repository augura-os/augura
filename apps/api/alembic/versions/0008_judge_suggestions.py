"""add judge_suggestions (LLM 预裁结果缓存).

Pairwise/归族判定结果按 (kind, left_id, right_id) 缓存，收件箱只读不现算；
自动级判定由 scripts/judge_candidates.py 批量执行并留痕。

Revision ID: 0008_judge_suggestions
Revises: 0007_variant_derivations
Create Date: 2026-07-28

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_judge_suggestions"
down_revision: str | None = "0007_variant_derivations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "judge_suggestions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("left_id", sa.String(length=36), nullable=False),
        sa.Column("right_id", sa.String(length=36), nullable=True),
        sa.Column("verdict", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("votes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
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
        sa.UniqueConstraint("kind", "left_id", "right_id", name="uq_judge_subject"),
    )


def downgrade() -> None:
    op.drop_table("judge_suggestions")
