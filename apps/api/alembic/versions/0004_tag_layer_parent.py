"""add tags.layer / tags.parent (five-layer tag ontology).

Lands the five-layer ontology of boundary-rules §5.1 on the tags table:
``layer`` is one of hook / mechanic / character / reward / meta, with
"setting" as a transitional bucket for legacy scene tags; ``parent`` holds
the parent category for specific character values. Backfill of the 90
existing tags is done by ``scripts/backfill_tag_layers.py`` (data, kept
out of the migration so the mapping stays reviewable in one place).

Revision ID: 0004_tag_layer_parent
Revises: 0003_edit_log_engine_version
Create Date: 2026-07-21

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_tag_layer_parent"
down_revision: str | None = "0003_edit_log_engine_version"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tags", sa.Column("layer", sa.String(length=16), nullable=True))
    op.add_column("tags", sa.Column("parent", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("tags", "parent")
    op.drop_column("tags", "layer")
