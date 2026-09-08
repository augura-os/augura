"""Anonymization primitives: rotating instance id + coarse timestamp buckets."""
from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timezone

ROTATION_DAYS = 90
BUCKET_HOURS = 6

# 素材名 slug（≥3 段的 kebab 串）可能含项目/campaign 线索——回流前按实例盐哈希
_SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+){2,}")


def scrub_slugs(value: str, *, salt: str) -> str:
    """素材 slug → h_<hash8>（判定结构保留，具体内容不出本机）。

    DNA 编码（D01）、verdict、中文理由等不含三连 kebab 的内容不受影响。
    """

    def _sub(match: re.Match[str]) -> str:
        digest = hashlib.sha1(f"{salt}:{match.group(0)}".encode()).hexdigest()
        return f"h_{digest[:8]}"

    return _SLUG_RE.sub(_sub, value)


def new_instance_id() -> str:
    """Random UUID with no link to device/account (PRIVACY.md §1.1)."""
    return str(uuid.uuid4())


def should_rotate(created_at: datetime, *, now: datetime | None = None) -> bool:
    """Instance ids rotate every ROTATION_DAYS days."""
    now = now or datetime.now(timezone.utc)
    return (now - created_at).days >= ROTATION_DAYS


def timestamp_bucket(dt: datetime | None = None) -> str:
    """Coarse 6-hour bucket (never exact timestamps)."""
    dt = dt or datetime.now(timezone.utc)
    hour = (dt.hour // BUCKET_HOURS) * BUCKET_HOURS
    return dt.replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()
