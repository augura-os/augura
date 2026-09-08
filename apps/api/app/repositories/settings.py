"""Settings key/value persistence (OpenAI API key storage)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Setting


class SettingsRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, key: str) -> str | None:
        setting = self.db.scalar(select(Setting).where(Setting.key == key))
        return setting.value if setting is not None else None

    def set(self, key: str, value: str) -> Setting:
        setting = self.db.scalar(select(Setting).where(Setting.key == key))
        if setting is None:
            setting = Setting(key=key, value=value)
            self.db.add(setting)
        else:
            setting.value = value
        self.db.flush()
        return setting
