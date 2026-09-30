"""VerdictSnapshot persistence."""

from __future__ import annotations

from datetime import datetime
from typing import Sequence

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models import VerdictSnapshot


class VerdictSnapshotRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def latest_for_creative(self, creative_id: str) -> VerdictSnapshot | None:
        return self.db.scalar(
            select(VerdictSnapshot)
            .where(VerdictSnapshot.creative_id == creative_id)
            .order_by(VerdictSnapshot.computed_at.desc())
            .limit(1)
        )

    def latest_hashes(self) -> dict[str, tuple[datetime, str]]:
        """creative_id → 最新快照的 (computed_at, content_hash)（去重判定用）。

        写入方保证同 creative 的 computed_at 严格递增（services/
        verdict_snapshots 的单调性护栏），max(computed_at) 回接无并列歧义。
        """
        latest = (
            select(
                VerdictSnapshot.creative_id,
                func.max(VerdictSnapshot.computed_at).label("computed_at"),
            )
            .group_by(VerdictSnapshot.creative_id)
            .subquery()
        )
        stmt = select(
            VerdictSnapshot.creative_id,
            VerdictSnapshot.computed_at,
            VerdictSnapshot.content_hash,
        ).join(
            latest,
            and_(
                VerdictSnapshot.creative_id == latest.c.creative_id,
                VerdictSnapshot.computed_at == latest.c.computed_at,
            ),
        )
        return {
            creative_id: (computed_at, content_hash)
            for creative_id, computed_at, content_hash in self.db.execute(stmt)
        }

    def bulk_insert(self, snapshots: Sequence[VerdictSnapshot]) -> None:
        self.db.add_all(snapshots)
        self.db.flush()
