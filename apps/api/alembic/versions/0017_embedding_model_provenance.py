"""add embedding_model columns (per-row vector provenance).

三处向量存储列（analysis_results.embedding、creative_variants.embedding、
creatives.representative_embedding）此前是裸 JSONB 向量，导出/遥测/排障
无法确认单条向量出自哪个模型。行级 ``embedding_model`` 记录产出该向量的
模型 id（``local:BAAI/...`` / ``provider:text-embedding-...``，与 settings
表 embedding_model_active 同格式）；nullable、无 server_default——存量
行 NULL 表示"未知模型"。全局一致性机制不变：切换模型仍整体失效清空
（services/embedding.invalidate_embeddings 同时清戳）。

Revision ID: 0017_embedding_model_provenance
Revises: 0016_analysis_jobs
Create Date: 2026-09-30

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017_embedding_model_provenance"
down_revision: str | None = "0016_analysis_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "analysis_results",
        sa.Column("embedding_model", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "creative_variants",
        sa.Column("embedding_model", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "creatives",
        sa.Column("embedding_model", sa.String(length=128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("creatives", "embedding_model")
    op.drop_column("creative_variants", "embedding_model")
    op.drop_column("analysis_results", "embedding_model")
