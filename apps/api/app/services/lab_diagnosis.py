"""Creative Lab 定性诊断编排（M1）。

观察层（analysis_results）+ product-facts + 受众上下文 + 图证据 →
LLM（lab_prompts 诊断 prompt，max_tokens 4000）→ lab_validate 校验
（非法输出重试一次，仍非法则抛错不写库）→ evidence_label 服务端钳制
→ creative_diagnoses 按 (asset_id, prompt_version) 幂等 upsert。

业务异常由路由层映状态码：asset 缺失 404 / 观察层未就绪 409 /
AI key 未配置 409 / 诊断生成失败 502。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import AnalysisResult, CreativeDiagnosis
from app.repositories.analysis import AnalysisRepository
from app.repositories.assets import AssetRepository
from app.repositories.creative_diagnoses import CreativeDiagnosisRepository
from app.services.clustering import cosine_similarity
from app.services.lab_prompts import (
    DIAGNOSIS_SCHEMA,
    LAB_PROMPT_VERSION,
    diagnosis_system,
    diagnosis_user,
)
from app.services.lab_validate import validate
from app.services.llm_judge import complete_json
from app.services.settings import resolve_ai_config, resolve_product_facts

logger = logging.getLogger(__name__)

# 图证据相似度阈值：与聚类 CLUSTER_THRESHOLD 同口径（余弦 ≥ 0.85 视为同族证据）
GRAPH_EVIDENCE_THRESHOLD = 0.85

DIAGNOSIS_MAX_TOKENS = 4000


class LabAssetNotFoundError(RuntimeError):
    """asset 不存在（路由层映 404）。"""


class LabObservationNotReadyError(RuntimeError):
    """观察层未就绪：无 analysis_results 或分析未完成（路由层映 409）。"""


class LabAIKeyMissingError(RuntimeError):
    """AI key 未配置（路由层映 409，人话指向 Settings）。"""


class LabDiagnosisFailedError(RuntimeError):
    """诊断生成失败：两次输出均未过 schema 校验（路由层映 502，不写库）。"""


def observation_from_analysis(analysis: AnalysisResult) -> dict[str, Any]:
    """观察层 dict：analysis_results 字段照实装配（None 字段给 None，不编造）。"""
    return {
        "summary": analysis.summary,
        "hook": analysis.hook,
        "conflict": analysis.conflict,
        "gameplay": analysis.gameplay,
        "reward": analysis.reward,
        "characters": analysis.characters,
        "environment": analysis.environment,
        "emotion": analysis.emotion,
        "tags": analysis.tags,
        "variant_factors": analysis.variant_factors,
        "creative_name": analysis.creative_name,
    }


def graph_evidence_for(
    db: Session, analysis: AnalysisResult
) -> dict[str, Any] | None:
    """图证据：库内其它带 embedding 素材中与本条余弦相似度 ≥ 0.85 的数量。

    本条无 embedding 或库内无候选 → None（此时诊断 evidence_label 只能
    reference_only，由 run_diagnosis 服务端钳制兜底）。
    """
    embedding = analysis.embedding
    if not embedding:
        return None
    candidates = list(
        db.scalars(
            select(AnalysisResult).where(
                AnalysisResult.asset_id != analysis.asset_id,
                AnalysisResult.embedding.is_not(None),
            )
        ).all()
    )
    if not candidates:
        return None
    similar_count = sum(
        1
        for candidate in candidates
        if candidate.embedding
        and cosine_similarity(embedding, candidate.embedding) >= GRAPH_EVIDENCE_THRESHOLD
    )
    return {"similar_count": similar_count, "threshold": GRAPH_EVIDENCE_THRESHOLD}


def run_diagnosis(
    db: Session,
    settings: Settings,
    *,
    asset_id: str,
    audience: dict[str, Any] | None,
) -> CreativeDiagnosis:
    """跑一条素材的定性诊断并幂等落库，返回落库行（flush，不 commit）。"""
    asset = AssetRepository(db).get(asset_id)
    if asset is None:
        raise LabAssetNotFoundError(f"素材不存在：{asset_id}")

    analysis = AnalysisRepository(db).get_by_asset(asset.id)
    if analysis is None or asset.analysis_status != "completed":
        raise LabObservationNotReadyError(
            f"观察层未就绪：{asset_id}（analysis_status={asset.analysis_status}）"
            "——请先完成 AI 分析"
        )

    observation = observation_from_analysis(analysis)
    product_facts = resolve_product_facts(db) or None  # 空 dict 按未配置处理
    graph_evidence = graph_evidence_for(db, analysis)

    config = resolve_ai_config(db, settings)
    if not config.api_key:
        raise LabAIKeyMissingError(
            "AI API Key 未配置：请在 Settings 页面填写"
            "（支持 OpenAI / Kimi 等 OpenAI 兼容接口），"
            "或设置环境变量 OPENAI_API_KEY 后重试"
        )

    user_prompt = diagnosis_user(
        observation=observation,
        product_facts=product_facts,
        audience=audience,
        graph_evidence=graph_evidence,
    )

    # 非法输出（无返回 / schema 校验不过）重试一次；仍失败抛错，不写库
    output: dict[str, Any] | None = None
    last_errors: list[str] = []
    for _attempt in range(2):
        candidate = complete_json(
            config,
            system=diagnosis_system(),
            user=user_prompt,
            max_tokens=DIAGNOSIS_MAX_TOKENS,
        )
        if candidate is None:
            last_errors = ["LLM 未返回可用 JSON"]
            continue
        errors = validate(candidate, DIAGNOSIS_SCHEMA)
        if not errors:
            output = candidate
            break
        last_errors = errors
    if output is None:
        logger.warning("诊断输出两次均未通过校验 asset=%s errors=%s", asset_id, last_errors)
        raise LabDiagnosisFailedError(
            "诊断生成失败：模型两次输出均未通过 schema 校验"
            f"（{'; '.join(last_errors[:3])}）"
        )

    # evidence_label 服务端钳制：无图证据时强制 reference_only——
    # 不管模型自报什么（铁律 6 的兜底执行者）
    if graph_evidence is None:
        output["evidence_label"] = "reference_only"

    return CreativeDiagnosisRepository(db).upsert(
        asset.id,
        prompt_version=LAB_PROMPT_VERSION,
        observation_snapshot=observation,
        product_facts_snapshot=product_facts,
        audience_context=audience,
        output=output,
        evidence_label=str(output["evidence_label"]),
        engine_version=f"auto:{config.vision_model}",
    )
