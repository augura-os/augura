"""Creative Lab 历史参考列表（GET /lab/references 的数据源）。

带 competitor-reference 标签（repositories/tags.STICKY_SOURCE_TAGS 成员，
POST /lab/references 上传时统一打标）且生命周期为 active 的素材。
lifecycle_state 是所属 creative 的属性：未归族素材（outer join 出 NULL）
按 active 计——同 GET /assets 列表与 presenters.to_asset_list_item 的
None 约定。

每条附带诊断语言分组（前端「可重诊断」提示的数据源）：

- diagnosis_current_langs：有当前 prompt 版本（LAB_PROMPT_VERSION）
  诊断行的语言；
- diagnosis_stale_langs：有诊断行但只剩旧 prompt 版本的语言（与
  current 不重叠——同语言新旧版本行并存时归 current）。

诊断行按 asset 一次 IN 查询取出后在 Python 侧分组（同 lab_vocab_gaps
的取舍：Lab 数据量级为资产数，不逐 asset 循环查）。
"""

from __future__ import annotations

from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AnalysisResult,
    Creative,
    CreativeAsset,
    CreativeDiagnosis,
    CreativeVariant,
    Tag,
    TagAssignment,
)
from app.repositories.tags import COMPETITOR_REFERENCE_TAG
from app.services.lab_prompts import LAB_PROMPT_VERSION


class ReferenceRow(NamedTuple):
    """一条参考素材的列表数据（AssetListItem 入参 + 诊断语言分组）。"""

    asset: CreativeAsset
    creative_name: str | None
    confidence: float | None
    lifecycle_state: str | None
    diagnosis_current_langs: list[str]
    diagnosis_stale_langs: list[str]


def _reference_rows(
    db: Session,
) -> list[tuple[CreativeAsset, str | None, float | None, str | None]]:
    """带 competitor-reference 标签的 active 素材（created_at 倒序）。

    返回形状同 AssetRepository.list_with_creative：
    (asset, creative_name, confidence, lifecycle_state)。
    """
    stmt = (
        select(
            CreativeAsset,
            Creative.name,
            AnalysisResult.confidence,
            Creative.lifecycle_state,
        )
        .join(TagAssignment, TagAssignment.asset_id == CreativeAsset.id)
        .join(Tag, Tag.id == TagAssignment.tag_id)
        .outerjoin(CreativeVariant, CreativeVariant.asset_id == CreativeAsset.id)
        .outerjoin(Creative, Creative.id == CreativeVariant.creative_id)
        .outerjoin(AnalysisResult, AnalysisResult.asset_id == CreativeAsset.id)
        .where(Tag.name == COMPETITOR_REFERENCE_TAG)
        # 未归族素材（无 creative，join 出 NULL）视为 active
        .where(
            (Creative.lifecycle_state == "active")
            | (Creative.lifecycle_state.is_(None))
        )
        .order_by(CreativeAsset.created_at.desc())
    )
    return [
        (asset, creative_name, confidence, lifecycle_state)
        for asset, creative_name, confidence, lifecycle_state in db.execute(stmt).all()
    ]


def _diagnosis_lang_groups(
    db: Session, asset_ids: list[str]
) -> dict[str, tuple[list[str], list[str]]]:
    """每 asset 的 (current_langs, stale_langs)，两组内均按语言名排序。"""
    if not asset_ids:
        return {}
    stmt = select(
        CreativeDiagnosis.asset_id,
        CreativeDiagnosis.lang,
        CreativeDiagnosis.prompt_version,
    ).where(CreativeDiagnosis.asset_id.in_(asset_ids))
    current: dict[str, set[str]] = {}
    stale: dict[str, set[str]] = {}
    for asset_id, lang, prompt_version in db.execute(stmt):
        target = current if prompt_version == LAB_PROMPT_VERSION else stale
        target.setdefault(asset_id, set()).add(lang)
    return {
        asset_id: (
            sorted(current.get(asset_id, ())),
            sorted(stale.get(asset_id, set()) - current.get(asset_id, set())),
        )
        for asset_id in set(current) | set(stale)
    }


def list_reference_rows(db: Session) -> list[ReferenceRow]:
    """GET /lab/references 的行数据（created_at 倒序），presenter 在路由侧套用。"""
    rows = _reference_rows(db)
    lang_groups = _diagnosis_lang_groups(db, [asset.id for asset, *_ in rows])
    items: list[ReferenceRow] = []
    for asset, creative_name, confidence, lifecycle_state in rows:
        current_langs, stale_langs = lang_groups.get(asset.id, ([], []))
        items.append(
            ReferenceRow(
                asset=asset,
                creative_name=creative_name,
                confidence=confidence,
                lifecycle_state=lifecycle_state,
                diagnosis_current_langs=current_langs,
                diagnosis_stale_langs=stale_langs,
            )
        )
    return items
