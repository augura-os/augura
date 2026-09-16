"""add variant_derivations.factor_reviewed (人工已确认因子).

裂变边创建即自动测量复核后，必须区分"机器猜的因子"和"人拍板的因子"：
factor_reviewed=True 的边，复核（自动/批量）直接跳过，不修正也不重新
建议——否则每次批量重跑都会把人工裁决过的边重新拎进收件箱，人机打架。
置位时机：POST 创建时显式给了非 unknown 因子；PUT 人工改因子（收件箱
"采纳"也走 PUT，同样置位）。

Revision ID: 0011_factor_reviewed
Revises: 0010_judge_kind_len
Create Date: 2026-08-25

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011_factor_reviewed"
down_revision: str | None = "0010_judge_kind_len"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "variant_derivations",
        sa.Column(
            "factor_reviewed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("variant_derivations", "factor_reviewed")
