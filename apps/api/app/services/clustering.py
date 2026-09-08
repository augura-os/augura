"""Creative clustering — cosine similarity on embeddings (threshold 0.85),
with a local token-Jaccard fallback when no embedding provider is
configured (e.g. Kimi has no embeddings endpoint)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from math import sqrt
from typing import Sequence

from app.models import Creative

CLUSTER_THRESHOLD = 0.85
# Fallback threshold for token-Jaccard when embeddings are unavailable.
TEXT_CLUSTER_THRESHOLD = 0.34
# margin 决策：top1 过阈值但与 top2 差距小于该值时不自动归入（双子并列
# 防摇摆误并，实体解析的标准做法——明确赢家才自动）
CLUSTER_MARGIN = 0.05
# 触发测量裁决的文本分下限：低于此分的候选不值得花测量成本
BORDERLINE_LOW = 0.20

_TOKEN_RE = re.compile(r"[a-z0-9]+|[一-鿿]+")


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (sqrt(norm_a) * sqrt(norm_b))


def _tokens(text: str) -> set[str]:
    """Word tokens for latin text; unigrams+bigrams for CJK runs (no
    segmentation available, and CJK names like 老人掉水/女孩掉水 share
    characters rather than whole words)."""
    tokens: set[str] = set()
    for chunk in _TOKEN_RE.findall(text.lower()):
        if re.fullmatch(r"[一-鿿]+", chunk):
            tokens.update(chunk)
            tokens.update(chunk[i : i + 2] for i in range(len(chunk) - 1))
        else:
            tokens.add(chunk)
    return tokens


def token_similarity(text_a: str, text_b: str) -> float:
    """Jaccard similarity over normalized word/CJK tokens (0.0–1.0)."""
    a = _tokens(text_a)
    b = _tokens(text_b)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def top_creative_matches(
    embedding: Sequence[float], creatives: Sequence[Creative], limit: int = 2
) -> list[tuple[Creative, float]]:
    """按 representative embedding 余弦相似度召回 top-N 候选族（降序）。"""
    scored: list[tuple[Creative, float]] = []
    for creative in creatives:
        representative = creative.representative_embedding
        if not representative:
            continue
        scored.append((creative, cosine_similarity(embedding, representative)))
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored[:limit]


def best_creative_match(
    embedding: Sequence[float], creatives: Sequence[Creative]
) -> tuple[Creative | None, float]:
    """Return the most similar creative (by representative embedding) and
    its cosine score. ``(None, 0.0)`` when nothing is comparable."""
    matches = top_creative_matches(embedding, creatives, limit=1)
    if not matches or matches[0][1] <= 0.0:
        return None, 0.0
    return matches[0]


def _text_score(name: str, text: str, creative: Creative) -> float:
    reference = creative.representative_text or creative.name or ""
    if not reference.strip():
        return 0.0
    return 0.6 * token_similarity(name, creative.name or "") + 0.4 * (
        token_similarity(text, reference)
    )


def top_creative_matches_by_text(
    name: str, text: str, creatives: Sequence[Creative], limit: int = 2
) -> list[tuple[Creative, float]]:
    """Embedding-free fallback 的 top-N 召回（打分口径同
    :func:`best_creative_match_by_text`，降序）。"""
    scored = [(creative, _text_score(name, text, creative)) for creative in creatives]
    scored = [item for item in scored if item[1] > 0.0]
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored[:limit]


def best_creative_match_by_text(
    name: str, text: str, creatives: Sequence[Creative]
) -> tuple[Creative | None, float]:
    """Embedding-free fallback. The concept name carries the creative's
    identity (hook + mechanic) while tags are mostly generic attributes
    (game / genre / market), so score = 0.6·name Jaccard + 0.4·full-text
    Jaccard against each creative's representative_text."""
    matches = top_creative_matches_by_text(name, text, creatives, limit=1)
    if not matches:
        return None, 0.0
    return matches[0]


@dataclass(frozen=True)
class ClusterDecision:
    """top-2 召回后的关联决策（纯数据，不含副作用）。"""

    action: str  # "attach" | "create" | "inbox"
    creative: Creative | None  # attach/inbox 时的首选候选族；无候选为 None
    score: float  # top1 得分（无候选为 0.0）
    margin: float | None  # top1 - top2；候选不足两个为 None


def decide_cluster(
    candidates: Sequence[tuple[Creative, float]], threshold: float
) -> ClusterDecision:
    """文本/向量召回的 margin 决策：

    - 无候选或 top1 < threshold → ``create``（新建族）
    - top1 ≥ threshold 且 margin ≥ CLUSTER_MARGIN（或无 top2）→ ``attach``
    - top1 ≥ threshold 但 margin < CLUSTER_MARGIN（双子并列）→ ``inbox``
      （不自动归，新建族后由收件箱合并候选机制人工裁）
    """
    if not candidates:
        return ClusterDecision("create", None, 0.0, None)
    top_creative, top_score = candidates[0]
    margin = (
        top_score - candidates[1][1] if len(candidates) > 1 else None
    )
    if top_score < threshold:
        return ClusterDecision("create", top_creative, top_score, margin)
    if margin is not None and margin < CLUSTER_MARGIN:
        return ClusterDecision("inbox", top_creative, top_score, margin)
    return ClusterDecision("attach", top_creative, top_score, margin)


def running_mean(
    old: Sequence[float] | None, new: Sequence[float], count: int
) -> list[float]:
    """representative_embedding 的增量式均值：族内已有 ``count`` 个变体时
    把新向量并入均值。旧值缺失/维度不符/计数异常时直接用新向量。"""
    if not old or len(old) != len(new) or count <= 0:
        return list(new)
    return [(o * count + x) / (count + 1) for o, x in zip(old, new)]
