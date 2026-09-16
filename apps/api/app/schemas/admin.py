"""Admin route DTOs（POST /admin/backfill-embeddings）。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class BackfillResult(BaseModel):
    """一轮向量回填的统计（services/embedding.backfill_embeddings）。"""

    analyses: int = 0  # 补了向量的 analysis_results 条数
    variants: int = 0  # 同步写入 variant.embedding 的条数
    creatives: int = 0  # 按成员重算代表向量的 creative 数
    failed: int = 0  # 单条失败/空文本跳过
    remaining: int = 0  # 仍缺向量的 analysis 条数
    skipped: list[str] = Field(default_factory=list)  # 跳过条的 analysis id
