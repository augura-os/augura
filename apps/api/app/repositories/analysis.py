"""AnalysisResult persistence."""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import AnalysisResult
from app.schemas.analysis import AnalysisPayload


class AnalysisRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_asset(self, asset_id: str) -> AnalysisResult | None:
        return self.db.scalar(
            select(AnalysisResult).where(AnalysisResult.asset_id == asset_id)
        )

    def upsert(
        self,
        asset_id: str,
        payload: AnalysisPayload,
        embedding: list[float] | None = None,
        engine_version: str | None = None,
    ) -> AnalysisResult:
        """Create or update the analysis row for an asset.

        ``embedding=None`` keeps the previously stored embedding (used by the
        human-edit flow where re-embedding is a separate step).
        ``engine_version=None`` keeps the origin engine (human edits must not
        rewrite which engine produced the analysis).
        """
        result = self.get_by_asset(asset_id)
        if result is None:
            result = AnalysisResult(asset_id=asset_id)
            self.db.add(result)
        result.summary = payload.summary
        result.hook = payload.hook
        result.conflict = payload.conflict
        result.gameplay = payload.gameplay
        result.reward = payload.reward
        result.characters = list(payload.characters)
        result.environment = list(payload.environment)
        result.emotion = list(payload.emotion)
        result.tags = list(payload.tags)
        result.variant_factors = list(payload.variant_factors)
        result.creative_name = payload.creative_name
        result.confidence = payload.confidence
        if embedding is not None:
            result.embedding = embedding
        if engine_version is not None:
            result.engine_version = engine_version
        self.db.flush()
        return result

    def set_embedding(self, result: AnalysisResult, embedding: list[float]) -> None:
        result.embedding = embedding
        self.db.flush()

    def delete_for_asset(self, asset_id: str) -> None:
        self.db.execute(
            delete(AnalysisResult).where(AnalysisResult.asset_id == asset_id)
        )
        self.db.flush()
