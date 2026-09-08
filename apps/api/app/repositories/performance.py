"""Performance persistence (rows parsed from Facebook Excel exports)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Sequence

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import Performance
from app.services.matching import matches

if TYPE_CHECKING:
    from app.services.excel import ParsedPerformanceRow


class PerformanceRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def bulk_create(
        self, asset_id: str, rows: Sequence[ParsedPerformanceRow]
    ) -> list[Performance]:
        created: list[Performance] = []
        for row in rows:
            performance = Performance(
                asset_id=asset_id,
                creative_name=row.creative_name,
                date=row.row_date,
                impressions=row.impressions,
                clicks=row.clicks,
                spend=row.spend,
                installs=row.installs,
                raw=row.raw,
            )
            self.db.add(performance)
            created.append(performance)
        self.db.flush()
        return created

    def list_for_asset(self, asset_id: str) -> list[Performance]:
        stmt = (
            select(Performance)
            .where(Performance.asset_id == asset_id)
            .order_by(Performance.created_at)
        )
        return list(self.db.scalars(stmt).all())

    def list_for_creative_name(self, asset_filename: str) -> list[Performance]:
        """Rows whose creative_name matches the asset filename.

        Two-directional prefix matching with a minimum shared prefix
        (``app.services.matching``): filenames share a long boilerplate, so
        plain ``startswith`` both misses Excel names that drop the trailing
        ``-竖`` suffix and cross-matches assets that differ only after the
        boilerplate. Filtering happens in Python; the performance table is
        small (thousands of rows) so this stays cheap for the MVP.
        """
        stmt = select(Performance).order_by(Performance.date, Performance.creative_name)
        return [
            row
            for row in self.db.scalars(stmt).all()
            if row.creative_name and matches(asset_filename, row.creative_name)
        ]

    def delete_for_asset(self, asset_id: str) -> None:
        self.db.execute(delete(Performance).where(Performance.asset_id == asset_id))
        self.db.flush()
