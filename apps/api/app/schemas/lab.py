"""Creative Lab DTOs（M1：诊断服务三端点）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel


class AudienceContext(BaseModel):
    """诊断受众上下文（全可选 string；全空按未提供处理）。"""

    gender: str | None = None
    age: str | None = None
    region: str | None = None
    category: str | None = None


class LabDiagnosisRequest(BaseModel):
    """Body of ``POST /lab/diagnoses``."""

    asset_id: str
    audience: AudienceContext | None = None
    # 诊断报告输出语言（仅换 prompt 第 8 条铁律；zh 文案逐字不变）
    lang: Literal["zh", "en"] = "zh"


class LabDiagnosisOut(BaseModel):
    """落库的定性诊断（output 为六要素诊断 JSON，结构见 DIAGNOSIS_SCHEMA）。"""

    asset_id: str
    # 该次诊断的输出语言（creative_diagnoses.lang 列，upsert 键之一）
    lang: str
    output: dict[str, Any]
    evidence_label: str
    prompt_version: str
    engine_version: str
    created_at: datetime


class LabAnalysisView(BaseModel):
    """Response data of ``GET /lab/analysis/{asset_id}``：观察层 + 当前语言诊断 + 证据。"""

    asset_id: str
    analysis_status: str
    observation: dict[str, Any] | None
    diagnosis: LabDiagnosisOut | None
    # 请求语言下 latest 诊断存在但 prompt_version 落后于当前版本 = 可重诊断；
    # 该语言无任何行 → diagnosis=None 且 stale=False（前端自动生成）
    diagnosis_stale: bool
    graph_evidence: dict[str, Any] | None
    product_facts: dict[str, Any]
