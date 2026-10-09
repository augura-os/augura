"""performances.optimization_type: 优化方式维度（install/aeo/vo）+ 存量回填。

历史 Excel 自带「优化方式」列且所有行的 raw JSONB 完整保留，因此纯回填建
维度、零重新导入。归一映射（与 services/excel.normalize_objective 同口径，
SQL 是冻结副本）：安装/install/installs/mai → install，aeo → aeo，
vo/value optimization → vo，其余与 NULL → NULL（未知 = 回落 aeo 判定口径，
见 services/recommendation_rules.OBJECTIVE_PROFILES）。

Revision ID: 0020_perf_optimization_type
Revises: 0019_verdict_snapshot_labels
Create Date: 2026-10-09

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0020_perf_optimization_type"
down_revision: str | None = "0019_verdict_snapshot_labels"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "performances",
        sa.Column("optimization_type", sa.String(16), nullable=True),
    )
    op.execute(
        """
        UPDATE performances SET optimization_type = CASE lower(trim(raw->>'优化方式'))
            WHEN '安装' THEN 'install'
            WHEN 'install' THEN 'install'
            WHEN 'installs' THEN 'install'
            WHEN 'mai' THEN 'install'
            WHEN 'aeo' THEN 'aeo'
            WHEN 'vo' THEN 'vo'
            WHEN 'value optimization' THEN 'vo'
            ELSE NULL END
        """
    )


def downgrade() -> None:
    op.drop_column("performances", "optimization_type")
