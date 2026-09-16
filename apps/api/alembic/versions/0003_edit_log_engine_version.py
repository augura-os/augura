"""add edit_logs table + analysis_results.engine_version.

edit_logs is the human-override audit trail (AI Constitution §5: every
correction is captured as learning material). engine_version records
which engine produced each analysis (§11: versioned AI output).
Existing manual analyses are backfilled as "manual:kimi-work".

Revision ID: 0003_edit_log_engine_version
Revises: 0002_representative_text
Create Date: 2026-07-17

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_edit_log_engine_version"
down_revision: str | None = "0002_representative_text"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "edit_logs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("entity_type", sa.String(length=16), nullable=False),
        sa.Column("entity_id", sa.String(length=36), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("field", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("old_value", sa.Text(), nullable=False, server_default=""),
        sa.Column("new_value", sa.Text(), nullable=False, server_default=""),
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
    )
    op.create_index("ix_edit_logs_entity_type", "edit_logs", ["entity_type"])
    op.create_index("ix_edit_logs_entity_id", "edit_logs", ["entity_id"])

    op.add_column(
        "analysis_results",
        sa.Column(
            "engine_version",
            sa.String(length=128),
            nullable=False,
            server_default="",
        ),
    )
    # The three analyses written through the manual in-the-loop flow.
    op.execute(
        "UPDATE analysis_results SET engine_version = 'manual:kimi-work' "
        "WHERE engine_version = ''"
    )


def downgrade() -> None:
    op.drop_column("analysis_results", "engine_version")
    op.drop_index("ix_edit_logs_entity_id", table_name="edit_logs")
    op.drop_index("ix_edit_logs_entity_type", table_name="edit_logs")
    op.drop_table("edit_logs")
