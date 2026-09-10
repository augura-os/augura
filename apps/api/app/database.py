"""SQLAlchemy 2.0 engine / session factory."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

engine = create_engine(
    get_settings().database_url,
    pool_pre_ping=True,
    pool_timeout=60,  # 默认 30s 在批量分析高峰期容易误杀
    future=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    class_=Session,
    autoflush=False,
    expire_on_commit=False,
)
