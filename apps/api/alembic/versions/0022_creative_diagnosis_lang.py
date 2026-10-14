"""creative_diagnoses 按语言分行：定性报告从「每素材每 prompt 版本一份」
改为「每素材每 prompt 版本每语言一份」。

报告语言永远跟随界面语言：切语言后该语言无报告则由前端自动生成，因此
幂等 upsert 键从 (asset_id, prompt_version) 扩为
(asset_id, prompt_version, lang)——同素材同版本下 zh/en 两行共存互不
覆盖。存量行的语言此前只记在 audience_context 快照里（'en' 或缺省），
回填时 COALESCE(audience_context->>'lang', 'zh') 归位。

Revision ID: 0022_creative_diagnosis_lang
Revises: 0021_creative_diagnoses
Create Date: 2026-10-14

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0022_creative_diagnosis_lang"
down_revision: str | None = "0021_creative_diagnoses"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "creative_diagnoses",
        sa.Column(
            "lang",
            sa.String(length=8),
            nullable=False,
            server_default="zh",
        ),
    )
    # 存量英文行的 lang 只存在于 audience_context 快照里，回填归位
    op.execute(
        "UPDATE creative_diagnoses "
        "SET lang = COALESCE(audience_context->>'lang', 'zh')"
    )
    op.drop_constraint(
        "uq_creative_diagnoses_asset_prompt", "creative_diagnoses", type_="unique"
    )
    op.create_unique_constraint(
        "uq_creative_diagnoses_asset_prompt_lang",
        "creative_diagnoses",
        ["asset_id", "prompt_version", "lang"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_creative_diagnoses_asset_prompt_lang", "creative_diagnoses", type_="unique"
    )
    op.create_unique_constraint(
        "uq_creative_diagnoses_asset_prompt",
        "creative_diagnoses",
        ["asset_id", "prompt_version"],
    )
    op.drop_column("creative_diagnoses", "lang")
