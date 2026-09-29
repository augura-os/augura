"""AnalysisJob — durable queue row for post-upload AI analysis.

One row per asset (``asset_id`` unique = idempotency key). The API enqueues
at upload time; the standalone worker process (``python -m app.worker``)
claims rows with ``SELECT ... FOR UPDATE SKIP LOCKED`` and leases them.

status: queued | running | done | failed | dead | cancelled | waiting_user
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, new_uuid

JOB_STATUSES = (
    "queued",
    "running",
    "done",
    "failed",
    "dead",
    "cancelled",
    "waiting_user",
)
# Terminal states the retry endpoint is allowed to reset back to queued.
JOB_RETRYABLE_STATUSES = ("failed", "dead", "cancelled", "waiting_user")


class AnalysisJob(TimestampMixin, Base):
    __tablename__ = "analysis_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    asset_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("creative_assets.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    lease_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Pipeline checkpoint: frames / vision / cluster / judge / done.
    stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Column is named "metadata" in SQL; the attribute avoids SQLAlchemy's
    # reserved DeclarativeBase.metadata.
    job_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )

    __table_args__ = (
        Index("ix_analysis_jobs_status_available", "status", "available_at"),
    )
