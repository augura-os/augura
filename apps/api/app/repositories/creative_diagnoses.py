"""CreativeDiagnosis persistence."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CreativeDiagnosis


class CreativeDiagnosisRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_current(
        self, asset_id: str, prompt_version: str, lang: str
    ) -> CreativeDiagnosis | None:
        """指定 prompt 版本与语言的当前诊断行（幂等 upsert 键的读侧）。"""
        return self.db.scalar(
            select(CreativeDiagnosis).where(
                CreativeDiagnosis.asset_id == asset_id,
                CreativeDiagnosis.prompt_version == prompt_version,
                CreativeDiagnosis.lang == lang,
            )
        )

    def get_latest(self, asset_id: str, lang: str) -> CreativeDiagnosis | None:
        """指定语言下最新一条诊断（跨 prompt 版本，created_at 倒序取一）。"""
        return self.db.scalar(
            select(CreativeDiagnosis)
            .where(
                CreativeDiagnosis.asset_id == asset_id,
                CreativeDiagnosis.lang == lang,
            )
            .order_by(CreativeDiagnosis.created_at.desc())
            .limit(1)
        )

    def upsert(
        self,
        asset_id: str,
        *,
        prompt_version: str,
        lang: str,
        observation_snapshot: dict[str, object],
        output: dict[str, object],
        evidence_label: str,
        engine_version: str,
        product_facts_snapshot: dict[str, object] | None = None,
        audience_context: dict[str, object] | None = None,
    ) -> CreativeDiagnosis:
        """同素材同 prompt 版本同语言幂等写入：已存在则整体覆盖输出与输入快照。

        (asset_id, prompt_version, lang) 是唯一约束键——同版本同语言重诊断
        不堆行；prompt 升级（LAB_PROMPT_VERSION bump）或换语言各起新行，
        旧行保留可回归对比。updated_at 由 TimestampMixin.onupdate 自动刷新。
        """
        row = self.get_current(asset_id, prompt_version, lang)
        if row is None:
            row = CreativeDiagnosis(
                asset_id=asset_id, prompt_version=prompt_version, lang=lang
            )
            self.db.add(row)
        row.observation_snapshot = observation_snapshot
        row.product_facts_snapshot = product_facts_snapshot
        row.audience_context = audience_context
        row.output = output
        row.evidence_label = evidence_label
        row.engine_version = engine_version
        self.db.flush()
        return row
