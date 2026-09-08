"""Flush the local telemetry queue to the configured endpoint.

Usage:
    TELEMETRY_ENDPOINT=https://pool.example.com python -m scripts.flush_telemetry
"""
from __future__ import annotations

import os
import platform
from datetime import datetime, timezone

from app.config import get_settings
from app.database import SessionLocal
from app.repositories.settings import SettingsRepository
from app.services.settings import resolve_genre
from app.version import APP_VERSION
from telemetry.anonymize import timestamp_bucket
from telemetry.client import TelemetryClient
from telemetry.export import (
    aggregate_metric_events,
    corrections_from_edit_logs,
    creative_lifecycle_events,
)

CORRECTIONS_WATERMARK_SETTING = "telemetry_corrections_since"


def main() -> None:
    settings = get_settings()
    db = SessionLocal()
    try:
        repo = SettingsRepository(db)
        # telemetry_enabled 语义 = 创意基因共建计划开关（默认开）；
        # 运行保障事件（报错/版本）不受其影响（PRIVACY §1.1a）
        program_enabled = repo.get("telemetry_enabled") != "false"
        endpoint = os.environ.get("TELEMETRY_ENDPOINT", "")
        token = os.environ.get("TELEMETRY_TOKEN", "")
        instance_id = repo.get("telemetry_instance_id") or ""
        genre = resolve_genre(db)

        queue_path = os.path.join(settings.upload_dir, "telemetry", "queue.db")
        os.makedirs(os.path.dirname(queue_path), exist_ok=True)
        client = TelemetryClient(
            queue_path, enabled=True, program_enabled=program_enabled,
            endpoint=endpoint, token=token, instance_id=instance_id,
        )

        # 运行保障（始终）：精简版 session_start——版本/OS/时间桶，不带品类
        client.record(
            "session_start",
            app_version=APP_VERSION,
            os_family=platform.system().lower(),
            timestamp_bucket=timestamp_bucket(),
        )
        # 创意基因共建计划（可关闭）：品类分桶的 session_start + 聚合 + 修正
        client.record(
            "session_start",
            app_version=APP_VERSION,
            os_family=platform.system().lower(),
            genre=genre,
            timestamp_bucket=timestamp_bucket(),
        )
        for event in aggregate_metric_events(db, genre=genre):
            client.record("aggregate_metric", **event)
        for event in creative_lifecycle_events(db, genre=genre):
            client.record("creative_lifecycle", **event)

        # 共建计划关闭时不推进水位线——重新开启后修正数据可补发
        if program_enabled:
            watermark_raw = repo.get(CORRECTIONS_WATERMARK_SETTING)
            since = datetime.fromisoformat(watermark_raw) if watermark_raw else None
            for event in corrections_from_edit_logs(
                db, since=since, salt=instance_id, genre=genre
            ):
                client.record("correction", **event)
            repo.set(
                CORRECTIONS_WATERMARK_SETTING, datetime.now(timezone.utc).isoformat()
            )
        db.commit()  # 水位线必须落库，否则每次 flush 重复导出全部修正
    finally:
        db.close()

    print(f"queue: {client.queue_size()} pending, endpoint: {endpoint or '(not set)'}")
    # 聚合事件是"当前状态快照"非日志流：每次 flush 全部送完，不在队列里积压
    sent = 0
    while True:
        batch = client.flush()
        sent += batch
        if batch == 0:
            break
    print(f"sent: {sent}, remaining: {client.queue_size()}")
    client.close()


if __name__ == "__main__":
    main()
