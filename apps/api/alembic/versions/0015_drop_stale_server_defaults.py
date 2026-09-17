"""drop stale server_defaults left behind by earlier migrations (P2-21).

0012 为 creatives.lifecycle_state 加列时留了 server_default "active"，
0013 为 creative_dnas.keywords 加列时留了 server_default "[]"。两者都是
为了回填存量行的一次性默认值，应用层模型已各自声明 default，继续在
数据库层保留 server_default 会让绕过 ORM 的写入拿到与应用语义脱节的
隐式默认值。本迁移仅移除这两处 server_default，不改任何存量数据。

0014 的 creatives.embedding_count server_default "0" 保持不动（计数列
语义允许库级默认）。

Revision ID: 0015_drop_stale_server_defaults
Revises: 0014_creative_embedding_count
Create Date: 2026-09-16

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0015_drop_stale_server_defaults"
down_revision: str | None = "0014_creative_embedding_count"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("creatives", "lifecycle_state", server_default=None)
    op.alter_column("creative_dnas", "keywords", server_default=None)


def downgrade() -> None:
    op.alter_column("creatives", "lifecycle_state", server_default="active")
    op.alter_column("creative_dnas", "keywords", server_default="[]")
