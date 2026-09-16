"""CreativeDNA persistence."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Creative, CreativeDNA


class DnaRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_all(self) -> list[CreativeDNA]:
        return list(self.db.scalars(select(CreativeDNA).order_by(CreativeDNA.code)).all())

    def get(self, dna_id: str) -> CreativeDNA | None:
        return self.db.scalar(select(CreativeDNA).where(CreativeDNA.id == dna_id))

    def get_by_code(self, code: str) -> CreativeDNA | None:
        return self.db.scalar(select(CreativeDNA).where(CreativeDNA.code == code))

    def create(self, **fields: object) -> CreativeDNA:
        dna = CreativeDNA(**fields)  # type: ignore[arg-type]
        self.db.add(dna)
        self.db.flush()
        return dna

    def assign_creative(self, creative: Creative, dna: CreativeDNA | None) -> None:
        """Attach a creative to a DNA family (None = unassign)."""
        creative.dna_id = dna.id if dna is not None else None
        self.db.flush()

    def list_creatives(self, dna_id: str) -> list[Creative]:
        stmt = select(Creative).where(Creative.dna_id == dna_id).order_by(Creative.name)
        return list(self.db.scalars(stmt).all())
