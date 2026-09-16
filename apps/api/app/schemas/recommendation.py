"""Recommendation DTOs (GET /creatives/recommendations)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

RecommendationAction = Literal["KEEP", "ITERATE", "PAUSE", "ARCHIVE"]


class MetricsOut(BaseModel):
    spend: float
    payers: int
    installs: int
    cpp: float | None
    roas: float | None
    cpi: float | None
    ipm: float | None
    days_idle: int | None
    recent_spend: float
    recent_cpp: float | None
    variant_count: int
    # 演化维度：裂变次数 / 已判定数 / 有效数
    derivation_count: int = 0
    judged_count: int = 0
    positive_count: int = 0
    # 可选判定指标（judge_metrics 开启后有意义）
    d3_roas: float | None = None
    d1_retention: float | None = None


class RecommendationItem(BaseModel):
    creative_id: str
    creative_name: str
    dna_code: str | None
    dna_name: str | None
    action: RecommendationAction
    reasons: list[str] = Field(default_factory=list)
    metrics: MetricsOut
    # Creative Score（services/creative_score）：总分 + 四要素拆解
    score: float | None = None
    score_breakdown: dict[str, float] = Field(default_factory=dict)
    # 生命周期（services/lifecycle）：active / watch / archived
    lifecycle_state: str = "active"


class RecommendationReport(BaseModel):
    generated_at: datetime
    date_min: date | None
    date_max: date | None
    items: list[RecommendationItem] = Field(default_factory=list)
