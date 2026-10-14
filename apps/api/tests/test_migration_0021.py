"""迁移 0021：creative_diagnoses 表（Creative Lab 定性诊断结果，M1）。

关键断言：升级后表存在、关键列齐全、唯一约束
uq_creative_diagnoses_asset_prompt 在列 (asset_id, prompt_version) 上；
upgrade → downgrade → upgrade 往返可重复。
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
DB_NAME = "augura_migration_0021_test"

KEY_COLUMNS = {
    "id",
    "asset_id",
    "observation_snapshot",
    "product_facts_snapshot",
    "audience_context",
    "output",
    "evidence_label",
    "prompt_version",
    "engine_version",
    "created_at",
    "updated_at",
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


def _table_columns(conn) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'creative_diagnoses'"
            )
        )
    }


def _unique_constraint_columns(conn) -> list[str]:
    rows = conn.execute(
        text(
            "SELECT a.attname FROM pg_constraint c "
            "JOIN pg_attribute a "
            "  ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey) "
            "WHERE c.conrelid = 'creative_diagnoses'::regclass "
            "  AND c.contype = 'u' "
            "  AND c.conname = 'uq_creative_diagnoses_asset_prompt' "
            "ORDER BY array_position(c.conkey, a.attnum)"
        )
    )
    return [row[0] for row in rows]


def test_0021_table_structure(migration_url: str) -> None:
    # 钉在 0021 本版：0022 起该唯一约束被 (asset_id, prompt_version, lang) 取代
    _alembic("upgrade", "0021_creative_diagnoses", migration_url)
    engine = create_engine(migration_url)
    with engine.connect() as conn:
        assert KEY_COLUMNS <= _table_columns(conn)
        assert _unique_constraint_columns(conn) == ["asset_id", "prompt_version"]
    engine.dispose()


def test_0021_downgrade_drops_table(migration_url: str) -> None:
    _alembic("upgrade", "head", migration_url)
    _alembic("downgrade", "0020_perf_optimization_type", migration_url)
    engine = create_engine(migration_url)
    with engine.connect() as conn:
        tables = {
            row[0]
            for row in conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
        }
    assert "creative_diagnoses" not in tables

    # 再升级——往返可重复
    _alembic("upgrade", "head", migration_url)
    with engine.connect() as conn:
        assert KEY_COLUMNS <= _table_columns(conn)
    engine.dispose()
