"""幂等键基础设施（P0a 批量接口契约 §2.1），三个批量端点共用。

- 请求头 ``Idempotency-Key`` 作用域 = 单批：同 key 同 endpoint 的重复
  提交直接返回首次存储的 response，不重复执行。
- TTL 24h，读取时惰性清理（无定时任务）。
- key 被不同 endpoint 复用属于客户端错误，409 拒绝（防止跨接口串数据）。
"""

from __future__ import annotations

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.exceptions import ApiError
from app.models import IdempotencyKey

_TTL = text("now() - interval '24 hours'")


def require_key(key: str | None) -> str:
    """Idempotency-Key 必需（契约 §2.1）：缺失/空白 400，返回去空白后的 key。"""
    if key is None or not key.strip():
        raise ApiError(400, "缺少 Idempotency-Key 请求头")
    return key.strip()


def cleanup_expired(db: Session) -> None:
    """惰性清理过期键（每次读取前跑一次，单行 DELETE 代价可忽略）。"""
    db.execute(delete(IdempotencyKey).where(IdempotencyKey.created_at < _TTL))
    db.flush()


def get_stored(db: Session, *, key: str, endpoint: str) -> dict | None:
    """读取已存储的 response；无记录返回 None，key 被别的 endpoint 占用 409。"""
    cleanup_expired(db)
    row = db.scalar(select(IdempotencyKey).where(IdempotencyKey.key == key))
    if row is None:
        return None
    if row.endpoint != endpoint:
        raise ApiError(409, "Idempotency-Key 已被其他批量接口占用")
    return dict(row.response_json)


def store_response(db: Session, *, key: str, endpoint: str, response: dict) -> None:
    """写入首次执行结果（随调用方的 commit 一起落库）。"""
    db.add(IdempotencyKey(key=key, endpoint=endpoint, response_json=response))
    db.flush()
