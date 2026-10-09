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


class ReasonBitModel(BaseModel):
    """Context-only supplementary reason, i18n-ready (brief.bit.<code> 模板）。"""

    code: str
    params: dict[str, float | int | str | None] = Field(default_factory=dict)


class RecommendationItem(BaseModel):
    creative_id: str
    creative_name: str
    dna_code: str | None
    dna_name: str | None
    action: RecommendationAction
    reasons: list[str] = Field(default_factory=list)
    # 结构化判定（services/recommendation Verdict）：前端按 reason_code + params
    # 渲染 i18n 决策句；reasons 保留旧中文文案兼容
    reason_code: str = ""
    reason_params: dict[str, float | int | str | None] = Field(default_factory=dict)
    # 补充理由（只读上下文，不影响判定）：前端按 brief.bit.<code> 渲染，
    # 缺模板/为空时回退 reasons[1:] 中文文案（兼容旧数据）
    reason_bits: list[ReasonBitModel] = Field(default_factory=list)
    # 赢家七分类标签（LabelCode；不影响判定）：前端按 brief.label.<code> 渲染徽章
    labels: list[str] = Field(default_factory=list)
    # 货币化 priority（services/priority）：这条建议的日度金额 + 后验把握
    priority_dollars: float = 0.0
    confidence: float = 0.0
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
