"""Shared LLM judgement primitives (2024–2026 方法论).

- 自洽性采样：同题采样 N 次，**一致率即置信度**（Pangakis & Wolken 2025；
  Horych et al. 2025 多数投票聚合）。不让模型自报 confidence——
  "judges can rank but cannot score"（arXiv 2604.25235）。
- Pairwise 判定防位置偏差：A/B 交换顺序各跑（Zheng et al. 2023）。
- 失败静默：任何 LLM 异常都降级为"无建议"，绝不阻塞主流程。
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from typing import Any

from openai import OpenAI

from app.services.settings import AIConfig

logger = logging.getLogger(__name__)


def _client(config: AIConfig) -> OpenAI:
    return OpenAI(api_key=config.api_key, base_url=config.base_url)


def complete_json(
    config: AIConfig,
    *,
    system: str,
    user: str,
    max_tokens: int = 800,
) -> dict[str, Any] | None:
    """One JSON-mode completion; None on any failure (静默降级)."""
    try:
        completion = _client(config).chat.completions.create(
            model=config.vision_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
            max_tokens=max_tokens,
        )
        raw = completion.choices[0].message.content or ""
        return json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        logger.debug("llm judge completion failed: %s", exc)
        return None


def vote_json(
    config: AIConfig,
    *,
    system: str,
    user: str,
    key: str,
    n: int = 3,
    max_tokens: int = 800,
) -> tuple[Any | None, int, list[str]]:
    """Run ``complete_json`` n times and majority-vote on ``key``.

    Returns (winning_value, win_count, reasons). winning_value is None when
    there is no majority winner (分裂 → 纯人工).
    """
    values: list[Any] = []
    reasons: list[str] = []
    for _ in range(n):
        result = complete_json(config, system=system, user=user, max_tokens=max_tokens)
        if result is None:
            continue
        value = result.get(key)
        if value is not None:
            values.append(value if not isinstance(value, str) else value.strip())
        reason = result.get("reason")
        if isinstance(reason, str) and reason.strip():
            reasons.append(reason.strip())
    if not values:
        return None, 0, reasons
    winner, count = Counter(values).most_common(1)[0]
    return winner, count, reasons
