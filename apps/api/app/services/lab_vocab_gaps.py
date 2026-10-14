"""Creative Lab 词表缺口聚合（GET /lab/vocab-gaps 的数据源）。

诊断输出的可选 ``vocab_gaps`` 数组（lab_prompts.DIAGNOSIS_SCHEMA 第 10 条
铁律的提名位）随整份 output 落库（creative_diagnoses.output，JSONB）。
本模块把全库提名按 (field, proposal) 聚合，供词表增补评审使用：

- 每 asset 只取最新一条带 vocab_gaps 的诊断行——同血缘 zh/en 行
  （lab_diagnosis.run_translation 翻译路径）vocab_gaps 相同，按 asset
  去重一次即可；同 asset 多 prompt 版本只留最新，旧版本提名视为已被
  新诊断覆盖。
- count = 提名该 (field, proposal) 的素材数；rationale 取最新一次提名；
  recent_assets 为最近 3 个素材（join creative_assets 取 filename）。

聚合在 Python 侧完成（与 lab_diagnosis.graph_evidence_for 同一取舍：
Lab 数据量级为资产数，一次 per-asset 最新行扫描远小于图证据全量比对）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CreativeAsset, CreativeDiagnosis
from app.schemas.lab import VocabGapAssetRef, VocabGapItem

# count ≥ 该值的缺口建议进入词表增补评审（随响应透传给前端作高亮线）
SUGGEST_THRESHOLD: Final = 2


def _latest_gap_rows(db: Session) -> list[tuple[str, datetime, Any]]:
    """每 asset 最新一条带 vocab_gaps 的诊断行：(asset_id, created_at, gaps)。"""
    stmt = (
        select(
            CreativeDiagnosis.asset_id,
            CreativeDiagnosis.created_at,
            CreativeDiagnosis.output["vocab_gaps"],
        )
        .where(CreativeDiagnosis.output.has_key("vocab_gaps"))
        .distinct(CreativeDiagnosis.asset_id)
        .order_by(
            CreativeDiagnosis.asset_id,
            CreativeDiagnosis.created_at.desc(),
            # 同事务落库行 created_at 并列（func.now() 取事务起点）时的稳定决胜
            CreativeDiagnosis.id.desc(),
        )
    )
    return [(asset_id, created_at, gaps) for asset_id, created_at, gaps in db.execute(stmt)]


def aggregate_vocab_gaps(db: Session) -> list[VocabGapItem]:
    """全库 vocab_gaps 按 (field, proposal) 聚合，count 降序。

    同 count 按 (field, proposal) 字典序，保证输出稳定。缺 field/proposal
    的畸形提名项跳过（validate 已拦 required，这里是读侧防御存量脏数据）。
    """
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for asset_id, created_at, gaps in _latest_gap_rows(db):
        for gap in gaps or []:
            if not isinstance(gap, dict):
                continue
            field, proposal = gap.get("field"), gap.get("proposal")
            if not field or not proposal:
                continue
            entry = groups.setdefault(
                (str(field), str(proposal)),
                {"assets": [], "rationale": "", "latest_at": None},
            )
            entry["assets"].append((created_at, asset_id))
            if entry["latest_at"] is None or created_at >= entry["latest_at"]:
                entry["latest_at"] = created_at
                entry["rationale"] = str(gap.get("rationale") or "")

    asset_ids = {
        asset_id for entry in groups.values() for _, asset_id in entry["assets"]
    }
    filenames: dict[str, str] = {}
    if asset_ids:
        stmt = select(CreativeAsset.id, CreativeAsset.filename).where(
            CreativeAsset.id.in_(asset_ids)
        )
        filenames = {asset_id: filename for asset_id, filename in db.execute(stmt)}

    items = [
        VocabGapItem(
            field=field,
            proposal=proposal,
            # 每 asset 只贡献一行（_latest_gap_rows 已按 asset 去重）→ 条数即素材数
            count=len(entry["assets"]),
            rationale=entry["rationale"],
            recent_assets=[
                VocabGapAssetRef(
                    asset_id=asset_id, filename=filenames.get(asset_id, "")
                )
                for _, asset_id in sorted(
                    entry["assets"], key=lambda pair: pair[0], reverse=True
                )[:3]
            ],
        )
        for (field, proposal), entry in groups.items()
    ]
    items.sort(key=lambda item: (-item.count, item.field, item.proposal))
    return items
