"""EditLog persistence — one row per human correction."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import EditLog


class EditLogRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def record(
        self,
        *,
        entity_type: str,
        entity_id: str,
        action: str,
        field: str = "",
        old_value: str = "",
        new_value: str = "",
    ) -> EditLog:
        entry = EditLog(
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            field=field,
            old_value=old_value,
            new_value=new_value,
        )
        self.db.add(entry)
        self.db.flush()
        return entry
