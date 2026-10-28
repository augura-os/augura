"""DNA 家族停用/启用 + 批量收件箱操作的服务层（P0a 契约 §1、§2）。

路由只做参数解析与 commit / Neo4j / rebuild_mirror 收口；逐条语义全部
在这里，单项 begin_nested（savepoint）隔离——单项失败仅回滚该项，
不拖垮整批。本模块一律不 commit（调用方统一提交）。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.exceptions import ApiError
from app.models import Creative, CreativeDNA, JudgeSuggestion
from app.repositories.creatives import CreativeRepository
from app.repositories.dna import DnaRepository
from app.repositories.edit_logs import EditLogRepository
from app.schemas.batch import BatchItemResult, BatchResult, collect
from app.services import family_bootstrap

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# 建族发号（手动建族与智能建族确认共用）
# ----------------------------------------------------------------------
def _next_code(db: Session) -> str:
    """Server-side numbering: max existing D-sequence + 1, formatted D%02d.

    code 不复用（契约 §1.2）：archived/merged 家族的 code 永久保留，发号
    必须看全部状态，不能按 active 过滤。
    """
    codes = db.scalars(select(CreativeDNA.code)).all()
    max_n = max(
        (int(c[1:]) for c in codes if re.fullmatch(r"D\d+", c)),
        default=0,
    )
    return f"D{max_n + 1:02d}"


def create_dna_numbered(
    db: Session,
    *,
    name: str,
    hook_prototype: str = "",
    core_mechanic: str = "",
    narrative_structure: str = "",
    description: str = "",
    keywords: list[str] | None = None,
) -> CreativeDNA:
    """发号 + 落库（不含留痕/同步）。"""
    dna_repo = DnaRepository(db)
    dna: CreativeDNA | None = None
    # code 后端发号：取最大序号 +1；并发撞 unique 时重发一次
    for _ in range(2):
        try:
            with db.begin_nested():
                dna = dna_repo.create(
                    code=_next_code(db),
                    name=name,
                    hook_prototype=hook_prototype,
                    core_mechanic=core_mechanic,
                    narrative_structure=narrative_structure,
                    description=description,
                    keywords=family_bootstrap.clean_keywords(keywords),
                )
            break
        except IntegrityError:
            dna = None
    if dna is None:
        raise ApiError(409, "家族编号冲突，请重试")
    return dna


# ----------------------------------------------------------------------
# 停用 / 启用（契约 §1）
# ----------------------------------------------------------------------
def deactivate_family(db: Session, dna_id: str) -> tuple[CreativeDNA, int]:
    """停用家族：status→archived，成员全部回散点（不删数据），edit_log 留痕。

    仅 active 可停用；merged / 已 archived 一律 409（契约 §1.2.7）。
    返回 (dna, 回散点成员数)；code 永久保留不复用。
    """
    dna_repo = DnaRepository(db)
    dna = dna_repo.get(dna_id)
    if dna is None:
        raise ApiError(404, f"DNA 家族不存在：{dna_id}")
    if dna.status == "merged":
        raise ApiError(409, f"家族 {dna.code} {dna.name} 已被合并，不能停用")
    if dna.status == "archived":
        raise ApiError(409, f"家族 {dna.code} {dna.name} 已是停用状态")

    members = dna_repo.list_creatives(dna.id)
    for creative in members:
        creative.dna_id = None
    dna.status = "archived"
    db.flush()

    EditLogRepository(db).record(
        entity_type="dna",
        entity_id=dna.id,
        action="update",
        field="status",
        old_value="active",
        new_value=f"archived（{len(members)} 个成员已回散点）",
    )
    return dna, len(members)


def reactivate_family(db: Session, dna_id: str) -> CreativeDNA:
    """启用家族：status→active，不回挂成员（散点走正常归族流程重新分配）。"""
    dna = DnaRepository(db).get(dna_id)
    if dna is None:
        raise ApiError(404, f"DNA 家族不存在：{dna_id}")
    if dna.status == "merged":
        raise ApiError(409, f"家族 {dna.code} {dna.name} 已被合并，不能启用")
    if dna.status == "active":
        raise ApiError(409, f"家族 {dna.code} {dna.name} 已是启用状态")

    dna.status = "active"
    db.flush()
    EditLogRepository(db).record(
        entity_type="dna",
        entity_id=dna.id,
        action="update",
        field="status",
        old_value="archived",
        new_value="active",
    )
    return dna


# ----------------------------------------------------------------------
# 单条确认建族（confirm-family 路由与批量 confirm-families 共用）
# ----------------------------------------------------------------------
@dataclass
class FamilyConfirmOutcome:
    dna: CreativeDNA
    assigned: list[Creative]
    attach: bool  # True = 并入已有家族（未建族）


def assignable_members(db: Session, member_ids: list[str]) -> list[Creative]:
    """仍是散点的有效成员（与 confirm 的挂接口径一致，空族护栏预检同用）。"""
    creative_repo = CreativeRepository(db)
    members: list[Creative] = []
    for member_id in dict.fromkeys(member_ids):
        creative = creative_repo.get(member_id)
        if creative is None or creative.dna_id is not None:
            continue
        members.append(creative)
    return members


def confirm_family_tx(
    db: Session,
    *,
    suggestion_id: str,
    name: str = "",
    hook_prototype: str = "",
    core_mechanic: str = "",
    narrative_structure: str = "",
    keywords: list[str] | None = None,
    member_ids: list[str] | None = None,
    existing_dna_code: str | None = None,
) -> FamilyConfirmOutcome:
    """确认一个智能建族提案的 DB 部分：建族（或并入）+ 挂接 + 留痕 + 删建议。

    不含 commit / Neo4j 同步 / rebuild_mirror（由调用方统一收口）。
    """
    suggestion = db.get(JudgeSuggestion, suggestion_id)
    if suggestion is None or suggestion.kind != family_bootstrap.KIND:
        raise ApiError(404, f"建族提案不存在：{suggestion_id}")

    attach_code = (existing_dna_code or "").strip().upper()
    if attach_code:
        # 并入已有家族：不建族只挂接，name/三要素/keywords 忽略
        dna = db.scalar(select(CreativeDNA).where(CreativeDNA.code == attach_code))
        if dna is None:
            raise ApiError(404, f"DNA 家族不存在：{attach_code}")
        if dna.status != "active":
            raise ApiError(409, f"家族 {dna.code} {dna.name} 已停用或合并，不能并入")
    else:
        name = name.strip()
        if not name:
            raise ApiError(400, "家族名称不能为空")
        dna = create_dna_numbered(
            db,
            name=name,
            hook_prototype=hook_prototype.strip(),
            core_mechanic=core_mechanic.strip(),
            narrative_structure=narrative_structure.strip(),
            keywords=keywords,
        )

    dna_repo = DnaRepository(db)
    edit_logs = EditLogRepository(db)
    assigned: list[Creative] = []
    # 只挂仍是散点的成员（提案生成后可能已被其他流程归族，绝不抢）
    for creative in assignable_members(db, member_ids or []):
        dna_repo.assign_creative(creative, dna)
        source = "智能建族并入已有家族" if attach_code else "智能建族人工确认"
        # 人工确认的批量归族不是机器自动执行：不带 auto: 前缀——否则会被
        # judge_calibration 计入 dna_assign 自动改判率桶（P0-2 统计污染），
        # 在 intervention 口径里它本就是人工裁决（收件箱逐族确认）
        edit_logs.record(
            entity_type="creative",
            entity_id=creative.id,
            action="update",
            field="dna_id",
            new_value=f"{dna.code} {dna.name}（{source}）",
        )
        assigned.append(creative)

    db.delete(suggestion)
    db.flush()
    return FamilyConfirmOutcome(dna=dna, assigned=assigned, attach=bool(attach_code))


# ----------------------------------------------------------------------
# 单条归族/取消归族（PUT /creatives/{id}/dna 与批量端点共用）
# ----------------------------------------------------------------------
def set_creative_dna(db: Session, creative: Creative, dna: CreativeDNA | None) -> None:
    """赋值 + 留痕（dna=None 即取消归族）。"""
    dna_repo = DnaRepository(db)
    old_label = ""
    if creative.dna_id:
        old_dna = dna_repo.get(creative.dna_id)
        old_label = f"{old_dna.code} {old_dna.name}" if old_dna else str(creative.dna_id)

    dna_repo.assign_creative(creative, dna)
    new_label = f"{dna.code} {dna.name}" if dna else ""
    EditLogRepository(db).record(
        entity_type="creative",
        entity_id=creative.id,
        action="update",
        field="dna_id",
        old_value=old_label,
        new_value=new_label,
    )


# ----------------------------------------------------------------------
# 批量执行（契约 §2：逐项 savepoint 隔离，统一 per-item 结果结构）
# ----------------------------------------------------------------------
@dataclass
class BatchRun:
    result: BatchResult
    # 成功项涉及的家族 id（路由据此逐个做 Neo4j sync_dna_subgraph）
    touched_dna_ids: list[str] = field(default_factory=list)


def _failed(item_id: str, exc: Exception) -> BatchItemResult:
    if isinstance(exc, ApiError):
        reason = {404: "not_found", 409: "conflict"}.get(exc.status_code, "error")
        return BatchItemResult(id=item_id, status="failed", reason=reason,
                               message=exc.message)
    logger.warning("批量项 %s 执行失败: %s", item_id, exc)
    return BatchItemResult(id=item_id, status="failed", reason="error",
                           message=str(exc))


def _read_family_payload(suggestion: JudgeSuggestion) -> dict[str, Any]:
    """建族提案的 reason JSON；坏了的提案按单项 failed 处理（不建族不删建议）。"""
    try:
        payload = json.loads(suggestion.reason)
    except (TypeError, ValueError):
        payload = None
    if not isinstance(payload, dict) or not isinstance(payload.get("member_ids"), list):
        raise ApiError(422, "建族提案负载已损坏")
    keywords = payload.get("keywords")
    return {
        "name": str(payload.get("name") or suggestion.verdict or ""),
        "hook_prototype": str(payload.get("hook_prototype") or ""),
        "core_mechanic": str(payload.get("core_mechanic") or ""),
        "narrative_structure": str(payload.get("narrative_structure") or ""),
        "keywords": keywords if isinstance(keywords, list) else [],
        "member_ids": [str(m) for m in payload["member_ids"]],
        "existing_dna_code": payload.get("existing_dna_code"),
    }


def run_confirm_families(db: Session, suggestion_ids: list[str]) -> BatchRun:
    """批量确认建族（契约 §2.2）。

    逐项语义 = confirm_family_tx，外加空族护栏：过滤后无有效成员 →
    不建族、删建议、skipped(reason="empty_family")。
    """
    items: list[BatchItemResult] = []
    touched: list[str] = []
    for suggestion_id in suggestion_ids:
        try:
            with db.begin_nested():
                suggestion = db.get(JudgeSuggestion, suggestion_id)
                if suggestion is None or suggestion.kind != family_bootstrap.KIND:
                    raise ApiError(404, f"建族提案不存在：{suggestion_id}")
                fields = _read_family_payload(suggestion)
                if not assignable_members(db, fields["member_ids"]):
                    # 空族护栏：成员均已被归族/不存在——不建族，删建议
                    db.delete(suggestion)
                    item = BatchItemResult(
                        id=suggestion_id, status="skipped", reason="empty_family",
                        message="成员均已被归族或不存在，已移除该提案",
                    )
                else:
                    outcome = confirm_family_tx(db, suggestion_id=suggestion_id, **fields)
                    touched.append(outcome.dna.id)
                    item = BatchItemResult(
                        id=suggestion_id, status="success",
                        data={
                            "dna_id": outcome.dna.id,
                            "assigned_count": len(outcome.assigned),
                        },
                    )
        except Exception as exc:  # noqa: BLE001 — 单项失败仅回滚该项
            item = _failed(suggestion_id, exc)
        items.append(item)
    return BatchRun(result=collect(items), touched_dna_ids=touched)


def run_batch_assign(db: Session, *, creative_ids: list[str], dna_id: str) -> BatchRun:
    """批量归入同一族（契约 §2.3），逐项语义 = PUT /creatives/{id}/dna 赋值路径。"""
    dna = DnaRepository(db).get(dna_id)
    if dna is None:
        raise ApiError(404, f"DNA 家族不存在：{dna_id}")
    if dna.status != "active":
        raise ApiError(409, f"家族 {dna.code} {dna.name} 已停用或合并，不能归族")

    creative_repo = CreativeRepository(db)
    items: list[BatchItemResult] = []
    for creative_id in creative_ids:
        try:
            with db.begin_nested():
                creative = creative_repo.get(creative_id)
                if creative is None:
                    raise ApiError(404, f"Creative 不存在：{creative_id}")
                if creative.dna_id == dna.id:
                    item = BatchItemResult(
                        id=creative_id, status="skipped", reason="already_done",
                        message=f"已在家族 {dna.code} {dna.name}",
                    )
                else:
                    set_creative_dna(db, creative, dna)
                    item = BatchItemResult(
                        id=creative_id, status="success", data={"dna_id": dna.id},
                    )
        except Exception as exc:  # noqa: BLE001 — 单项失败仅回滚该项
            item = _failed(creative_id, exc)
        items.append(item)
    touched = [dna.id] if any(i.status == "success" for i in items) else []
    return BatchRun(result=collect(items), touched_dna_ids=touched)


def run_batch_unassign(db: Session, creative_ids: list[str]) -> BatchRun:
    """批量取消归族（契约 §2.4），逐项语义 = PUT /creatives/{id}/dna 传空。"""
    creative_repo = CreativeRepository(db)
    items: list[BatchItemResult] = []
    for creative_id in creative_ids:
        try:
            with db.begin_nested():
                creative = creative_repo.get(creative_id)
                if creative is None:
                    raise ApiError(404, f"Creative 不存在：{creative_id}")
                if creative.dna_id is None:
                    item = BatchItemResult(
                        id=creative_id, status="skipped", reason="already_done",
                        message="未归族",
                    )
                else:
                    set_creative_dna(db, creative, None)
                    item = BatchItemResult(id=creative_id, status="success")
        except Exception as exc:  # noqa: BLE001 — 单项失败仅回滚该项
            item = _failed(creative_id, exc)
        items.append(item)
    return BatchRun(result=collect(items))
