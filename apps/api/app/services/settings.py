"""AI provider config resolution: DB settings table → env vars.

Supports any OpenAI-compatible provider (OpenAI, Kimi/Moonshot, DeepSeek,
OpenRouter, SiliconFlow …). The API uses the official ``openai`` SDK with a
configurable ``base_url`` — no provider-specific clients.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    OpenAI,
)
from sqlalchemy.orm import Session

from app.config import Settings
from app.repositories.settings import SettingsRepository

# Storage keys in the `settings` table. The key name is kept for backwards
# compatibility with rows written by the first MVP version.
API_KEY_SETTING = "openai_api_key"
BASE_URL_SETTING = "ai_base_url"
VISION_MODEL_SETTING = "ai_vision_model"
EMBEDDING_MODEL_SETTING = "ai_embedding_model"


@dataclass(frozen=True)
class AIConfig:
    api_key: str
    base_url: str
    vision_model: str
    embedding_model: str

    @property
    def is_moonshot(self) -> bool:
        return "moonshot" in self.base_url or "kimi" in self.base_url


def _resolve(db_value: str | None, env_value: str) -> str:
    # A present DB row always wins — including an explicitly empty value
    # (e.g. embedding_model="" disables embeddings on purpose). Only a
    # missing row falls back to the environment default.
    if db_value is not None:
        return db_value.strip()
    return env_value.strip()


def resolve_ai_config(db: Session, settings: Settings) -> AIConfig:
    repo = SettingsRepository(db)
    return AIConfig(
        api_key=_resolve(repo.get(API_KEY_SETTING), settings.openai_api_key),
        base_url=_resolve(repo.get(BASE_URL_SETTING), settings.openai_base_url),
        vision_model=_resolve(repo.get(VISION_MODEL_SETTING), settings.vision_model),
        embedding_model=_resolve(
            repo.get(EMBEDDING_MODEL_SETTING), settings.embedding_model
        ),
    )


def mask_api_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return key[:2] + "***"
    return f"{key[:4]}...{key[-4:]}"


# ---------------------------------------------------------------------------
# 模型自动发现与连接测试（Settings 页「获取模型列表」/「测试连接」）。
# 走 OpenAI 兼容端点的 GET {base_url}/models——大多数 provider 支持；
# 不支持的返回明确人话原因，前端保留手填。
# ---------------------------------------------------------------------------

# /models 只做发现/验证，超时收紧到 10s（默认 600s 会让设置页干等）
MODELS_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class ModelListResult:
    ok: bool
    models: list[str]
    # ok=False 时的人话原因（区分 401 / 超时 / 连接失败 / 端点不支持）
    message: str = ""


def list_provider_models(config: AIConfig) -> ModelListResult:
    """调 OpenAI 兼容端点的 /models 列出可用模型 id（按字母序）。"""
    if not config.api_key:
        return ModelListResult(False, [], "API key 未配置，请先保存")
    if not config.base_url:
        return ModelListResult(False, [], "Base URL 未配置，请先保存")
    client = OpenAI(
        api_key=config.api_key,
        base_url=config.base_url,
        timeout=MODELS_TIMEOUT_SECONDS,
        max_retries=0,  # 验证类调用不重试，失败立刻反馈
    )
    try:
        page = client.models.list()
    except AuthenticationError:
        return ModelListResult(False, [], "API key 无效或已过期（401）")
    except APITimeoutError:
        return ModelListResult(
            False, [], f"连接超时（{MODELS_TIMEOUT_SECONDS} 秒无响应），请检查 Base URL"
        )
    except APIConnectionError:
        return ModelListResult(False, [], "连接失败，请检查 Base URL 与网络")
    except APIStatusError as exc:
        if exc.status_code == 404:
            return ModelListResult(
                False,
                [],
                "端点返回 404——请检查 Base URL 是否完整"
                "（例如 Kimi 是 https://api.moonshot.cn/v1，注意 /v1）；"
                "若确认 URL 无误，则该端点不支持模型列表，请手填模型名",
            )
        return ModelListResult(False, [], f"端点返回错误（HTTP {exc.status_code}）")
    models = sorted({model.id for model in page.data})
    if not models:
        return ModelListResult(False, [], "连接正常，但端点返回的模型列表为空")
    return ModelListResult(True, models)


def test_provider_connection(
    config: AIConfig, vision_model: str | None = None
) -> tuple[bool, str]:
    """连通性测试：/models 能通 = key+URL 有效；带 vision_model 时验证其在列表中。"""
    result = list_provider_models(config)
    if not result.ok:
        return False, result.message
    if vision_model and vision_model not in result.models:
        return False, (
            f"连接正常，但模型 {vision_model} 不在列表中"
            f"（共 {len(result.models)} 个可用）"
        )
    return True, f"连接正常（{len(result.models)} 个模型可用）"


# ---------------------------------------------------------------------------
# Metric configuration (display profile + judge metrics + thresholds).
#
# Three settings rows, all JSON. Missing/invalid rows fall back to the
# defaults below, which mirror the UA team's documented priorities
# (AGENTS.md §6) — so an unconfigured install behaves exactly as before.
# ---------------------------------------------------------------------------

METRIC_PROFILE_SETTING = "metric_profile"
JUDGE_METRICS_SETTING = "judge_metrics"
METRIC_THRESHOLDS_SETTING = "metric_thresholds"
MARKET_THRESHOLDS_SETTING = "market_thresholds"
GENRE_SETTING = "project_category"

# 项目品类（onboarding 时选择；阈值档与遥测分桶都按它走）
GENRES = ("casual_slg", "idle_tycoon", "merge2", "rpg", "match3", "other")
DEFAULT_GENRE = "other"

# 可选显示指标全集（默认 profile 是前 6 个，顺序即展示顺序）
DISPLAY_METRICS = (
    "spend",
    "payers",
    "cpp",
    "d1_roas",
    "cpi",
    "ipm",
    "impressions",
    "clicks",
    "installs",
    "d3_roas",
    "d1_retention",
)
DEFAULT_METRIC_PROFILE = ["payers", "cpp", "spend", "d1_roas", "cpi", "ipm"]

# 可参与判定的指标（cpp/d1_roas 默认参与，d3_roas/次留需显式开启）
JUDGEABLE_METRICS = ("cpp", "d1_roas", "d3_roas", "d1_retention")
DEFAULT_JUDGE_METRICS = ["cpp", "d1_roas"]

DEFAULT_THRESHOLDS: dict[str, float] = {
    "cpp_red_line": 120.0,
    "cpp_pause_line": 180.0,
    "cpp_efficient": 60.0,
    "roas_green_line": 0.02,
    "roas_weak_line": 0.01,
    "d3_roas_weak_line": 0.04,
    "d1_retention_weak_line": 0.35,
}

# 品类默认阈值档（v0.12）：模拟经营/放置沿用全局默认；休闲类（二合/RPG/三消）
# 留存与回报要求更高——初值占位，后续按 aggregate_metric 回收的真实分布校准。
_GENRE_THRESHOLD_OVERRIDES: dict[str, dict[str, float]] = {
    "merge2": {"d1_retention_weak_line": 0.40, "roas_green_line": 0.03},
    "rpg": {"d1_retention_weak_line": 0.40, "roas_green_line": 0.03},
    "match3": {"d1_retention_weak_line": 0.40, "roas_green_line": 0.03},
}


def resolve_genre(db: Session) -> str:
    """当前项目品类；未设置/非法值一律按 other（全局默认阈值）。"""
    value = SettingsRepository(db).get(GENRE_SETTING) or ""
    return value if value in GENRES else DEFAULT_GENRE


@dataclass(frozen=True)
class MetricConfig:
    profile: list[str]
    judge_metrics: list[str]
    thresholds: dict[str, float]


def _resolve_str_list(
    db_value: str | None, *, allowed: tuple[str, ...], default: list[str]
) -> list[str]:
    if db_value is None:
        return list(default)
    try:
        value = json.loads(db_value)
    except (TypeError, ValueError):
        return list(default)
    if not isinstance(value, list):
        return list(default)
    filtered = [item for item in value if item in allowed]
    return filtered or list(default)


def resolve_metric_config(db: Session) -> MetricConfig:
    repo = SettingsRepository(db)
    # 品类默认档 → 用户显式覆盖（DB 行永远最高优先）
    thresholds = {
        **DEFAULT_THRESHOLDS,
        **_GENRE_THRESHOLD_OVERRIDES.get(resolve_genre(db), {}),
    }
    raw_thresholds = repo.get(METRIC_THRESHOLDS_SETTING)
    if raw_thresholds is not None:
        try:
            overrides = json.loads(raw_thresholds)
        except (TypeError, ValueError):
            overrides = None
        if isinstance(overrides, dict):
            for key, value in overrides.items():
                if key in thresholds and isinstance(value, (int, float)) and value > 0:
                    thresholds[key] = float(value)
    return MetricConfig(
        profile=_resolve_str_list(
            repo.get(METRIC_PROFILE_SETTING),
            allowed=DISPLAY_METRICS,
            default=DEFAULT_METRIC_PROFILE,
        ),
        judge_metrics=_resolve_str_list(
            repo.get(JUDGE_METRICS_SETTING),
            allowed=JUDGEABLE_METRICS,
            default=DEFAULT_JUDGE_METRICS,
        ),
        thresholds=thresholds,
    )


def resolve_market_threshold_overrides(db: Session) -> dict[str, dict[str, float]]:
    """settings 键 market_thresholds 的解析：{市场标签: {阈值键: 值}}。

    非法市场条目/未知阈值键/非正数值一律丢弃（与 metric_thresholds 同纪律）。
    """
    raw = SettingsRepository(db).get(MARKET_THRESHOLDS_SETTING)
    if raw is None:
        return {}
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    overrides: dict[str, dict[str, float]] = {}
    from app.services.markets import prefix_to_market_code

    for market, entries in payload.items():
        if not isinstance(market, str) or not market.strip():
            continue
        if not isinstance(entries, dict):
            continue
        valid = {
            key: float(value)
            for key, value in entries.items()
            if key in DEFAULT_THRESHOLDS
            and isinstance(value, (int, float))
            and value > 0
        }
        if valid:
            # 键归一为市场码（兼容未跑迁移脚本的旧前缀写法）
            overrides[prefix_to_market_code(market)] = valid
    return overrides


def resolve_thresholds(db: Session, market: str | None = None) -> dict[str, float]:
    """阈值解析优先级：市场覆盖 → 用户全局覆盖/品类档（resolve_metric_config）
    → 全局默认。market 为空或无该市场覆盖时与原行为完全一致。
    """
    thresholds = resolve_metric_config(db).thresholds
    if market:
        overrides = resolve_market_threshold_overrides(db).get(market.upper())
        if overrides:
            thresholds.update(overrides)
    return thresholds


# ---------------------------------------------------------------------------
# Creative score & lifecycle（v0.13）：四要素权重 + 自动归档阈值 + 总开关。
# ---------------------------------------------------------------------------

SCORE_WEIGHT_PERFORMANCE_SETTING = "score_weight_performance"
SCORE_WEIGHT_FRESHNESS_SETTING = "score_weight_freshness"
SCORE_WEIGHT_EVOLUTION_SETTING = "score_weight_evolution"
SCORE_WEIGHT_CONFIDENCE_SETTING = "score_weight_confidence"
ARCHIVE_SCORE_THRESHOLD_SETTING = "archive_score_threshold"
ARCHIVE_IDLE_DAYS_SETTING = "archive_idle_days"
LIFECYCLE_AUTO_ENABLED_SETTING = "lifecycle_auto_enabled"

DEFAULT_SCORE_WEIGHTS: dict[str, float] = {
    "performance": 40.0,
    "freshness": 25.0,
    "evolution": 20.0,
    "confidence": 15.0,
}
DEFAULT_ARCHIVE_SCORE_THRESHOLD = 30.0
DEFAULT_ARCHIVE_IDLE_DAYS = 7


@dataclass(frozen=True)
class ScoreConfig:
    """评分权重 + 归档阈值 + 评分用的效果锚点（取自指标阈值配置）。"""

    weight_performance: float = DEFAULT_SCORE_WEIGHTS["performance"]
    weight_freshness: float = DEFAULT_SCORE_WEIGHTS["freshness"]
    weight_evolution: float = DEFAULT_SCORE_WEIGHTS["evolution"]
    weight_confidence: float = DEFAULT_SCORE_WEIGHTS["confidence"]
    archive_score_threshold: float = DEFAULT_ARCHIVE_SCORE_THRESHOLD
    archive_idle_days: int = DEFAULT_ARCHIVE_IDLE_DAYS
    auto_enabled: bool = True
    cpp_efficient: float = DEFAULT_THRESHOLDS["cpp_efficient"]
    cpp_pause_line: float = DEFAULT_THRESHOLDS["cpp_pause_line"]
    roas_green_line: float = DEFAULT_THRESHOLDS["roas_green_line"]


def _resolve_float(db_value: str | None, default: float) -> float:
    if db_value is None:
        return default
    try:
        value = float(db_value)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def resolve_score_config(db: Session, market: str | None = None) -> ScoreConfig:
    """评分/生命周期配置：DB 行优先，缺省回落默认（未配置 = 旧行为）。

    market 给定（creative 主市场）时，效果锚点（cpp_efficient /
    cpp_pause_line / roas_green_line）走分市场阈值——不同市场 CPP 量级
    不同，用全局红线给高分市场打分会把好素材打成 0 分。
    """
    repo = SettingsRepository(db)
    metric_thresholds = resolve_thresholds(db, market)
    return ScoreConfig(
        weight_performance=_resolve_float(
            repo.get(SCORE_WEIGHT_PERFORMANCE_SETTING),
            DEFAULT_SCORE_WEIGHTS["performance"],
        ),
        weight_freshness=_resolve_float(
            repo.get(SCORE_WEIGHT_FRESHNESS_SETTING),
            DEFAULT_SCORE_WEIGHTS["freshness"],
        ),
        weight_evolution=_resolve_float(
            repo.get(SCORE_WEIGHT_EVOLUTION_SETTING),
            DEFAULT_SCORE_WEIGHTS["evolution"],
        ),
        weight_confidence=_resolve_float(
            repo.get(SCORE_WEIGHT_CONFIDENCE_SETTING),
            DEFAULT_SCORE_WEIGHTS["confidence"],
        ),
        archive_score_threshold=_resolve_float(
            repo.get(ARCHIVE_SCORE_THRESHOLD_SETTING), DEFAULT_ARCHIVE_SCORE_THRESHOLD
        ),
        archive_idle_days=int(
            _resolve_float(
                repo.get(ARCHIVE_IDLE_DAYS_SETTING), DEFAULT_ARCHIVE_IDLE_DAYS
            )
        ),
        auto_enabled=(repo.get(LIFECYCLE_AUTO_ENABLED_SETTING) or "true") != "false",
        cpp_efficient=metric_thresholds["cpp_efficient"],
        cpp_pause_line=metric_thresholds["cpp_pause_line"],
        roas_green_line=metric_thresholds["roas_green_line"],
    )
