"""add split_rulings (观察对结案的机器可读裁决).

结案维持拆分时自动写入；review 候选排除 + merge_guard 实时拦截都读它。
与手写 docs/case-rulings.json 互补：JSON 是文档化案例，本表是自动化沉淀。

Revision ID: 0009_split_rulings
Revises: 0008_judge_suggestions
Create Date: 2026-07-30

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009_split_rulings"
down_revision: str | None = "0008_judge_suggestions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "split_rulings",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name_a", sa.String(length=512), nullable=False),
        sa.Column("name_b", sa.String(length=512), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "source",
            sa.String(length=32),
            nullable=False,
            server_default="inbox_close",
        ),
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
        sa.UniqueConstraint("name_a", "name_b", name="uq_split_ruling_pair"),
    )


def downgrade() -> None:
    op.drop_table("split_rulings")
