"""Settings routes: GET / PUT AI provider config (OpenAI-compatible)."""

from __future__ import annotations

import json
import logging
import re

from fastapi import APIRouter

from app.api.deps import DbDep, SettingsDep
from app.exceptions import ApiError
from app.repositories.settings import SettingsRepository
from app.schemas.common import Envelope, ok
from app.schemas.settings import (
    AiModelsInfo,
    AiTestRequest,
    AiTestResult,
    SettingsInfo,
    SettingsUpdate,
)
from app.services.markets import (
    MARKET_CODES,
    MARKET_PREFIXES_SETTING,
    prefix_to_market_code,
    resolve_market_prefixes,
)
from app.services.settings import (
    API_KEY_SETTING,
    ARCHIVE_IDLE_DAYS_SETTING,
    ARCHIVE_SCORE_THRESHOLD_SETTING,
    BASE_URL_SETTING,
    DEFAULT_THRESHOLDS,
    DISPLAY_METRICS,
    EMBEDDING_MODEL_SETTING,
    GENRE_SETTING,
    GENRES,
    JUDGE_METRICS_SETTING,
    JUDGEABLE_METRICS,
    LIFECYCLE_AUTO_ENABLED_SETTING,
    MARKET_THRESHOLDS_SETTING,
    METRIC_PROFILE_SETTING,
    METRIC_THRESHOLDS_SETTING,
    SCORE_WEIGHT_CONFIDENCE_SETTING,
    SCORE_WEIGHT_EVOLUTION_SETTING,
    SCORE_WEIGHT_FRESHNESS_SETTING,
    SCORE_WEIGHT_PERFORMANCE_SETTING,
    VISION_MODEL_SETTING,
    list_provider_models,
    mask_api_key,
    resolve_ai_config,
    resolve_market_threshold_overrides,
    resolve_metric_config,
    resolve_score_config,
    test_provider_connection,
)

TELEMETRY_ENABLED_SETTING = "telemetry_enabled"
TELEMETRY_INSTANCE_ID_SETTING = "telemetry_instance_id"
MERGE_AUTO_ENABLED_SETTING = "merge_auto_enabled"

_SCORE_WEIGHT_KEYS = {
    "performance": SCORE_WEIGHT_PERFORMANCE_SETTING,
    "freshness": SCORE_WEIGHT_FRESHNESS_SETTING,
    "evolution": SCORE_WEIGHT_EVOLUTION_SETTING,
    "confidence": SCORE_WEIGHT_CONFIDENCE_SETTING,
}


def _resolve_instance_id(repo: SettingsRepository, db_commit) -> str:  # noqa: ANN001
    """Get or lazily create the anonymous instance id (PRIVACY.md §1.1)."""
    from telemetry.anonymize import new_instance_id

    existing = repo.get(TELEMETRY_INSTANCE_ID_SETTING)
    if existing:
        return existing
    instance_id = new_instance_id()
    repo.set(TELEMETRY_INSTANCE_ID_SETTING, instance_id)
    db_commit()
    return instance_id

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/settings", response_model=Envelope[SettingsInfo])
def get_settings_info(db: DbDep, settings: SettingsDep) -> Envelope[SettingsInfo]:
    config = resolve_ai_config(db, settings)
    metric_config = resolve_metric_config(db)
    score_config = resolve_score_config(db)
    repo = SettingsRepository(db)
    enabled_raw = repo.get(TELEMETRY_ENABLED_SETTING)
    return ok(
        SettingsInfo(
            api_key_set=bool(config.api_key),
            api_key_masked=mask_api_key(config.api_key),
            base_url=config.base_url,
            vision_model=config.vision_model,
            embedding_model=config.embedding_model,
            telemetry_enabled=enabled_raw != "false",  # 默认开启（PRIVACY.md §1.1）
            telemetry_instance_id=_resolve_instance_id(repo, db.commit),
            merge_auto_enabled=repo.get(MERGE_AUTO_ENABLED_SETTING) != "false",  # 默认开
            metric_profile=metric_config.profile,
            judge_metrics=metric_config.judge_metrics,
            metric_thresholds=metric_config.thresholds,
            market_thresholds=resolve_market_threshold_overrides(db),
            project_category=repo.get(GENRE_SETTING) or "",
            market_prefixes=list(resolve_market_prefixes(db)),
            market_codes=dict(MARKET_CODES),
            score_weights={
                "performance": score_config.weight_performance,
                "freshness": score_config.weight_freshness,
                "evolution": score_config.weight_evolution,
                "confidence": score_config.weight_confidence,
            },
            archive_score_threshold=score_config.archive_score_threshold,
            archive_idle_days=score_config.archive_idle_days,
            lifecycle_auto_enabled=score_config.auto_enabled,
        )
    )


@router.put("/settings", response_model=Envelope[SettingsInfo])
def update_settings(
    payload: SettingsUpdate, db: DbDep, settings: SettingsDep
) -> Envelope[SettingsInfo]:
    repo = SettingsRepository(db)
    if payload.api_key is not None and payload.api_key.strip():
        repo.set(API_KEY_SETTING, payload.api_key.strip())
    if payload.base_url is not None:
        base_url = payload.base_url.strip()
        if base_url and not base_url.startswith(("http://", "https://")):
            raise ApiError(400, "base_url 必须以 http:// 或 https:// 开头")
        if base_url:
            repo.set(BASE_URL_SETTING, base_url.rstrip("/"))
    if payload.vision_model is not None and payload.vision_model.strip():
        repo.set(VISION_MODEL_SETTING, payload.vision_model.strip())
    if payload.embedding_model is not None:
        # 允许显式置空：空字符串 = 不调用 embedding 接口，聚类走本地文本相似度
        repo.set(EMBEDDING_MODEL_SETTING, payload.embedding_model.strip())
    if payload.telemetry_enabled is not None:
        repo.set(
            TELEMETRY_ENABLED_SETTING, "true" if payload.telemetry_enabled else "false"
        )
    if payload.merge_auto_enabled is not None:
        repo.set(
            MERGE_AUTO_ENABLED_SETTING,
            "true" if payload.merge_auto_enabled else "false",
        )
    if payload.metric_profile is not None:
        unknown = [m for m in payload.metric_profile if m not in DISPLAY_METRICS]
        if unknown:
            raise ApiError(400, f"未知显示指标：{', '.join(unknown)}")
        if not payload.metric_profile:
            raise ApiError(400, "显示指标至少保留一个")
        repo.set(METRIC_PROFILE_SETTING, json.dumps(payload.metric_profile))
    if payload.judge_metrics is not None:
        unknown = [m for m in payload.judge_metrics if m not in JUDGEABLE_METRICS]
        if unknown:
            raise ApiError(400, f"未知判定指标：{', '.join(unknown)}")
        repo.set(JUDGE_METRICS_SETTING, json.dumps(payload.judge_metrics))
    if payload.metric_thresholds is not None:
        for key, value in payload.metric_thresholds.items():
            if key not in DEFAULT_THRESHOLDS:
                raise ApiError(400, f"未知阈值项：{key}")
            if value <= 0:
                raise ApiError(400, f"阈值必须为正数：{key}")
        repo.set(METRIC_THRESHOLDS_SETTING, json.dumps(payload.metric_thresholds))
    if payload.market_thresholds is not None:
        for market, entries in payload.market_thresholds.items():
            if not market.strip():
                raise ApiError(400, "市场标签不能为空")
            for key, value in entries.items():
                if key not in DEFAULT_THRESHOLDS:
                    raise ApiError(400, f"未知阈值项：{market}.{key}")
                if value <= 0:
                    raise ApiError(400, f"阈值必须为正数：{market}.{key}")
        repo.set(
            MARKET_THRESHOLDS_SETTING,
            json.dumps(
                {
                    # 键归一为规范市场码（ABCBR → PT、EN → 别名 US）
                    prefix_to_market_code(market): entries
                    for market, entries in payload.market_thresholds.items()
                    if entries
                }
            ),
        )
    if payload.project_category is not None:
        if payload.project_category not in GENRES:
            raise ApiError(400, f"未知项目品类：{payload.project_category}")
        repo.set(GENRE_SETTING, payload.project_category)
    if payload.score_weights is not None:
        unknown = [k for k in payload.score_weights if k not in _SCORE_WEIGHT_KEYS]
        if unknown:
            raise ApiError(400, f"未知评分要素：{', '.join(unknown)}")
        for key, value in payload.score_weights.items():
            if value <= 0:
                raise ApiError(400, f"权重必须为正数：{key}")
            repo.set(_SCORE_WEIGHT_KEYS[key], str(value))
    if payload.archive_score_threshold is not None:
        if payload.archive_score_threshold <= 0:
            raise ApiError(400, "归档评分阈值必须为正数")
        repo.set(ARCHIVE_SCORE_THRESHOLD_SETTING, str(payload.archive_score_threshold))
    if payload.archive_idle_days is not None:
        if payload.archive_idle_days <= 0:
            raise ApiError(400, "归档闲置天数必须为正数")
        repo.set(ARCHIVE_IDLE_DAYS_SETTING, str(payload.archive_idle_days))
    if payload.lifecycle_auto_enabled is not None:
        repo.set(
            LIFECYCLE_AUTO_ENABLED_SETTING,
            "true" if payload.lifecycle_auto_enabled else "false",
        )
    if payload.market_prefixes is not None:
        cleaned = [x.strip().upper() for x in payload.market_prefixes if x.strip()]
        bad = [x for x in cleaned if not MARKET_PREFIX_RE.fullmatch(x)]
        if bad:
            raise ApiError(
                400, f"市场前缀必须是 2-8 位大写字母/下划线：{', '.join(bad)}"
            )
        repo.set(MARKET_PREFIXES_SETTING, ",".join(cleaned))
    db.commit()
    return get_settings_info(db, settings)


# 市场前缀格式：2-8 位大写字母/下划线（KS_EN、BR 等）
MARKET_PREFIX_RE = re.compile(r"[A-Z_]{2,8}")


@router.get("/settings/ai/models", response_model=Envelope[AiModelsInfo])
def list_ai_models(db: DbDep, settings: SettingsDep) -> Envelope[AiModelsInfo]:
    """模型自动发现：用已存的 base_url + api_key 调端点的 /models。

    失败（key 错/超时/连接失败/端点不支持）返回 400 + 人话原因，
    前端据提示保留手填。
    """
    result = list_provider_models(resolve_ai_config(db, settings))
    if not result.ok:
        raise ApiError(400, result.message)
    return ok(AiModelsInfo(models=result.models))


@router.post("/settings/ai/test", response_model=Envelope[AiTestResult])
def test_ai_connection(
    payload: AiTestRequest, db: DbDep, settings: SettingsDep
) -> Envelope[AiTestResult]:
    """连接测试：/models 验证 key+URL；带 vision_model 时验证它在列表中。"""
    ok_flag, message = test_provider_connection(
        resolve_ai_config(db, settings),
        vision_model=(payload.vision_model or "").strip() or None,
    )
    return ok(AiTestResult(ok=ok_flag, message=message))
