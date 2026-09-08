"""Tag + TagAssignment models (contract §6)."""

from __future__ import annotations

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, new_uuid


class Tag(TimestampMixin, Base):
    __tablename__ = "tags"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    # Five-layer tag ontology (boundary-rules §5.1):
    # hook / mechanic / character / reward / meta; "setting" is a transitional
    # bucket for legacy scene tags pending governance. NULL = not yet layered.
    layer: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Parent category, required for specific character values (goblin → merchant).
    parent: Mapped[str | None] = mapped_column(String(255), nullable=True)


class TagAssignment(TimestampMixin, Base):
    __tablename__ = "tag_assignments"
    __table_args__ = (
        UniqueConstraint("asset_id", "tag_id", name="uq_tag_assignment"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    asset_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("creative_assets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tag_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tags.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
