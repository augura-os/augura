"""widen judge_suggestions.kind 16 -> 32.

"derivation-factor"（裂变因子归因建议，17 字符）超出了 0008 建表时的
String(16)，review_derivations.py 写建议时直接 StringDataRightTruncation。
verdict 列已是 String(32)，可容纳最长因子 "live-action-vs-animation"（24）。

Revision ID: 0010_judge_kind_len
Revises: 0009_split_rulings
Create Date: 2026-08-25

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010_judge_kind_len"
down_revision: str | None = "0009_split_rulings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("judge_suggestions", "kind", type_=sa.String(length=32))


def downgrade() -> None:
    op.alter_column("judge_suggestions", "kind", type_=sa.String(length=16))
