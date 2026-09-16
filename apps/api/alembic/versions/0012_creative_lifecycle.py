"""add creatives.lifecycle_state (active/watch/archived).

创意评分与生命周期管理（services/creative_score + services/lifecycle）：
评分计算即得不落库；状态落库驱动视图过滤。存量全部 active。
归档不静默——score < 阈值且 idle 超期的只进收件箱"建议归档"，
人工确认后才置 archived（Human > AI）。

Revision ID: 0012_creative_lifecycle
Revises: 0011_factor_reviewed
Create Date: 2026-09-02

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012_creative_lifecycle"
down_revision: str | None = "0011_factor_reviewed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "creatives",
        sa.Column(
            "lifecycle_state",
            sa.String(16),
            nullable=False,
            server_default="active",
        ),
    )


def downgrade() -> None:
    op.drop_column("creatives", "lifecycle_state")
