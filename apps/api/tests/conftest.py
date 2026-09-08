"""Pytest fixtures: isolated Postgres test database.

Creates ``augura_test`` on the local Postgres (default localhost:5433,
override with ``TEST_ADMIN_URL``), runs alembic migrations against it, and
hands out sessions wrapped in a rolled-back transaction per test. Tests
that need the DB are skipped when Postgres is unreachable.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

API_DIR = Path(__file__).resolve().parents[1]
ADMIN_URL = os.environ.get(
    "TEST_ADMIN_URL",
    "postgresql+psycopg2://augura:augura@localhost:5433/postgres",
)
TEST_DB_NAME = os.environ.get("TEST_DB_NAME", "augura_test")


def _admin_engine() -> Engine:
    return create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")


def _test_url() -> str:
    return ADMIN_URL.rsplit("/", 1)[0] + "/" + TEST_DB_NAME


def _migrate(url: str) -> None:
    from alembic import command
    from alembic.config import Config
    from app.config import get_settings

    os.environ["DATABASE_URL"] = url
    get_settings.cache_clear()
    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "alembic"))
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session")
def test_db_url() -> Iterator[str]:
    try:
        with _admin_engine().connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {TEST_DB_NAME}"))
            conn.execute(text(f"CREATE DATABASE {TEST_DB_NAME}"))
    except OperationalError as exc:
        pytest.skip(f"Postgres 不可达（{ADMIN_URL}）：{exc.__class__.__name__}")
    _migrate(_test_url())
    yield _test_url()
    with _admin_engine().connect() as conn:
        conn.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :name AND pid <> pg_backend_pid()"
            ),
            {"name": TEST_DB_NAME},
        )
        conn.execute(text(f"DROP DATABASE IF EXISTS {TEST_DB_NAME}"))


@pytest.fixture()
def db_session(test_db_url: str) -> Iterator[Session]:
    engine = create_engine(test_db_url)
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
        engine.dispose()
