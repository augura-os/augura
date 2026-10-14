"""Creative Lab 路由（M1）：竞品参考上传 / 分析视图 / 定性诊断。

- ``POST /lab/references``：与 /upload 同一 ingest 核心，新建 asset 全部
  打 competitor-reference 来源标记（sticky，pipeline 重分析不抹）。
- ``GET /lab/analysis/{asset_id}?lang=``：观察层 + 当前 prompt 版本该语言
  诊断 + stale 标记（按语言维度）+ 另一语言报告可用性 + 图证据 +
  product-facts 的只读视图。
- ``POST /lab/diagnoses``：定性诊断落库（幂等 upsert）。默认分流：
  当前版本该语言已有报告 → 幂等直接返回；另一语言有 → LLM 翻译；
  都没有 → 全量诊断。``force_regenerate=true`` 跳过分流强制全量重跑。
"""

from __future__ import annotations

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, File, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import DbDep, SettingsDep, StorageDep
from app.api.presenters import to_asset_list_item
from app.config import Settings
from app.exceptions import ApiError
from app.models import CreativeDiagnosis
from app.repositories.analysis import AnalysisRepository
from app.repositories.assets import AssetRepository
from app.repositories.creative_diagnoses import CreativeDiagnosisRepository
from app.repositories.tags import TagRepository
from app.schemas.asset import UploadResult
from app.schemas.common import Envelope, ok
from app.schemas.lab import (
    AudienceContext,
    LabAnalysisView,
    LabDiagnosisOut,
    LabDiagnosisRequest,
)
from app.services import lab_diagnosis
from app.services.ingest import ingest_files
from app.services.lab_prompts import LAB_PROMPT_VERSION
from app.services.settings import resolve_product_facts

logger = logging.getLogger(__name__)

router = APIRouter()

# 竞品参考来源标记（repositories/tags.STICKY_SOURCE_TAGS 成员）
COMPETITOR_REFERENCE_TAG = "competitor-reference"


def _diagnosis_out(row: CreativeDiagnosis) -> LabDiagnosisOut:
    return LabDiagnosisOut(
        asset_id=row.asset_id,
        lang=row.lang,
        output=row.output,
        evidence_label=row.evidence_label,
        prompt_version=row.prompt_version,
        engine_version=row.engine_version,
        created_at=row.created_at,
    )


def _asset_not_found(asset_id: str) -> ApiError:
    # message 保留中文旧文案作 legacy fallback；前端按 code 渲染
    return ApiError(
        404,
        f"素材不存在：{asset_id}",
        code="asset_not_found",
        params={"id": asset_id},
    )


@router.post("/lab/references", response_model=Envelope[UploadResult])
def upload_references(
    db: DbDep,
    settings: SettingsDep,
    storage: StorageDep,
    files: Annotated[list[UploadFile], File(...)],
) -> Envelope[UploadResult]:
    outcome = ingest_files(db, settings, storage, files)
    tag_repo = TagRepository(db)
    for asset in outcome.created:
        # merge 语义：新 asset 此时无 tag，直接 set 即为并入
        tag_repo.set_asset_tags(asset.id, [COMPETITOR_REFERENCE_TAG])
    if outcome.created:
        db.commit()

    message = f"已上传 {len(outcome.created)} 个文件"
    if outcome.skipped:
        message += f"，跳过 {len(outcome.skipped)} 个重复"
    return ok(
        UploadResult(
            uploaded=[to_asset_list_item(asset, None) for asset in outcome.created],
            skipped=outcome.skipped,
            warnings=outcome.warnings,
        ),
        message=message,
    )


@router.get("/lab/analysis/{asset_id}", response_model=Envelope[LabAnalysisView])
def get_lab_analysis(
    db: DbDep, asset_id: str, lang: Literal["zh", "en"] = "zh"
) -> Envelope[LabAnalysisView]:
    asset = AssetRepository(db).get(asset_id)
    if asset is None:
        raise _asset_not_found(asset_id)
    analysis = AnalysisRepository(db).get_by_asset(asset.id)
    diagnosis_repo = CreativeDiagnosisRepository(db)
    # 报告按 (asset, prompt_version, lang) 分行：切语言读对应语言的行；
    # 该语言无任何行时 diagnosis=None 且 stale=False（前端自动生成）
    current = diagnosis_repo.get_current(asset.id, LAB_PROMPT_VERSION, lang)
    latest = diagnosis_repo.get_latest(asset.id, lang)
    other_lang = "en" if lang == "zh" else "zh"
    other_lang_available = (
        diagnosis_repo.get_current(asset.id, LAB_PROMPT_VERSION, other_lang)
        is not None
    )
    return ok(
        LabAnalysisView(
            asset_id=asset.id,
            analysis_status=asset.analysis_status,
            observation=(
                lab_diagnosis.observation_from_analysis(analysis)
                if analysis is not None
                else None
            ),
            diagnosis=_diagnosis_out(current) if current is not None else None,
            diagnosis_stale=(
                latest is not None and latest.prompt_version != LAB_PROMPT_VERSION
            ),
            other_lang_available=other_lang_available,
            graph_evidence=(
                lab_diagnosis.graph_evidence_for(db, analysis)
                if analysis is not None
                else None
            ),
            product_facts=resolve_product_facts(db),
        )
    )


def _audience_dict(audience: AudienceContext | None) -> dict[str, str] | None:
    """受众上下文转 dict：只带填了的字段；全空按未提供（None）。"""
    if audience is None:
        return None
    values = {key: value for key, value in audience.model_dump().items() if value}
    return values or None


@router.post("/lab/diagnoses", response_model=Envelope[LabDiagnosisOut])
def create_diagnosis(
    payload: LabDiagnosisRequest, db: DbDep, settings: SettingsDep
) -> Envelope[LabDiagnosisOut]:
    try:
        if payload.force_regenerate:
            # 强制全量重诊断：跳过幂等命中与翻译分流
            row = lab_diagnosis.run_diagnosis(
                db,
                settings,
                asset_id=payload.asset_id,
                audience=_audience_dict(payload.audience),
                lang=payload.lang,
            )
        else:
            row = _diagnose_idempotent(db, settings, payload)
    except lab_diagnosis.LabAssetNotFoundError:
        raise _asset_not_found(payload.asset_id) from None
    except lab_diagnosis.LabObservationNotReadyError as exc:
        raise ApiError(409, str(exc), code="lab_observation_not_ready") from None
    except lab_diagnosis.LabAIKeyMissingError as exc:
        raise ApiError(409, str(exc), code="ai_key_not_configured") from None
    except lab_diagnosis.LabDiagnosisFailedError as exc:
        raise ApiError(502, str(exc), code="lab_diagnosis_invalid_output") from None
    db.commit()
    return ok(_diagnosis_out(row), message="诊断完成")


def _diagnose_idempotent(
    db: Session, settings: Settings, payload: LabDiagnosisRequest
) -> CreativeDiagnosis:
    """幂等分流：当前版本该语言已有报告 → 直接返回（不花 LLM）；
    另一语言有当前版本报告 → 翻译（译文两次未过校验降级全量重诊断）；
    都没有 → 全量诊断。
    """
    repo = CreativeDiagnosisRepository(db)
    current = repo.get_current(payload.asset_id, LAB_PROMPT_VERSION, payload.lang)
    if current is not None:
        return current
    other_lang = "en" if payload.lang == "zh" else "zh"
    source = repo.get_current(payload.asset_id, LAB_PROMPT_VERSION, other_lang)
    if source is not None:
        try:
            return lab_diagnosis.run_translation(
                db,
                settings,
                asset_id=payload.asset_id,
                source_row=source,
                target_lang=payload.lang,
            )
        except lab_diagnosis.TranslationInvalidError:
            logger.warning(
                "翻译降级为全量重诊断 asset=%s lang=%s",
                payload.asset_id,
                payload.lang,
            )
    return lab_diagnosis.run_diagnosis(
        db,
        settings,
        asset_id=payload.asset_id,
        audience=_audience_dict(payload.audience),
        lang=payload.lang,
    )
