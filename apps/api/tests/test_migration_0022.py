"""迁移 0022：creative_diagnoses 加 lang 列，唯一约束扩为 (asset_id, prompt_version, lang)。

关键断言：升级后 lang 列存在（NOT NULL，server_default 'zh'）；旧约束
uq_creative_diagnoses_asset_prompt 被删除，新约束
uq_creative_diagnoses_asset_prompt_lang 在 (asset_id, prompt_version, lang)
上；存量行的 lang 按 COALESCE(audience_context->>'lang', 'zh') 回填
（英文行归位 'en'，无 lang 键/无快照行归位 'zh'）；downgrade 复原旧约束。
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
DB_NAME = "augura_migration_0022_test"

OLD_CONSTRAINT = "uq_creative_diagnoses_asset_prompt"
NEW_CONSTRAINT = "uq_creative_diagnoses_asset_prompt_lang"


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


def _unique_constraint_columns(conn, name: str) -> list[str]:
    rows = conn.execute(
        text(
            "SELECT a.attname FROM pg_constraint c "
            "JOIN pg_attribute a "
            "  ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey) "
            "WHERE c.conrelid = 'creative_diagnoses'::regclass "
            "  AND c.contype = 'u' "
            "  AND c.conname = :name "
            "ORDER BY array_position(c.conkey, a.attnum)"
        ),
        {"name": name},
    )
    return [row[0] for row in rows]


def _lang_column_default_and_nullable(conn) -> tuple[str | None, str]:
    row = conn.execute(
        text(
            "SELECT column_default, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'creative_diagnoses' AND column_name = 'lang'"
        )
    ).one()
    return row[0], row[1]


def test_0022_lang_column_and_new_constraint(migration_url: str) -> None:
    _alembic("upgrade", "head", migration_url)
    engine = create_engine(migration_url)
    with engine.connect() as conn:
        assert "lang" in _table_columns(conn)
        default, nullable = _lang_column_default_and_nullable(conn)
        assert default == "'zh'::character varying"
        assert nullable == "NO"
        assert _unique_constraint_columns(conn, NEW_CONSTRAINT) == [
            "asset_id",
            "prompt_version",
            "lang",
        ]
        assert _unique_constraint_columns(conn, OLD_CONSTRAINT) == []
    engine.dispose()


def test_0022_backfill_lang_from_audience_context(migration_url: str) -> None:
    # 先停在 0021（尚无 lang 列），用 SQL 直造三种存量行
    _alembic("upgrade", "0021_creative_diagnoses", migration_url)
    engine = create_engine(migration_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO creative_assets (id, filename, file_type, storage_key) "
                "VALUES ('asset-bf', 'FAKE-m1b-backfill.mp4', 'video', "
                "'test/migration-0022-backfill')"
            )
        )
        rows = [
            # 英文存量行：audience_context 快照里有 lang='en'
            ("diag-en", "lab-v0.1.0", '{"lang": "en"}'),
            # 有受众快照但无 lang 键 → 归位 zh
            ("diag-audience-no-lang", "lab-v0.2.0", '{"region": "FAKE"}'),
            # 快照整列 NULL → 归位 zh
            ("diag-null", "lab-v0.3.0", None),
        ]
        for row_id, version, audience in rows:
            conn.execute(
                text(
                    "INSERT INTO creative_diagnoses "
                    "(id, asset_id, observation_snapshot, output, evidence_label, "
                    " prompt_version, engine_version, audience_context) "
                    "VALUES (:id, 'asset-bf', CAST('{}' AS jsonb), CAST('{}' AS jsonb), "
                    "'reference_only', :version, 'auto:fake', "
                    "CAST(:audience AS jsonb))"
                ),
                {"id": row_id, "version": version, "audience": audience},
            )
    _alembic("upgrade", "head", migration_url)
    with engine.connect() as conn:
        langs = dict(
            conn.execute(
                text("SELECT id, lang FROM creative_diagnoses")
            ).all()
        )
    engine.dispose()
    assert langs == {
        "diag-en": "en",
        "diag-audience-no-lang": "zh",
        "diag-null": "zh",
    }


def test_0022_same_asset_version_two_langs_coexist(migration_url: str) -> None:
    # 新约束允许同 asset 同 prompt 版本下 zh/en 各一行
    _alembic("upgrade", "head", migration_url)
    engine = create_engine(migration_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO creative_assets (id, filename, file_type, storage_key) "
                "VALUES ('asset-co', 'FAKE-m1b-coexist.mp4', 'video', "
                "'test/migration-0022-coexist')"
            )
        )
        for lang in ("zh", "en"):
            conn.execute(
                text(
                    "INSERT INTO creative_diagnoses "
                    "(id, asset_id, observation_snapshot, output, evidence_label, "
                    " prompt_version, lang, engine_version) "
                    "VALUES (:id, 'asset-co', CAST('{}' AS jsonb), CAST('{}' AS jsonb), "
                    "'reference_only', 'lab-v0.1.0', :lang, 'auto:fake')"
                ),
                {"id": f"diag-{lang}", "lang": lang},
            )
        count = conn.execute(
            text(
                "SELECT count(*) FROM creative_diagnoses "
                "WHERE asset_id = 'asset-co' AND prompt_version = 'lab-v0.1.0'"
            )
        ).scalar_one()
    engine.dispose()
    assert count == 2


def test_0022_downgrade_restores_old_constraint(migration_url: str) -> None:
    _alembic("upgrade", "head", migration_url)
    _alembic("downgrade", "0021_creative_diagnoses", migration_url)
    engine = create_engine(migration_url)
    with engine.connect() as conn:
        assert "lang" not in _table_columns(conn)
        assert _unique_constraint_columns(conn, OLD_CONSTRAINT) == [
            "asset_id",
            "prompt_version",
        ]
        assert _unique_constraint_columns(conn, NEW_CONSTRAINT) == []

    # 再升级——往返可重复
    _alembic("upgrade", "head", migration_url)
    with engine.connect() as conn:
        assert "lang" in _table_columns(conn)
        assert _unique_constraint_columns(conn, NEW_CONSTRAINT) == [
            "asset_id",
            "prompt_version",
            "lang",
        ]
    engine.dispose()
