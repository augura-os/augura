"""Telemetry client: allowlist validation → local SQLite queue → batch POST.

Design rules (telemetry/README.md):
- 失败静默：任何上报错误都不影响主流程，重试 3 次后丢弃
- 离线累积：队列上限 1000 条，超出后丢弃最旧
- 未配置端点 / 开关关闭时为 no-op
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
import urllib.request
from pathlib import Path

from telemetry.allowlist import PROGRAM_EVENTS, validate

logger = logging.getLogger(__name__)

MAX_QUEUE = 1000
BATCH_SIZE = 50
MAX_ATTEMPTS = 3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
)
"""


class TelemetryClient:
    def __init__(
        self,
        db_path: str | Path,
        *,
        enabled: bool = True,
        program_enabled: bool = True,
        endpoint: str = "",
        token: str = "",
        instance_id: str = "",
    ) -> None:
        # enabled：总开关（endpoint 未配置时一切 no-op）
        # program_enabled：创意基因共建计划开关——只影响 PROGRAM_EVENTS；
        # 运行保障事件（报错/版本）始终收集（PRIVACY.md §1.1a 明示）
        self.enabled = enabled
        self.program_enabled = program_enabled
        self.endpoint = endpoint.rstrip("/")
        self.token = token
        self.instance_id = instance_id
        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._db.execute(_SCHEMA)
        self._db.commit()

    def record(self, event_type: str, **fields: object) -> bool:
        """Validate against the allowlist and enqueue. Returns False if dropped."""
        if not self.enabled:
            return False
        if event_type in PROGRAM_EVENTS and not self.program_enabled:
            return False
        payload = validate(event_type, fields)
        if payload is None:
            return False
        payload["instance_id"] = self.instance_id
        with self._db:
            self._db.execute(
                "INSERT INTO queue (event_type, payload, created_at) VALUES (?, ?, ?)",
                (event_type, json.dumps(payload, ensure_ascii=False), time.time()),
            )
            self._db.execute(
                "DELETE FROM queue WHERE id NOT IN "
                "(SELECT id FROM queue ORDER BY id DESC LIMIT ?)",
                (MAX_QUEUE,),
            )
        return True

    def queue_size(self) -> int:
        return int(self._db.execute("SELECT COUNT(*) FROM queue").fetchone()[0])

    def flush(self) -> int:
        """POST queued events in batches. Returns the number sent (0 on any failure)."""
        if not self.enabled or not self.endpoint:
            return 0
        sent = 0
        rows = self._db.execute(
            "SELECT id, event_type, payload, attempts FROM queue ORDER BY id LIMIT ?",
            (BATCH_SIZE,),
        ).fetchall()
        for row_id, event_type, payload, attempts in rows:
            if attempts >= MAX_ATTEMPTS:
                with self._db:
                    self._db.execute("DELETE FROM queue WHERE id = ?", (row_id,))
                continue
            try:
                # 事件类型一并上送（接收端按类型分桶统计；payload 本体已过白名单）
                body = json.dumps(
                    {"event_type": event_type, **json.loads(payload)},
                    ensure_ascii=False,
                )
                headers = {"Content-Type": "application/json"}
                if self.token:
                    headers["X-Augura-Token"] = self.token
                request = urllib.request.Request(
                    f"{self.endpoint}/v1/events",
                    data=body.encode("utf-8"),
                    headers=headers,
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=10) as response:
                    if response.status < 300:
                        with self._db:
                            self._db.execute("DELETE FROM queue WHERE id = ?", (row_id,))
                        sent += 1
                    else:
                        raise OSError(f"HTTP {response.status}")
            except Exception as exc:  # noqa: BLE001 — 失败静默
                logger.debug("telemetry flush failed, will retry later: %s", exc)
                with self._db:
                    self._db.execute(
                        "UPDATE queue SET attempts = attempts + 1 WHERE id = ?", (row_id,)
                    )
                break
        return sent

    def close(self) -> None:
        self._db.close()
