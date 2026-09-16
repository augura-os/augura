"""Shared FastAPI dependencies."""

from __future__ import annotations

from typing import Annotated, Generator

from fastapi import Depends
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import SessionLocal
from app.services.storage import StorageService


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_storage(settings: Annotated[Settings, Depends(get_settings)]) -> StorageService:
    return StorageService(settings)


DbDep = Annotated[Session, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
StorageDep = Annotated[StorageService, Depends(get_storage)]
