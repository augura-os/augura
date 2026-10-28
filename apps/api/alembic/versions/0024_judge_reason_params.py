"""judge_suggestions 增加 reason_params（P1c B1 结构化判定解释）。

merge-judge-v2 起，合并预裁的四层 relation、core/variant 维度判定、
三视角票明细、model_id/prompt_version 落此列——支撑 B2 阈值回测、
审计页解释展示与按模型版本整批回滚。

Revision ID: 0024_judge_reason_params
Revises: 0023_idempotency_keys
Create Date: 2026-10-21

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0024_judge_reason_params"
down_revision: str | None = "0023_idempotency_keys"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "judge_suggestions",
        sa.Column(
            "reason_params", JSONB, nullable=False, server_default="{}"
        ),
    )


def downgrade() -> None:
    op.drop_column("judge_suggestions", "reason_params")
