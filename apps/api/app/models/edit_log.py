"""EditLog — audit trail of every human correction (AI Constitution §5).

AI output is always overridable by humans, and each override is the
training signal a future Learning Agent needs: field, old value, new
value, when. Nothing here is ever written by the AI pipeline itself.
"""

from __future__ import annotations

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, new_uuid


class EditLog(TimestampMixin, Base):
    __tablename__ = "edit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    # "asset" | "creative"
    entity_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    # "update" (field edit) | "merge" | "split"
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    # Changed field ("tags", "summary", ...); "" for structural actions.
    field: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    old_value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    new_value: Mapped[str] = mapped_column(Text, nullable=False, default="")
