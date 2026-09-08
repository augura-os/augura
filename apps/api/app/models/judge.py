"""JudgeSuggestion model — cached LLM pre-adjudications."""

from __future__ import annotations

from sqlalchemy import String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, new_uuid


class JudgeSuggestion(TimestampMixin, Base):
    __tablename__ = "judge_suggestions"
    __table_args__ = (
        UniqueConstraint("kind", "left_id", "right_id", name="uq_judge_subject"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    # "dna_assign"（归族建议，right_id 为 DNA id）
    # "merge_pair"（合并预裁，right_id 为对方 creative id）
    # "derivation-factor"（裂变因子归因/疑似误链建议，right_id 为空）
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    left_id: Mapped[str] = mapped_column(String(36), nullable=False)
    right_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    verdict: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    votes: Mapped[int] = mapped_column(default=0)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
