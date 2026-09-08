"""Asset persistence (CreativeAsset) plus list queries joining creatives."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AnalysisResult, Creative, CreativeAsset, CreativeVariant


class AssetRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        *,
        filename: str,
        file_type: str,
        mime_type: str,
        storage_key: str,
        size_bytes: int,
        analysis_status: str,
    ) -> CreativeAsset:
        asset = CreativeAsset(
            filename=filename,
            file_type=file_type,
            mime_type=mime_type,
            storage_key=storage_key,
            size_bytes=size_bytes,
            analysis_status=analysis_status,
        )
        self.db.add(asset)
        self.db.flush()
        return asset

    def get(self, asset_id: str) -> CreativeAsset | None:
        return self.db.get(CreativeAsset, asset_id)

    def existing_filenames(self, filenames: list[str]) -> set[str]:
        """Filenames already present in the library (upload dedup)."""
        if not filenames:
            return set()
        stmt = select(CreativeAsset.filename).where(
            CreativeAsset.filename.in_(filenames)
        )
        return set(self.db.scalars(stmt).all())

    def list_with_creative(
        self, search: str | None = None
    ) -> list[tuple[CreativeAsset, str | None, float | None, str | None]]:
        """Assets with creative name, analysis confidence and lifecycle state."""
        stmt = (
            select(
                CreativeAsset,
                Creative.name,
                AnalysisResult.confidence,
                Creative.lifecycle_state,
            )
            .outerjoin(CreativeVariant, CreativeVariant.asset_id == CreativeAsset.id)
            .outerjoin(Creative, Creative.id == CreativeVariant.creative_id)
            .outerjoin(AnalysisResult, AnalysisResult.asset_id == CreativeAsset.id)
            .order_by(CreativeAsset.created_at.desc())
        )
        if search:
            stmt = stmt.where(CreativeAsset.filename.ilike(f"%{search}%"))
        return [
            (asset, creative_name, confidence, lifecycle_state)
            for asset, creative_name, confidence, lifecycle_state
            in self.db.execute(stmt).all()
        ]

    def set_status(self, asset: CreativeAsset, status: str, message: str = "") -> None:
        asset.analysis_status = status
        asset.status_message = message
        self.db.flush()

    def delete(self, asset: CreativeAsset) -> None:
        self.db.delete(asset)
        self.db.flush()
