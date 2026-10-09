"""迁移 0020：performances.optimization_type 列 + 从 raw->>'优化方式' 回填归一。

存量行零重新导入：raw JSONB 完整保留原始 Excel 单元格，回填 SQL 与
services/excel.normalize_objective 同口径（安装/install/installs/mai →
install，aeo → aeo，vo/value optimization → vo，其余/缺列 → NULL）。
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

API_DIR = Path(__file__).resolve().parents[1]
ADMIN_URL = os.environ.get(
    "TEST_ADMIN_URL",
    "postgresql+psycopg2://augura:augura@localhost:5433/postgres",
)
DB_NAME = "augura_migration_0020_test"


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


def _insert_performance(conn, raw: dict[str, object]) -> str:
    row_id = str(uuid.uuid4())
    conn.execute(
        text(
            "INSERT INTO performances "
            "(id, creative_name, impressions, clicks, spend, installs, raw) "
            "VALUES (:id, 'KS_FAKE-m0020', 0, 0, 1.0, 0, CAST(:raw AS jsonb))"
        ),
        {"id": row_id, "raw": json.dumps(raw, ensure_ascii=False)},
    )
    return row_id


def test_0020_backfill_normalizes_aliases(migration_url: str) -> None:
    # 停在 0019（无 optimization_type 列），构造含各别名的存量行
    _alembic("upgrade", "0019_verdict_snapshot_labels", migration_url)
    engine = create_engine(migration_url)
    cases = {
        "install_zh": {"优化方式": "安装"},
        "install_mai": {"优化方式": "MAI"},  # 大小写不敏感
        "install_en": {"优化方式": "Installs"},
        "aeo": {"优化方式": "AEO"},
        "vo": {"优化方式": "VO"},
        "vo_long": {"优化方式": "value optimization"},
        "unknown": {"优化方式": "别的口径"},
        "missing": {"素材名称": "KS_FAKE-m0020"},  # 无该列 → NULL
    }
    ids: dict[str, str] = {}
    with engine.begin() as conn:
        for key, raw in cases.items():
            ids[key] = _insert_performance(conn, raw)

    _alembic("upgrade", "head", migration_url)
    with engine.connect() as conn:
        values = {
            row[0]: row[1]
            for row in conn.execute(
                text("SELECT id, optimization_type FROM performances")
            )
        }
    assert values[ids["install_zh"]] == "install"
    assert values[ids["install_mai"]] == "install"
    assert values[ids["install_en"]] == "install"
    assert values[ids["aeo"]] == "aeo"
    assert values[ids["vo"]] == "vo"
    assert values[ids["vo_long"]] == "vo"
    assert values[ids["unknown"]] is None
    assert values[ids["missing"]] is None
    engine.dispose()
