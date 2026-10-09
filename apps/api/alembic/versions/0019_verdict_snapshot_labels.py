"""verdict_snapshots.labels: winner-label array on decision snapshots.

赢家七分类标签（services/recommendation_rules.LabelCode）是 Verdict 的附加
信息。随 rules-v2 起快照落库时一并存入，且纳入 content_hash——标签变化
（判定输入不变、基准或窗口数据变化）同样产生新快照行，保证"当时这条创意
被贴了哪个标签"可回溯。已有行由 server_default '[]' 补齐（旧快照没有标签
概念，空数组即"无标签"，语义安全）。

Revision ID: 0019_verdict_snapshot_labels
Revises: 0018_verdict_snapshots
Create Date: 2026-10-09

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0019_verdict_snapshot_labels"
down_revision: str | None = "0018_verdict_snapshots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "verdict_snapshots",
        sa.Column("labels", JSONB, nullable=True, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("verdict_snapshots", "labels")
