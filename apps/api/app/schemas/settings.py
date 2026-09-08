"""Settings DTOs — AI provider configuration (OpenAI-compatible)."""

from __future__ import annotations

from pydantic import BaseModel


class SettingsInfo(BaseModel):
    api_key_set: bool
    api_key_masked: str
    base_url: str
    vision_model: str
    embedding_model: str
    telemetry_enabled: bool
    telemetry_instance_id: str
    # 合并自动执行开关（默认关；开启后 LLM 3/3 + pHash 对齐 ≥0.90 的合并
    # 候选对不再只写建议，由 judge_pipeline 直接执行并标 auto:）
    merge_auto_enabled: bool = False
    # 指标配置（v0.11）：显示 profile / 参与判定的指标 / 阈值
    metric_profile: list[str] = []
    judge_metrics: list[str] = []
    metric_thresholds: dict[str, float] = {}
    # 分市场阈值覆盖（市场标签 → 阈值键 → 值；空 = 跟随全局/品类档）
    market_thresholds: dict[str, dict[str, float]] = {}
    # 项目品类（v0.12 onboarding 设置；"" = 未设置）
    project_category: str = ""
    # 文件名市场前缀（v0.12 脱敏配置化；见 services/markets）
    market_prefixes: list[str] = []
    # 规范市场码表（码 → 展示名；Settings 页预置分市场阈值行用）
    market_codes: dict[str, str] = {}
    # 创意评分与生命周期（v0.13）：四要素权重 / 归档阈值 / 总开关
    score_weights: dict[str, float] = {}
    archive_score_threshold: float = 30.0
    archive_idle_days: int = 7
    lifecycle_auto_enabled: bool = True


class AiModelsInfo(BaseModel):
    """GET /settings/ai/models：端点可用的模型 id 列表。"""

    models: list[str]


class AiTestRequest(BaseModel):
    """POST /settings/ai/test：可选带 vision_model 验证其在列表中。"""

    vision_model: str | None = None


class AiTestResult(BaseModel):
    ok: bool
    message: str


class SettingsUpdate(BaseModel):
    # All fields optional: send only what changed. Empty api_key = keep current.
    api_key: str | None = None
    base_url: str | None = None
    vision_model: str | None = None
    embedding_model: str | None = None
    telemetry_enabled: bool | None = None
    merge_auto_enabled: bool | None = None
    metric_profile: list[str] | None = None
    judge_metrics: list[str] | None = None
    metric_thresholds: dict[str, float] | None = None
    market_thresholds: dict[str, dict[str, float]] | None = None
    project_category: str | None = None
    score_weights: dict[str, float] | None = None
    archive_score_threshold: float | None = None
    archive_idle_days: int | None = None
    lifecycle_auto_enabled: bool | None = None
    market_prefixes: list[str] | None = None
