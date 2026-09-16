"""Creative + CreativeVariant models (contract §5/§6)."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, new_uuid


class Creative(TimestampMixin, Base):
    __tablename__ = "creatives"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    project_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    # Representative embedding used for cosine clustering (§5).
    representative_embedding: Mapped[list[float] | None] = mapped_column(
        JSONB, nullable=True
    )
    # 实际参与过 representative_embedding 均值的向量条数（P0-3 口径：
    # 不是全部 variant 数——历史无向量 variant 不参与均值）。
    embedding_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # Text signature (creative_name + tags) for the embedding-free fallback
    # clustering used when the AI provider has no embeddings endpoint.
    representative_text: Mapped[str] = mapped_column(
        String(2048), nullable=False, default="", server_default=""
    )
    # Pattern-layer family (creative-dna-registry.md); NULL = unassigned.
    dna_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("creative_dnas.id", ondelete="SET NULL"),
        nullable=True,
    )
    # 生命周期（services/lifecycle）：active（默认）/ watch（低分观察）/
    # archived（人工确认归档，图谱与合并候选默认隐藏，可恢复，绝不删数据）
    lifecycle_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", server_default="active"
    )


class CreativeVariant(TimestampMixin, Base):
    __tablename__ = "creative_variants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    creative_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("creatives.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # One uploaded asset is wrapped by exactly one variant (§5.4).
    asset_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("creative_assets.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    name: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    embedding: Mapped[list[float] | None] = mapped_column(JSONB, nullable=True)
