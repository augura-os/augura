"""Review queue DTOs (GET /review/queue)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ReviewKind = Literal[
    "low_confidence",
    "dna_unassigned",
    "merge_candidate",
    "observation_pair",
    "pending_verdict",
    "derivation_review",
    "archive_suggestion",
    "market_conflict",
    "threshold_calibration",
    "market_detect",
]


class ReviewItem(BaseModel):
    kind: ReviewKind
    title: str
    reason: str
    creative_id: str | None = None
    creative_name: str | None = None
    asset_id: str | None = None
    related_creative_id: str | None = None
    related_creative_name: str | None = None
    derivation_id: str | None = None
    # 短名展示（裂变待判定）：市场标签 + 文件名尾部区分段
    source_label: str | None = None
    target_label: str | None = None
    factor: str | None = None
    # LLM 预裁建议（judge_suggestions 缓存；自洽性采样票数与理由）
    suggestion: str | None = None
    suggestion_votes: int | None = None
    suggestion_reason: str | None = None
    suggested_dna_id: str | None = None


class JudgeKindStats(BaseModel):
    """单个建议类别的准确率摘要（services/judge_calibration 只读计算）。"""

    auto_total: int = 0
    override_rate: float = 0.0
    # 无已决建议时为 null（没数据 ≠ 0% 采纳）
    acceptance_rate: float | None = None


class ReviewQueue(BaseModel):
    generated_at: datetime
    low_confidence: list[ReviewItem] = Field(default_factory=list)
    dna_unassigned: list[ReviewItem] = Field(default_factory=list)
    merge_candidates: list[ReviewItem] = Field(default_factory=list)
    observation_pairs: list[ReviewItem] = Field(default_factory=list)
    pending_verdicts: list[ReviewItem] = Field(default_factory=list)
    derivation_reviews: list[ReviewItem] = Field(default_factory=list)
    # 建议归档（score 低于阈值且长期无消耗；人工确认才置 archived）
    archive_suggestions: list[ReviewItem] = Field(default_factory=list)
    # 市场存疑（文件名前缀 × 分析标签冲突；仅提示人工核对，无操作）
    market_conflicts: list[ReviewItem] = Field(default_factory=list)
    # 阈值校准建议（修正驱动；人工在 Settings 改阈值才生效）
    threshold_calibrations: list[ReviewItem] = Field(default_factory=list)
    # 检测到的未配置市场前缀（引导新用户配置，人工确认）
    market_detects: list[ReviewItem] = Field(default_factory=list)
    # 各建议类别的改判率/采纳率摘要（judge 校准，供收件箱顶部展示）
    judge_stats: dict[str, JudgeKindStats] = Field(default_factory=dict)
