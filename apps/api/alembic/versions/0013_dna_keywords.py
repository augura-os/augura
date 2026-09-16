"""add creative_dnas.keywords (识别特征词, JSONB list).

智能建族质量增强（services/family_bootstrap PR3）：LLM 提案为每个家族
产出 5-10 个识别特征词（含常见同义写法/换皮词），确认建族时落库，
供后续归类匹配与前端展示。存量家族为空列表。列类型沿用表内/库内
现有 JSON 先例（analysis_results.characters 等均 JSONB）。

Revision ID: 0013_dna_keywords
Revises: 0012_creative_lifecycle
Create Date: 2026-09-15

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0013_dna_keywords"
down_revision: str | None = "0012_creative_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "creative_dnas",
        sa.Column(
            "keywords",
            JSONB,
            nullable=False,
            server_default="[]",
        ),
    )


def downgrade() -> None:
    op.drop_column("creative_dnas", "keywords")
