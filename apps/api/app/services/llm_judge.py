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

from openai import BadRequestError, OpenAI

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
    temperature: float | None = None,
) -> dict[str, Any] | None:
    """One JSON-mode completion; None on any failure (静默降级).

    temperature 显式透传（None 时用 config.judge_temperature）——不传时
    服务商默认值可能是 0，自洽投票的三次采样会退化为同一票。
    """
    kwargs: dict[str, Any] = {
        "model": config.vision_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": max_tokens,
        "temperature": (
            temperature if temperature is not None else config.judge_temperature
        ),
    }
    for attempt in range(2):
        try:
            completion = _client(config).chat.completions.create(**kwargs)
            raw = completion.choices[0].message.content or ""
            return json.loads(raw)
        except BadRequestError as exc:
            # 部分端点只允许固定温度（如 Kimi k3 仅允许 1），显式传值会
            # 400——被拒时不带温度重试，走服务商默认（k3 恒为 1，自洽
            # 投票的采样多样性仍在）。其它 400 不重试。
            if attempt == 0 and "temperature" in str(exc).lower():
                kwargs.pop("temperature", None)
                logger.info("端点拒绝显式 temperature，改用服务商默认值重试: %s", exc)
                continue
            logger.debug("llm judge completion failed: %s", exc)
            return None
        except Exception as exc:  # noqa: BLE001
            logger.debug("llm judge completion failed: %s", exc)
            return None
    return None


def vote_json(
    config: AIConfig,
    *,
    system: str,
    user: str,
    key: str,
    n: int = 3,
    max_tokens: int = 800,
    temperature: float | None = None,
) -> tuple[Any | None, int, list[str]]:
    """Run ``complete_json`` n times and majority-vote on ``key``.

    Returns (winning_value, win_count, reasons). winning_value is None when
    there is no majority winner (分裂 → 纯人工).
    """
    values: list[Any] = []
    reasons: list[str] = []
    for _ in range(n):
        result = complete_json(
            config,
            system=system,
            user=user,
            max_tokens=max_tokens,
            temperature=temperature,
        )
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
