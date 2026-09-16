"""Merge guard — real-time boundary-tree checks before a merge (case 13).

Three checks, all computed from existing data:

1. **prior ruling (block)**: the pair has a documented "维持拆分" ruling in
   docs/case-rulings.json — merging it overturns a human case ruling, so it
   requires an explicit ``force_reason`` and is audit-logged as forced.
2. **cross-DNA (warn)**: source and target sit in different DNA families —
   by Q1/Q2 that's usually a wrong merge; the user should confirm intent.
3. **low hook similarity (warn)**: token similarity below the borderline
   review floor (0.20) — the pair would not even surface as a candidate.

The guard warns and records; it never silently blocks (Human > AI).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Creative, SplitRuling
from app.services.clustering import token_similarity

BORDERLINE_LOW = 0.20

# 拆分裁决超过 90 天从 block 降级为 warn——投手对"相似性"的定义随市场
# 阶段变化，旧裁决提示但不强制理由（合并守卫软化，设计评审 2026-08）
RULING_DECAY_DAYS = 90

_RULINGS_PATH = Path(__file__).resolve().parents[4] / "docs" / "case-rulings.json"

logger = logging.getLogger(__name__)


@dataclass
class GuardHit:
    level: Literal["block", "warn"]
    check: str
    message: str


@lru_cache
def _rulings() -> list[dict[str, object]]:
    if not _RULINGS_PATH.exists():
        # Docker images may not ship docs/ (no volume mounted) — the guard
        # degrades to "no prior rulings" instead of 500-ing the review queue.
        logger.warning("case-rulings.json not found at %s; treating as empty", _RULINGS_PATH)
        return []
    return json.loads(_RULINGS_PATH.read_text(encoding="utf-8"))


def find_prior_ruling(name_a: str, name_b: str) -> dict[str, object] | None:
    pair = {name_a, name_b}
    for ruling in _rulings():
        if set(ruling["pair"]) == pair and ruling["verdict"] == "split":
            return ruling
    return None


def find_db_ruling(
    db: Session, name_a: str, name_b: str
) -> SplitRuling | None:
    """收件箱结案沉淀的拆分裁决（split_rulings 表，名称无序对匹配）。"""
    low, high = sorted((name_a, name_b))
    return db.scalar(
        select(SplitRuling).where(
            SplitRuling.name_a == low, SplitRuling.name_b == high
        )
    )


def check_merge(
    source: Creative, target: Creative, db: Session | None = None
) -> list[GuardHit]:
    hits: list[GuardHit] = []

    ruling = find_prior_ruling(source.name, target.name)
    if ruling is not None:
        hits.append(
            GuardHit(
                level="block",
                check="prior_ruling",
                message=(
                    f"案例 {ruling['case']} 已裁决维持拆分（{ruling['note']}），"
                    "合并将推翻既定裁决，必须填写理由"
                ),
            )
        )
    if db is not None:
        db_ruling = find_db_ruling(db, source.name, target.name)
        if db_ruling is not None:
            age_days = (
                datetime.now(timezone.utc) - db_ruling.created_at
            ).days
            if age_days > RULING_DECAY_DAYS:
                hits.append(
                    GuardHit(
                        level="warn",
                        check="prior_ruling",
                        message=(
                            f"该对在 {age_days} 天前结案维持拆分"
                            f"（{db_ruling.reason or '无理由记录'}），"
                            "语境可能已变化，请确认后再合并"
                        ),
                    )
                )
            else:
                hits.append(
                    GuardHit(
                        level="block",
                        check="prior_ruling",
                        message=(
                            f"该对已在收件箱结案维持拆分（{db_ruling.reason or '无理由记录'}），"
                            "合并将推翻既定裁决，必须填写理由"
                        ),
                    )
                )

    source_dna = source.dna_id
    target_dna = target.dna_id
    if source_dna and target_dna and source_dna != target_dna:
        hits.append(
            GuardHit(
                level="warn",
                check="cross_dna",
                message="跨 DNA 家族合并，请确认 Q1 钩子/Q2 机制一致",
            )
        )

    score = token_similarity(source.name, target.name)
    if score < BORDERLINE_LOW and ruling is None and not any(
        hit.check == "prior_ruling" for hit in hits
    ):
        hits.append(
            GuardHit(
                level="warn",
                check="low_similarity",
                message=f"名称相似度仅 {score:.2f}，请确认不是误操作",
            )
        )
    return hits
