"""add creatives.embedding_count (P0-3 代表向量均值口径修复).

语义 = 实际参与过 representative_embedding 均值的向量条数。存量
running_mean 错用全部 variant 数（pipeline.py 调用处传 variant_count），
历史 variant 无向量时 count 偏大、旧均值被过度加权（E0 漂移复现：
5 variant 仅 3 有向量时族中心偏移 31.83°）。embedding 常态化后
"成员无向量"从异常变日常，必须修。

回填口径：按"当前族内有向量的 variant 条数"初始化——与该列语义一致；
存量代表向量本身的历史漂移无法回溯修正（E2 回填按成员重算均值时会
一并纠正）。

Revision ID: 0014_creative_embedding_count
Revises: 0013_dna_keywords
Create Date: 2026-09-16

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014_creative_embedding_count"
down_revision: str | None = "0013_dna_keywords"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "creatives",
        sa.Column(
            "embedding_count",
            sa.Integer,
            nullable=False,
            server_default="0",
        ),
    )
    # 回填：有向量的成员 variant 条数（representative_embedding 为空的族
    # 自然是 0）
    op.execute(
        """
        UPDATE creatives
        SET embedding_count = (
            SELECT count(*) FROM creative_variants v
            WHERE v.creative_id = creatives.id AND v.embedding IS NOT NULL
        )
        """
    )


def downgrade() -> None:
    op.drop_column("creatives", "embedding_count")
