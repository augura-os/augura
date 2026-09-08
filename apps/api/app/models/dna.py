"""CreativeDNA model (design-principles: Pattern layer).

DNA = 钩子原型 × 核心机制 × 叙事结构 — the family level above Creative
(creative-dna-registry.md). One Creative belongs to at most one DNA;
assignments are human-confirmed (registry §2 rule 2) and audited via
edit_logs.
"""

from __future__ import annotations

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, new_uuid


class CreativeDNA(TimestampMixin, Base):
    __tablename__ = "creative_dnas"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    # D01, D02, ... — stable human-facing code from the registry.
    code: Mapped[str] = mapped_column(String(8), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    hook_prototype: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    core_mechanic: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    narrative_structure: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # active / merged / archived
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
