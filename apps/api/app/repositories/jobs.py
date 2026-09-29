"""AnalysisJob persistence + worker claim queries."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import AnalysisJob, CreativeAsset


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class JobRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, job_id: str) -> AnalysisJob | None:
        return self.db.get(AnalysisJob, job_id)

    def get_by_asset(self, asset_id: str) -> AnalysisJob | None:
        return self.db.scalar(
            select(AnalysisJob).where(AnalysisJob.asset_id == asset_id)
        )

    def enqueue(self, asset_id: str, *, available_at: datetime | None = None) -> bool:
        """Insert a queued job; no-op when the asset already has one.

        ``asset_id`` is the idempotency key (unique) — a duplicate enqueue
        (e.g. retried upload request) is silently ignored. Returns True when
        a row was actually inserted.
        """
        stmt = (
            pg_insert(AnalysisJob)
            .values(
                asset_id=asset_id,
                status="queued",
                available_at=available_at or utcnow(),
            )
            .on_conflict_do_nothing(index_elements=["asset_id"])
        )
        result = self.db.execute(stmt)
        self.db.flush()
        return result.rowcount > 0  # type: ignore[union-attr]

    def claim_next(
        self, worker_id: str, *, lease_seconds: int, now: datetime | None = None
    ) -> AnalysisJob | None:
        """Atomically claim the oldest due queued job (SKIP LOCKED).

        Multi-worker safe: concurrent workers never receive the same row.
        The caller's transaction commits the running state before the job
        is handed to the executor.
        """
        now = now or utcnow()
        job = self.db.scalar(
            select(AnalysisJob)
            .where(
                AnalysisJob.status == "queued",
                AnalysisJob.available_at <= now,
            )
            .order_by(AnalysisJob.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if job is None:
            return None
        job.status = "running"
        job.lease_until = now + timedelta(seconds=lease_seconds)
        job.worker_id = worker_id
        self.db.flush()
        return job

    def requeue_expired_leases(self, now: datetime | None = None) -> int:
        """Startup recovery: running jobs whose lease expired → queued."""
        now = now or utcnow()
        result = self.db.execute(
            update(AnalysisJob)
            .where(
                AnalysisJob.status == "running",
                AnalysisJob.lease_until < now,
            )
            .values(status="queued", lease_until=None, worker_id=None)
        )
        self.db.flush()
        return result.rowcount  # type: ignore[return-value]

    def renew_lease(self, job_id: str, *, lease_seconds: int) -> None:
        """Heartbeat: extend the lease of a job still owned and running."""
        self.db.execute(
            update(AnalysisJob)
            .where(AnalysisJob.id == job_id, AnalysisJob.status == "running")
            .values(lease_until=utcnow() + timedelta(seconds=lease_seconds))
        )
        self.db.flush()

    def processing_assets(self) -> list[CreativeAsset]:
        return list(
            self.db.scalars(
                select(CreativeAsset).where(
                    CreativeAsset.analysis_status == "processing"
                )
            ).all()
        )
