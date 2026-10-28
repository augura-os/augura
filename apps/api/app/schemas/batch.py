"""批量端点的 per-item 结果结构（P0a 契约 §2.1），三个批量端点统一。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class BatchItemResult(BaseModel):
    """status：success | skipped | failed；
    reason：empty_family | not_found | already_done | conflict | error | None。"""

    id: str
    status: str
    reason: str | None = None
    message: str = ""
    data: dict[str, Any] = {}


class BatchSummary(BaseModel):
    success: int = 0
    skipped: int = 0
    failed: int = 0


class BatchResult(BaseModel):
    results: list[BatchItemResult]
    summary: BatchSummary


def collect(items: list[BatchItemResult]) -> BatchResult:
    """逐项结果汇总成 BatchResult（summary 三态计数）。"""
    summary = BatchSummary()
    for item in items:
        if item.status == "success":
            summary.success += 1
        elif item.status == "skipped":
            summary.skipped += 1
        else:
            summary.failed += 1
    return BatchResult(results=items, summary=summary)
