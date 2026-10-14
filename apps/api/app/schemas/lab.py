"""Creative Lab DTOs（M1：诊断服务端点 + 词表缺口聚合视图）。"""

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
    # True = 跳过幂等/翻译分流，强制全量重诊断（覆盖写同键行）
    force_regenerate: bool = False


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
    # 另一语言当前版本报告存在与否：True 时切语言走翻译路径（秒级），
    # False 时切语言需全量重诊断（前端据此提示耗时差异）
    other_lang_available: bool
    graph_evidence: dict[str, Any] | None
    product_facts: dict[str, Any]


class VocabGapAssetRef(BaseModel):
    """缺口提名涉及的素材引用（asset_id + 文件名）。"""

    asset_id: str
    filename: str


class VocabGapItem(BaseModel):
    """按 (field, proposal) 聚合的一条词表缺口提名。"""

    field: str
    proposal: str
    # 提名该缺口的素材数（每 asset 只计一次：同血缘 zh/en 行与旧 prompt
    # 版本行按 asset 去重，只取最新一条诊断）
    count: int
    # 最新一次提名的理由
    rationale: str
    # 最近 3 个提名该缺口的素材（诊断 created_at 倒序）
    recent_assets: list[VocabGapAssetRef]


class VocabGapListOut(BaseModel):
    """Response data of ``GET /lab/vocab-gaps``。"""

    items: list[VocabGapItem]
    # count ≥ 该值的缺口建议进入词表增补评审（透传给前端作高亮线）
    suggest_threshold: int
