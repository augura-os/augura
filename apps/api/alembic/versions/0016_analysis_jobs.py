"""add analysis_jobs table (durable analysis queue).

素材分析从 FastAPI BackgroundTasks 改为 PostgreSQL durable job + 独立
worker 进程：批量上传时 API 进程不再持有分析线程，重启后 queued/running
（租约过期）任务可恢复。asset_id 唯一 = 幂等键（一个素材同时最多一个
job）；(status, available_at) 复合索引服务 worker 的抢单查询。

Revision ID: 0016_analysis_jobs
Revises: 0015_drop_stale_server_defaults
Create Date: 2026-09-28

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0016_analysis_jobs"
down_revision: str | None = "0015_drop_stale_server_defaults"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "analysis_jobs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("asset_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_id", sa.String(length=128), nullable=True),
        sa.Column("stage", sa.String(length=32), nullable=True),
        sa.Column("error_code", sa.String(length=32), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
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
        sa.ForeignKeyConstraint(
            ["asset_id"], ["creative_assets.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("asset_id", name="uq_analysis_jobs_asset_id"),
    )
    op.create_index(
        "ix_analysis_jobs_status_available",
        "analysis_jobs",
        ["status", "available_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_analysis_jobs_status_available", table_name="analysis_jobs")
    op.drop_table("analysis_jobs")
