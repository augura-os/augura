"""VariantDerivation persistence."""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import CreativeVariant, VariantDerivation


class DerivationRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, derivation_id: str) -> VariantDerivation | None:
        return self.db.scalar(
            select(VariantDerivation).where(VariantDerivation.id == derivation_id)
        )

    def get_pair(
        self, source_variant_id: str, target_variant_id: str
    ) -> VariantDerivation | None:
        stmt = select(VariantDerivation).where(
            VariantDerivation.source_variant_id == source_variant_id,
            VariantDerivation.target_variant_id == target_variant_id,
        )
        return self.db.scalar(stmt)

    def list_for_creative(self, creative_id: str) -> list[VariantDerivation]:
        stmt = (
            select(VariantDerivation)
            .join(
                CreativeVariant,
                CreativeVariant.id == VariantDerivation.source_variant_id,
            )
            .where(CreativeVariant.creative_id == creative_id)
            .order_by(VariantDerivation.created_at)
        )
        return list(self.db.scalars(stmt).all())

    def create(
        self,
        source_variant_id: str,
        target_variant_id: str,
        factor: str = "unknown",
        note: str = "",
    ) -> VariantDerivation:
        existing = self.get_pair(source_variant_id, target_variant_id)
        if existing is not None:
            return existing
        derivation = VariantDerivation(
            source_variant_id=source_variant_id,
            target_variant_id=target_variant_id,
            factor=factor,
            note=note,
        )
        self.db.add(derivation)
        self.db.flush()
        return derivation

    def delete(self, derivation: VariantDerivation) -> None:
        self.db.delete(derivation)
        self.db.flush()

    def list_pending_verdict(self) -> list[VariantDerivation]:
        stmt = select(VariantDerivation).where(VariantDerivation.verdict == "pending")
        return list(self.db.scalars(stmt).all())

    def any_between(self, variant_ids: list[str]) -> list[VariantDerivation]:
        stmt = select(VariantDerivation).where(
            or_(
                VariantDerivation.source_variant_id.in_(variant_ids),
                VariantDerivation.target_variant_id.in_(variant_ids),
            )
        )
        return list(self.db.scalars(stmt).all())
