"""Pairwise merge judge (2023–2026 LLM-as-a-Judge 方法论).

问题收敛为二选一（同一 Creative / 不同 Creative），A/B 交换顺序各判一次
防位置偏差（Zheng et al. 2023），再加一次正向共 3 票。3 票一致才采信；
分裂则不显示建议（纯人工）。失败静默降级。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.services import ip_pack
from app.services.llm_judge import complete_json
from app.services.settings import AIConfig

logger = logging.getLogger(__name__)

_SYSTEM = ip_pack.MERGE_JUDGE_SYSTEM


@dataclass
class MergeJudgement:
    same_creative: bool
    votes: int  # 3 = 一致
    reason: str
    swapped_consistent: bool


def _judge_once(config: AIConfig, a: str, b: str) -> tuple[bool, str] | None:
    user = f"【A】{a}\n\n【B】{b}"
    result = complete_json(config, system=_SYSTEM, user=user)
    if result is None:
        return None
    same = result.get("same_creative")
    reason = result.get("reason")
    if not isinstance(same, bool):
        return None
    return same, (reason if isinstance(reason, str) else "")


def judge_pair(
    config: AIConfig,
    *,
    analysis_a: str,
    analysis_b: str,
) -> MergeJudgement | None:
    """3 票：正向×2 + 交换×1；交换票与多数不一致则标记 swapped_consistent=False。"""
    votes: list[tuple[bool, str]] = []
    first = _judge_once(config, analysis_a, analysis_b)
    swapped = _judge_once(config, analysis_b, analysis_a)
    second = _judge_once(config, analysis_a, analysis_b)
    for vote in (first, swapped, second):
        if vote is not None:
            votes.append(vote)
    if len(votes) < 2:
        return None
    same_count = sum(1 for same, _ in votes if same)
    total = len(votes)
    if same_count == total or same_count == 0:
        verdict = same_count == total
        swapped_ok = swapped is None or swapped[0] == verdict
        reason = votes[0][1] or ("3 票一致" if total == 3 else f"{total} 票一致")
        return MergeJudgement(
            same_creative=verdict,
            votes=total,
            reason=reason,
            swapped_consistent=swapped_ok,
        )
    return None  # 分裂 → 纯人工
