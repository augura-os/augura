"""JudgeSuggestion 建议缓存的读写（收件箱"仅建议级"条目的落库处）。

LLM/测量层的所有预裁建议都经这里 upsert：同一 subject
（kind + left_id + right_id 唯一约束）重复写入只更新 verdict/votes/reason，
所以脚本和后台任务可以幂等重跑。
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import JudgeSuggestion


def upsert_suggestion(
    db: Session,
    *,
    kind: str,
    left_id: str,
    right_id: str | None,
    verdict: str,
    votes: int,
    reason: str,
) -> None:
    row = db.scalar(
        select(JudgeSuggestion).where(
            JudgeSuggestion.kind == kind,
            JudgeSuggestion.left_id == left_id,
            JudgeSuggestion.right_id.is_(right_id) if right_id is None
            else JudgeSuggestion.right_id == right_id,
        )
    )
    if row is None:
        row = JudgeSuggestion(
            id=str(uuid.uuid4()), kind=kind, left_id=left_id, right_id=right_id
        )
        db.add(row)
    row.verdict = verdict
    row.votes = votes
    row.reason = reason
    db.flush()


def delete_suggestion(
    db: Session,
    *,
    kind: str,
    left_id: str,
    right_id: str | None,
) -> None:
    """删除某 subject 的建议缓存（自动合并结案后清掉残留建议）。"""
    row = db.scalar(
        select(JudgeSuggestion).where(
            JudgeSuggestion.kind == kind,
            JudgeSuggestion.left_id == left_id,
            JudgeSuggestion.right_id.is_(right_id) if right_id is None
            else JudgeSuggestion.right_id == right_id,
        )
    )
    if row is not None:
        db.delete(row)
        db.flush()
