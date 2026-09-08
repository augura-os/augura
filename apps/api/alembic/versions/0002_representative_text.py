"""add creatives.representative_text — text signature for the
embedding-free fallback clustering (Kimi/Moonshot has no embeddings API).

Revision ID: 0002_representative_text
Revises: 0001_initial
Create Date: 2026-07-17

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_representative_text"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "creatives",
        sa.Column(
            "representative_text",
            sa.String(length=2048),
            nullable=False,
            server_default="",
        ),
    )


def downgrade() -> None:
    op.drop_column("creatives", "representative_text")
