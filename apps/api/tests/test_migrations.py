"""Alembic migration roundtrip against a throwaway database.

upgrade head → downgrade base → upgrade head must all succeed; this is the
schema-level rollback guarantee referenced by CONTRIBUTING.md.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

API_DIR = Path(__file__).resolve().parents[1]
ADMIN_URL = os.environ.get(
    "TEST_ADMIN_URL",
    "postgresql+psycopg2://augura:augura@localhost:5433/postgres",
)
DB_NAME = "augura_migration_test"

EXPECTED_TABLES = {
    "analysis_results",
    "creative_assets",
    "creative_variants",
    "creatives",
    "edit_logs",
    "graph_edges",
    "graph_nodes",
    "performances",
    "projects",
    "settings",
    "tag_assignments",
    "tags",
}


def _alembic(direction: str, revision: str, url: str) -> None:
    from alembic import command
    from alembic.config import Config
    from app.config import get_settings

    os.environ["DATABASE_URL"] = url
    get_settings.cache_clear()
    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "alembic"))
    getattr(command, direction)(cfg, revision)


@pytest.fixture()
def migration_url() -> str:
    url = ADMIN_URL.rsplit("/", 1)[0] + "/" + DB_NAME
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {DB_NAME}"))
            conn.execute(text(f"CREATE DATABASE {DB_NAME}"))
    except OperationalError as exc:
        pytest.skip(f"Postgres 不可达：{exc.__class__.__name__}")
    yield url
    with admin.connect() as conn:
        conn.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :name AND pid <> pg_backend_pid()"
            ),
            {"name": DB_NAME},
        )
        conn.execute(text(f"DROP DATABASE IF EXISTS {DB_NAME}"))
    admin.dispose()


def test_upgrade_downgrade_roundtrip(migration_url: str) -> None:
    _alembic("upgrade", "head", migration_url)
    engine = create_engine(migration_url)
    with engine.connect() as conn:
        tables = {
            row[0]
            for row in conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
        }
    assert EXPECTED_TABLES <= tables

    _alembic("downgrade", "base", migration_url)
    with engine.connect() as conn:
        tables = {
            row[0]
            for row in conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
        }
    assert not (EXPECTED_TABLES - {"alembic_version"}) & tables

    # Upgrade again — rollback must be repeatable.
    _alembic("upgrade", "head", migration_url)
    engine.dispose()
