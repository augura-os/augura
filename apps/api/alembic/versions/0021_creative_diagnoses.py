"""creative_diagnoses: Creative Lab 定性诊断结果表（M1）。

LLM 定性报告的输入快照（observation_snapshot / product_facts_snapshot /
audience_context）与六要素输出（output）整体落库，写完冻结——可复盘
"当时看到了什么"。(asset_id, prompt_version) 唯一 = 幂等 upsert 键：
同素材同 prompt 版本重诊断覆盖写，prompt 升级（services/lab_prompts
LAB_PROMPT_VERSION bump）另起新行、旧版本行保留可回归对比。素材删除
时级联清理（诊断是素材的附属产物）。

Revision ID: 0021_creative_diagnoses
Revises: 0020_perf_optimization_type
Create Date: 2026-10-13

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0021_creative_diagnoses"
down_revision: str | None = "0020_perf_optimization_type"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "creative_diagnoses",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("asset_id", sa.String(length=36), nullable=False),
        sa.Column("observation_snapshot", JSONB, nullable=False),
        sa.Column("product_facts_snapshot", JSONB, nullable=True),
        sa.Column("audience_context", JSONB, nullable=True),
        sa.Column("output", JSONB, nullable=False),
        sa.Column("evidence_label", sa.String(length=32), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("engine_version", sa.String(length=128), nullable=False),
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
        sa.UniqueConstraint(
            "asset_id", "prompt_version", name="uq_creative_diagnoses_asset_prompt"
        ),
    )
    op.create_index(
        "ix_creative_diagnoses_asset_id", "creative_diagnoses", ["asset_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_creative_diagnoses_asset_id", table_name="creative_diagnoses")
    op.drop_table("creative_diagnoses")
