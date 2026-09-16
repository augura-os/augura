"""Admin routes: POST /admin/backfill-embeddings（手动全量向量回填）。

设计 §4.4：随行回填（周期巩固顺带补一批）之外的"急性子"入口——同步
回填全部存量向量。失败单条跳过，响应带回填统计（remaining > 0 说明
还有缺向量的 analysis，通常是 embedding 后端不可用或空文本）。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import DbDep, SettingsDep
from app.schemas.admin import BackfillResult
from app.schemas.common import Envelope, ok
from app.services.embedding import backfill_embeddings
from app.services.settings import resolve_ai_config

router = APIRouter()


@router.post("/admin/backfill-embeddings", response_model=Envelope[BackfillResult])
def backfill_embeddings_route(
    db: DbDep, settings: SettingsDep
) -> Envelope[BackfillResult]:
    """手动全量回填：所有缺向量的 analysis 补向量 + 族代表向量按成员重算。"""
    stats = backfill_embeddings(db, resolve_ai_config(db, settings))
    db.commit()
    return ok(
        BackfillResult(
            analyses=stats.analyses,
            variants=stats.variants,
            creatives=stats.creatives,
            failed=stats.failed,
            remaining=stats.remaining,
            skipped=stats.skipped,
        )
    )
