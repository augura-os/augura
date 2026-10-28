"""Observation-pair workflow state and append-only evaluation evidence."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, new_uuid


class ObservationPair(TimestampMixin, Base):
    __tablename__ = "observation_pairs"
    __table_args__ = (
        UniqueConstraint("creative_id_a", "creative_id_b", name="uq_observation_pair"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    creative_id_a: Mapped[str] = mapped_column(
        String(36), ForeignKey("creatives.id", ondelete="CASCADE"), index=True
    )
    creative_id_b: Mapped[str] = mapped_column(
        String(36), ForeignKey("creatives.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="observing", server_default="observing", index=True
    )
    registered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    evidence_start_at: Mapped[date | None] = mapped_column(Date)
    last_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_evaluation_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    waiting_reason: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="awaiting_evaluation",
        server_default="awaiting_evaluation",
    )
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    resolution_type: Mapped[str | None] = mapped_column(String(32))
    evidence: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    auto_disabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )


class ObservationEvaluation(TimestampMixin, Base):
    __tablename__ = "observation_evaluations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    pair_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("observation_pairs.id", ondelete="CASCADE"), index=True
    )
    data_cutoff: Mapped[date | None] = mapped_column(Date)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    output: Mapped[str] = mapped_column(String(24), nullable=False)
    shadow: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    evidence: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
