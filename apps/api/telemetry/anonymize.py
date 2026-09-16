"""Anonymization primitives: rotating instance id + coarse timestamp buckets."""
from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timezone

ROTATION_DAYS = 90
BUCKET_HOURS = 6

# 命名串（≥3 段连字符结构，任意文字系统）可能含项目/campaign/人名线索——
# 回流前按实例盐哈希。结构匹配与语言无关（ASCII/CJK/阿拉伯文等同规则），
# 无需按语言维护字符集。
_SLUG_RE = re.compile(r"[^\s-]+(?:-[^\s-]+){2,}")


def scrub_slugs(value: str, *, salt: str) -> str:
    """命名串 → h_<hash8>（判定结构保留，具体内容不出本机）。

    DNA 编码（D01）、verdict、中文理由等无三连连字符的内容不受影响。
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
