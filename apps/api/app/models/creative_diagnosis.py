"""CreativeDiagnosis — Creative Lab 定性诊断结果（M1）。

LLM 对单条素材的「定性报告」落库行：observation_snapshot /
product_facts_snapshot / audience_context 是诊断输入快照（写完冻结，
可复盘"当时看到了什么"），output 是六要素诊断输出（结构见
services/lab_prompts.DIAGNOSIS_SCHEMA）。

(asset_id, prompt_version) 唯一 = 幂等 upsert 键：同素材同 prompt 版本
重诊断覆盖写（不堆行），prompt 升级（services/lab_prompts
LAB_PROMPT_VERSION bump）产生新行，旧版本行保留可回归对比。
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, new_uuid


class CreativeDiagnosis(TimestampMixin, Base):
    __tablename__ = "creative_diagnoses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    asset_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("creative_assets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 观察层输入快照（诊断时所见即所存）
    observation_snapshot: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    # product-facts 配置快照（settings product_facts；未配置存 NULL）
    product_facts_snapshot: Mapped[dict[str, object] | None] = mapped_column(
        JSONB, nullable=True
    )
    # 受众上下文快照（可选）
    audience_context: Mapped[dict[str, object] | None] = mapped_column(
        JSONB, nullable=True
    )
    # 诊断输出六要素
    output: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    # 证据标签（lab_taxonomy.EVIDENCE_LABELS，如 reference_only）
    evidence_label: Mapped[str] = mapped_column(String(32), nullable=False)
    # 诊断出自哪版 prompt（services/lab_prompts.LAB_PROMPT_VERSION）
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    # 产出诊断的引擎（格式同 analysis_results.engine_version）
    engine_version: Mapped[str] = mapped_column(String(128), nullable=False, default="")

    __table_args__ = (
        UniqueConstraint(
            "asset_id", "prompt_version", name="uq_creative_diagnoses_asset_prompt"
        ),
    )
