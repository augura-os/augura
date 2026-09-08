"""Creative / CreativeVariant persistence."""

from __future__ import annotations

from typing import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Creative, CreativeVariant


class CreativeRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        *,
        name: str,
        representative_embedding: list[float] | None = None,
        representative_text: str = "",
    ) -> Creative:
        creative = Creative(
            name=name,
            representative_embedding=representative_embedding,
            representative_text=representative_text,
        )
        self.db.add(creative)
        self.db.flush()
        return creative

    def get(self, creative_id: str) -> Creative | None:
        return self.db.get(Creative, creative_id)

    def list_all(self) -> list[Creative]:
        return list(self.db.scalars(select(Creative)).all())

    def variant_count(self, creative_id: str) -> int:
        return int(
            self.db.scalar(
                select(func.count())
                .select_from(CreativeVariant)
                .where(CreativeVariant.creative_id == creative_id)
            )
            or 0
        )

    def delete(self, creative: Creative) -> None:
        self.db.delete(creative)
        self.db.flush()


class VariantRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        *,
        asset_id: str,
        creative_id: str,
        name: str,
        embedding: list[float] | None = None,
    ) -> CreativeVariant:
        variant = CreativeVariant(
            asset_id=asset_id,
            creative_id=creative_id,
            name=name,
            embedding=embedding,
        )
        self.db.add(variant)
        self.db.flush()
        return variant

    def get(self, variant_id: str) -> CreativeVariant | None:
        return self.db.get(CreativeVariant, variant_id)

    def get_many(self, variant_ids: Sequence[str]) -> list[CreativeVariant]:
        if not variant_ids:
            return []
        stmt = select(CreativeVariant).where(CreativeVariant.id.in_(variant_ids))
        return list(self.db.scalars(stmt).all())

    def get_by_asset(self, asset_id: str) -> CreativeVariant | None:
        return self.db.scalar(
            select(CreativeVariant).where(CreativeVariant.asset_id == asset_id)
        )

    def list_by_creative(self, creative_id: str) -> list[CreativeVariant]:
        stmt = select(CreativeVariant).where(
            CreativeVariant.creative_id == creative_id
        )
        return list(self.db.scalars(stmt).all())

    def move_to_creative(self, variant: CreativeVariant, creative_id: str) -> None:
        variant.creative_id = creative_id
        self.db.flush()

    def delete(self, variant: CreativeVariant) -> None:
        self.db.delete(variant)
        self.db.flush()
