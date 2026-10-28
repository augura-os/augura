"""DNA family routes: create / list families + assign a creative to one.

Assignments are human decisions (registry §2) — every change is audit-logged
and synced to Neo4j (:CreativeDNA)-[:HAS_CREATIVE] + the SQL mirror.

P0a（契约 2026-10-20）：家族停用/启用 + 三个幂等批量端点。逐条语义在
app.services.family_ops，这里只做参数解析与 commit / Neo4j /
rebuild_mirror 收口。
"""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Header
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import DbDep, SettingsDep
from app.config import Settings
from app.exceptions import ApiError
from app.models import JudgeSuggestion
from app.repositories.creatives import CreativeRepository
from app.repositories.dna import DnaRepository
from app.repositories.edit_logs import EditLogRepository
from app.repositories.graph_mirror import rebuild_mirror
from app.repositories.settings import SettingsRepository
from app.schemas.batch import BatchResult
from app.schemas.common import Envelope, ok
from app.services import (
    family_bootstrap,
    family_ops,
    graph_sync,
    idempotency,
    markets,
    rule_feedback,
)
from app.services.family_ops import BatchRun
from app.services.settings import resolve_ai_config

router = APIRouter()


class DnaOut(BaseModel):
    id: str
    code: str
    name: str


class DnaCreatePayload(BaseModel):
    name: str
    hook_prototype: str = ""
    core_mechanic: str = ""
    narrative_structure: str = ""
    description: str = ""


class DnaAssignPayload(BaseModel):
    dna_id: str | None  # None = 取消归族


class FamilyConfirmPayload(BaseModel):
    """智能建族人工确认：四字段允许改名后的值 + 成员 id 列表。

    existing_dna_code 存在时走"并入已有家族"分支：不建族只挂接，
    name/三要素/keywords 全部忽略（name 可不填）。
    """

    suggestion_id: str
    name: str = ""
    hook_prototype: str = ""
    core_mechanic: str = ""
    narrative_structure: str = ""
    keywords: list[str] = []
    member_ids: list[str] = []
    existing_dna_code: str | None = None


class FamilyDismissPayload(BaseModel):
    suggestion_id: str


class ConfirmFamiliesPayload(BaseModel):
    """批量确认建族（契约 §2.2）：成员取值以提案负载为准，上限 50。"""

    suggestion_ids: list[str] = Field(default_factory=list, max_length=50)


class BatchAssignPayload(BaseModel):
    """批量归入同一族（契约 §2.3），上限 200。"""

    creative_ids: list[str] = Field(max_length=200)
    dna_id: str


class BatchUnassignPayload(BaseModel):
    """批量取消归族（契约 §2.4），上限 200。"""

    creative_ids: list[str] = Field(max_length=200)


class RuleKeywordPayload(BaseModel):
    """规则词确认/跳过：只传建议 id——词与目标从建议负载里读（服务端），
    客户端不能写任意词进词表（写入口只有这一条路）。"""

    suggestion_id: str


@router.get("/dnas", response_model=Envelope[list[DnaOut]])
def list_dnas(db: DbDep) -> Envelope[list[DnaOut]]:
    dnas = DnaRepository(db).list_all()
    return ok([DnaOut(id=d.id, code=d.code, name=d.name) for d in dnas])


@router.post("/dnas", response_model=Envelope[DnaOut])
def create_dna(
    payload: DnaCreatePayload,
    db: DbDep,
    settings: SettingsDep,
) -> Envelope[DnaOut]:
    name = payload.name.strip()
    if not name:
        raise ApiError(400, "家族名称不能为空")

    dna = family_ops.create_dna_numbered(
        db,
        name=name,
        hook_prototype=payload.hook_prototype.strip(),
        core_mechanic=payload.core_mechanic.strip(),
        narrative_structure=payload.narrative_structure.strip(),
        description=payload.description.strip(),
    )

    EditLogRepository(db).record(
        entity_type="dna",
        entity_id=dna.id,
        action="create",
        field="code",
        new_value=f"{dna.code} {dna.name}",
    )
    db.commit()

    graph_sync.sync_dna_subgraph(settings, dna=dna, creatives=[])
    rebuild_mirror(db)

    return ok(
        DnaOut(id=dna.id, code=dna.code, name=dna.name),
        message=f"已创建家族 {dna.code} {dna.name}",
    )


# ----------------------------------------------------------------------
# 家族停用 / 启用（P0a 契约 §1）
# ----------------------------------------------------------------------
@router.post("/dnas/{dna_id}/deactivate", response_model=Envelope[DnaOut])
def deactivate_dna(
    dna_id: str,
    db: DbDep,
    settings: SettingsDep,
) -> Envelope[DnaOut]:
    """停用家族：status→archived，成员全部回散点（不删数据），code 永久保留。

    Neo4j 移除 DNA 节点及成员边，SQL 镜像 rebuild 一次；仅 active 可停用，
    merged / 已停用一律 409。
    """
    dna, member_count = family_ops.deactivate_family(db, dna_id)
    db.commit()

    graph_sync.delete_dna_node(settings, dna.id)
    rebuild_mirror(db)

    return ok(
        DnaOut(id=dna.id, code=dna.code, name=dna.name),
        message=f"已停用家族 {dna.code} {dna.name}（{member_count} 个成员已回散点）",
    )


@router.post("/dnas/{dna_id}/reactivate", response_model=Envelope[DnaOut])
def reactivate_dna(
    dna_id: str,
    db: DbDep,
    settings: SettingsDep,
) -> Envelope[DnaOut]:
    """启用家族：status→active，不回挂成员（散点由正常归族流程重新分配）。"""
    dna = family_ops.reactivate_family(db, dna_id)
    db.commit()

    # 重建 Neo4j 节点（停用时已移除）；无成员边——成员走正常归族流程
    graph_sync.sync_dna_subgraph(settings, dna=dna, creatives=[])
    rebuild_mirror(db)

    return ok(
        DnaOut(id=dna.id, code=dna.code, name=dna.name),
        message=f"已启用家族 {dna.code} {dna.name}",
    )


# ----------------------------------------------------------------------
# 智能建族：提案 / 确认（单条 + 批量）/ 跳过
# ----------------------------------------------------------------------
@router.post("/dnas/suggest-families", response_model=Envelope[int])
def suggest_families_route(db: DbDep, settings: SettingsDep) -> Envelope[int]:
    """智能建族：LLM 对全部未归族 creative 做互斥家族划分，提案进收件箱。"""
    config = resolve_ai_config(db, settings)
    count = family_bootstrap.suggest_families(db, config)
    db.commit()
    if count == 0:
        return ok(0, message="暂无可建族素材（或未配置 API key）")
    return ok(count, message=f"已生成 {count} 个家族提案，请在收件箱逐族确认")


@router.post("/dnas/confirm-family", response_model=Envelope[DnaOut])
def confirm_family(
    payload: FamilyConfirmPayload,
    db: DbDep,
    settings: SettingsDep,
) -> Envelope[DnaOut]:
    """确认一个智能建族提案：建族（或并入已有家族）+ 挂接 + 留痕 + 删建议。"""
    outcome = family_ops.confirm_family_tx(
        db,
        suggestion_id=payload.suggestion_id,
        name=payload.name,
        hook_prototype=payload.hook_prototype,
        core_mechanic=payload.core_mechanic,
        narrative_structure=payload.narrative_structure,
        keywords=payload.keywords,
        member_ids=payload.member_ids,
        existing_dna_code=payload.existing_dna_code,
    )
    db.commit()

    dna = outcome.dna
    graph_sync.sync_dna_subgraph(settings, dna=dna, creatives=outcome.assigned)
    rebuild_mirror(db)

    action = "已并入家族" if outcome.attach else "已创建家族"
    return ok(
        DnaOut(id=dna.id, code=dna.code, name=dna.name),
        message=f"{action} {dna.code} {dna.name}（{len(outcome.assigned)} 个成员）",
    )


# ----------------------------------------------------------------------
# 幂等批量端点（P0a 契约 §2）：Idempotency-Key 必需，逐项 savepoint 隔离，
# 末尾统一 rebuild_mirror 一次（全部失败/全部 skipped 不 rebuild）。
# 同步执行不支持中途取消——前端可中断等待，后端继续跑完（契约 §2.1）。
# ----------------------------------------------------------------------
IdempotencyHeader = Annotated[str | None, Header(alias="Idempotency-Key")]


def _require_idempotency_key(key: str | None) -> str:
    return idempotency.require_key(key)


def _replay(db: Session, *, key: str, endpoint: str) -> Envelope[BatchResult] | None:
    """同 key 同 endpoint 的重复提交：直接返回首次存储的 response，不重复执行。"""
    stored = idempotency.get_stored(db, key=key, endpoint=endpoint)
    if stored is None:
        return None
    return ok(
        BatchResult(**stored["data"]),
        message=str(stored.get("message") or ""),
    )


def _finalize_batch(
    db: Session,
    settings: Settings,
    *,
    key: str,
    endpoint: str,
    run: BatchRun,
) -> Envelope[BatchResult]:
    """存幂等记录 + commit + 成功项 Neo4j 同步 + 末尾一次 rebuild_mirror。"""
    result = run.result
    summary = result.summary
    message = (
        f"批量完成：成功 {summary.success} / "
        f"跳过 {summary.skipped} / 失败 {summary.failed}"
    )
    idempotency.store_response(
        db,
        key=key,
        endpoint=endpoint,
        response={"data": result.model_dump(), "message": message},
    )
    db.commit()

    if summary.success > 0:
        dna_repo = DnaRepository(db)
        for touched_id in run.touched_dna_ids:
            dna = dna_repo.get(touched_id)
            if dna is not None:
                graph_sync.sync_dna_subgraph(
                    settings, dna=dna, creatives=dna_repo.list_creatives(dna.id)
                )
        rebuild_mirror(db)
    return ok(result, message=message)


@router.post("/dnas/confirm-families", response_model=Envelope[BatchResult])
def confirm_families(
    payload: ConfirmFamiliesPayload,
    db: DbDep,
    settings: SettingsDep,
    idempotency_key: IdempotencyHeader = None,
) -> Envelope[BatchResult]:
    """批量确认建族提案；空族护栏：无有效成员 → 不建族、删建议、skipped。"""
    key = _require_idempotency_key(idempotency_key)
    replay = _replay(db, key=key, endpoint="confirm-families")
    if replay is not None:
        return replay
    run = family_ops.run_confirm_families(db, payload.suggestion_ids)
    return _finalize_batch(db, settings, key=key, endpoint="confirm-families", run=run)


@router.post("/creatives/batch-assign-dna", response_model=Envelope[BatchResult])
def batch_assign_dna(
    payload: BatchAssignPayload,
    db: DbDep,
    settings: SettingsDep,
    idempotency_key: IdempotencyHeader = None,
) -> Envelope[BatchResult]:
    """批量归入同一族；已在该族的 skipped(already_done)，不存在 failed(not_found)。"""
    key = _require_idempotency_key(idempotency_key)
    replay = _replay(db, key=key, endpoint="batch-assign-dna")
    if replay is not None:
        return replay
    run = family_ops.run_batch_assign(
        db, creative_ids=payload.creative_ids, dna_id=payload.dna_id
    )
    return _finalize_batch(db, settings, key=key, endpoint="batch-assign-dna", run=run)


@router.post("/creatives/batch-unassign-dna", response_model=Envelope[BatchResult])
def batch_unassign_dna(
    payload: BatchUnassignPayload,
    db: DbDep,
    settings: SettingsDep,
    idempotency_key: IdempotencyHeader = None,
) -> Envelope[BatchResult]:
    """批量取消归族；未归族的 skipped(already_done)，不存在 failed(not_found)。"""
    key = _require_idempotency_key(idempotency_key)
    replay = _replay(db, key=key, endpoint="batch-unassign-dna")
    if replay is not None:
        return replay
    run = family_ops.run_batch_unassign(db, payload.creative_ids)
    return _finalize_batch(
        db, settings, key=key, endpoint="batch-unassign-dna", run=run
    )


@router.post("/dnas/dismiss-family", response_model=Envelope[None])
def dismiss_family(payload: FamilyDismissPayload, db: DbDep) -> Envelope[None]:
    """跳过一个智能建族提案（只删建议，不建族）。"""
    suggestion = db.get(JudgeSuggestion, payload.suggestion_id)
    if suggestion is None or suggestion.kind != family_bootstrap.KIND:
        raise ApiError(404, f"建族提案不存在：{payload.suggestion_id}")
    db.delete(suggestion)
    db.commit()
    return ok(None, message="已跳过该提案")


def _read_keyword_payload(suggestion: JudgeSuggestion) -> dict:
    """规则词建议的 reason JSON；坏了的建议当不存在（404 先于误写）。"""
    try:
        payload = json.loads(suggestion.reason)
    except (TypeError, ValueError):
        payload = None
    if not isinstance(payload, dict):
        raise ApiError(422, "规则词建议负载已损坏")
    word = str(payload.get("word") or "").strip()
    target = str(payload.get("target") or "").strip()
    if not word or target not in ("mechanic", "hook", "generic"):
        raise ApiError(422, "规则词建议负载已损坏")
    payload["word"] = word
    payload["target"] = target
    return payload


@router.post("/dnas/rule-keywords/confirm", response_model=Envelope[None])
def confirm_rule_keyword(payload: RuleKeywordPayload, db: DbDep) -> Envelope[None]:
    """确认规则词：追加进 settings 词表（立即对下一轮判定生效）+ 留痕 + 删建议。

    格式随键现状：rule_keywords:mechanic/hook 是 JSON 数组；
    similarity_generic_tokens 沿用 markets 的逗号分隔覆盖层（不为它改格式）。
    learned_at 等证据持久化在 edit_logs 的 new_value 里——命中率降权
    （陷阱三完整版）是后续工作。
    """
    suggestion = db.get(JudgeSuggestion, payload.suggestion_id)
    if suggestion is None or suggestion.kind != rule_feedback.KIND:
        raise ApiError(404, f"规则词建议不存在：{payload.suggestion_id}")
    data = _read_keyword_payload(suggestion)
    word, target = data["word"], data["target"]

    repo = SettingsRepository(db)
    if target == "generic":
        # 逗号分隔覆盖层（markets.resolve_generic_tokens 的读取格式）
        raw = repo.get(markets.GENERIC_TOKENS_SETTING) or ""
        tokens = [t.strip() for t in raw.split(",") if t.strip()]
        if word.lower() not in {t.lower() for t in tokens}:
            tokens.append(word)
        repo.set(markets.GENERIC_TOKENS_SETTING, ",".join(tokens))
        setting_key = markets.GENERIC_TOKENS_SETTING
    else:
        setting_key = rule_feedback.RULE_KEYWORDS_SETTING_TEMPLATE.format(target=target)
        raw = repo.get(setting_key)
        try:
            words = json.loads(raw) if raw else []
        except (TypeError, ValueError):
            words = []
        if not isinstance(words, list):
            words = []
        if word not in words:
            words.append(word)
            repo.set(setting_key, json.dumps(words, ensure_ascii=False))

    evidence = data.get("evidence") or {}
    EditLogRepository(db).record(
        entity_type="judge",
        entity_id=setting_key,
        action="rule_keyword",
        field=target,
        new_value=(
            f"规则词确认「{word}」→ {setting_key}"
            f"（score {float(data.get('score') or 0):.2f}，"
            f"同族对 {float(evidence.get('same_pair_rate') or 0):.0%} / "
            f"跨族对 {float(evidence.get('cross_pair_rate') or 0):.0%}，"
            f"样本 {int(evidence.get('support') or 0)}，"
            f"learned_at {data.get('learned_at') or '—'}）"
        ),
    )
    db.delete(suggestion)
    db.commit()
    return ok(None, message=f"已把「{word}」加入词表")


@router.post("/dnas/rule-keywords/dismiss", response_model=Envelope[None])
def dismiss_rule_keyword(payload: RuleKeywordPayload, db: DbDep) -> Envelope[None]:
    """跳过规则词建议（只删建议，不动词表）。"""
    suggestion = db.get(JudgeSuggestion, payload.suggestion_id)
    if suggestion is None or suggestion.kind != rule_feedback.KIND:
        raise ApiError(404, f"规则词建议不存在：{payload.suggestion_id}")
    db.delete(suggestion)
    db.commit()
    return ok(None, message="已跳过该建议")


@router.put("/creatives/{creative_id}/dna", response_model=Envelope[DnaOut | None])
def assign_creative_dna(
    creative_id: str,
    payload: DnaAssignPayload,
    db: DbDep,
    settings: SettingsDep,
) -> Envelope[DnaOut | None]:
    creative = CreativeRepository(db).get(creative_id)
    if creative is None:
        raise ApiError(404, f"Creative 不存在：{creative_id}")

    dna = DnaRepository(db).get(payload.dna_id) if payload.dna_id else None
    if payload.dna_id and dna is None:
        raise ApiError(404, f"DNA 家族不存在：{payload.dna_id}")
    if dna is not None and dna.status != "active":
        raise ApiError(409, f"家族 {dna.code} {dna.name} 已停用或合并，不能归族")

    family_ops.set_creative_dna(db, creative, dna)
    db.commit()

    if dna is not None:
        graph_sync.sync_dna_subgraph(
            settings, dna=dna, creatives=DnaRepository(db).list_creatives(dna.id)
        )
    rebuild_mirror(db)

    if dna is None:
        return ok(None, message=f"{creative.name} 已取消归族")
    return ok(DnaOut(id=dna.id, code=dna.code, name=dna.name), message="已归族")
