"""add analysis_results.variant_factors (boundary-rules §3.3 reskin factors).

The AI analysis output gains a ``variant_factors`` list: which reskin
factors (前贴 / 语言 / 画幅 / 角色换皮 / 口播文案 / 奖励换皮 / 品牌尾页 /
真人动画贴) this execution carries. They decide only the Variant layer,
never Creative clustering (boundary-rules §2 Q4, §3.3).

Revision ID: 0005_variant_factors
Revises: 0004_tag_layer_parent
Create Date: 2026-07-21

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0005_variant_factors"
down_revision: str | None = "0004_tag_layer_parent"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "analysis_results",
        sa.Column(
            "variant_factors",
            JSONB,
            nullable=False,
            server_default="[]",
        ),
    )


def downgrade() -> None:
    op.drop_column("analysis_results", "variant_factors")
