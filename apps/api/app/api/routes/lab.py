"""Creative Lab 路由（M1）：竞品参考上传 / 分析视图 / 定性诊断。

- ``POST /lab/references``：与 /upload 同一 ingest 核心，新建 asset 全部
  打 competitor-reference 来源标记（sticky，pipeline 重分析不抹）。
- ``GET /lab/analysis/{asset_id}``：观察层 + 当前 prompt 版本诊断 +
  stale 标记 + 图证据 + product-facts 的只读视图。
- ``POST /lab/diagnoses``：同步跑定性诊断并落库（幂等 upsert）。
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, File, UploadFile

from app.api.deps import DbDep, SettingsDep, StorageDep
from app.api.presenters import to_asset_list_item
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
def get_lab_analysis(db: DbDep, asset_id: str) -> Envelope[LabAnalysisView]:
    asset = AssetRepository(db).get(asset_id)
    if asset is None:
        raise _asset_not_found(asset_id)
    analysis = AnalysisRepository(db).get_by_asset(asset.id)
    diagnosis_repo = CreativeDiagnosisRepository(db)
    current = diagnosis_repo.get_current(asset.id, LAB_PROMPT_VERSION)
    latest = diagnosis_repo.get_latest(asset.id)
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
        row = lab_diagnosis.run_diagnosis(
            db, settings, asset_id=payload.asset_id, audience=_audience_dict(payload.audience)
        )
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
