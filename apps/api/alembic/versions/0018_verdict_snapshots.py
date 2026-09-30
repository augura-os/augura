"""add verdict_snapshots table (decision audit trail).

规则引擎产出的 Verdict 此前从不落库，阈值/规则变更后无法回答"当时为什么
这么判"。本表在有状态刷新（POST /creatives/recommendations/refresh、投放
数据导入）时把每次判定的输入（thresholds/metrics/rules_version）与输出
（action/reason_code/params/reasons/supplementary/priority/confidence）
整体落库；content_hash 对判定关键字段做 sha256，同一 creative 内容不变
不重复插行（去重逻辑在 services/verdict_snapshots），历史可对比且表不
膨胀。creative 删除时级联清理（快照是 creative 的附属审计数据）。

Revision ID: 0018_verdict_snapshots
Revises: 0017_embedding_model_provenance
Create Date: 2026-09-30

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0018_verdict_snapshots"
down_revision: str | None = "0017_embedding_model_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "verdict_snapshots",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("creative_id", sa.String(length=36), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column("params", JSONB, nullable=False, server_default="{}"),
        sa.Column("reasons", JSONB, nullable=False, server_default="[]"),
        sa.Column("supplementary", JSONB, nullable=False, server_default="[]"),
        sa.Column("priority_dollars", sa.Float(), nullable=False, server_default="0"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("rules_version", sa.String(length=32), nullable=False),
        sa.Column("thresholds", JSONB, nullable=False, server_default="{}"),
        sa.Column("metrics", JSONB, nullable=False, server_default="{}"),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["creative_id"], ["creatives.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_verdict_snapshots_creative_id", "verdict_snapshots", ["creative_id"]
    )
    op.create_index(
        "ix_verdict_snapshots_content_hash", "verdict_snapshots", ["content_hash"]
    )


def downgrade() -> None:
    op.drop_index("ix_verdict_snapshots_content_hash", table_name="verdict_snapshots")
    op.drop_index("ix_verdict_snapshots_creative_id", table_name="verdict_snapshots")
    op.drop_table("verdict_snapshots")
