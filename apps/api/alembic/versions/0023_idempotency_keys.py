"""幂等键存储表（P0a 批量接口契约 §2.1）。

Idempotency-Key 作用域 = 单批：同 key 同 endpoint 的重复提交直接返回
首次存储的 response_json，不重复执行。TTL 24h，读取时惰性清理由
app.services.idempotency 负责，表上不加定时任务。

Revision ID: 0023_idempotency_keys
Revises: 0022_creative_diagnosis_lang
Create Date: 2026-10-20

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0023_idempotency_keys"
down_revision: str | None = "0022_creative_diagnosis_lang"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "idempotency_keys",
        sa.Column("key", sa.String(length=128), primary_key=True),
        sa.Column("endpoint", sa.String(length=64), nullable=False),
        sa.Column("response_json", JSONB, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("idempotency_keys")
