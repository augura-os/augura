"""DNA family routes: create / list families + assign a creative to one.

Assignments are human decisions (registry §2) — every change is audit-logged
and synced to Neo4j (:CreativeDNA)-[:HAS_CREATIVE] + the SQL mirror.
"""

from __future__ import annotations

import json
import re

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import DbDep, SettingsDep
from app.exceptions import ApiError
from app.models import CreativeDNA, JudgeSuggestion
from app.repositories.creatives import CreativeRepository
from app.repositories.dna import DnaRepository
from app.repositories.edit_logs import EditLogRepository
from app.repositories.graph_mirror import rebuild_mirror
from app.repositories.settings import SettingsRepository
from app.schemas.common import Envelope, ok
from app.services import family_bootstrap, graph_sync, markets, rule_feedback
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


class RuleKeywordPayload(BaseModel):
    """规则词确认/跳过：只传建议 id——词与目标从建议负载里读（服务端），
    客户端不能写任意词进词表（写入口只有这一条路）。"""

    suggestion_id: str


def _next_code(db: Session) -> str:
    """Server-side numbering: max existing D-sequence + 1, formatted D%02d."""
    codes = db.scalars(select(CreativeDNA.code)).all()
    max_n = max(
        (int(c[1:]) for c in codes if re.fullmatch(r"D\d+", c)),
        default=0,
    )
    return f"D{max_n + 1:02d}"


def _create_dna_numbered(
    db: Session,
    *,
    name: str,
    hook_prototype: str = "",
    core_mechanic: str = "",
    narrative_structure: str = "",
    description: str = "",
    keywords: list[str] | None = None,
) -> CreativeDNA:
    """发号 + 落库（手动建族与智能建族确认共用；不含留痕/同步）。"""
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

    dna = _create_dna_numbered(
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
    suggestion = db.get(JudgeSuggestion, payload.suggestion_id)
    if suggestion is None or suggestion.kind != family_bootstrap.KIND:
        raise ApiError(404, f"建族提案不存在：{payload.suggestion_id}")

    attach_code = (payload.existing_dna_code or "").strip().upper()
    if attach_code:
        # 并入已有家族：不建族只挂接，name/三要素/keywords 忽略
        dna = db.scalar(
            select(CreativeDNA).where(CreativeDNA.code == attach_code)
        )
        if dna is None:
            raise ApiError(404, f"DNA 家族不存在：{attach_code}")
    else:
        name = payload.name.strip()
        if not name:
            raise ApiError(400, "家族名称不能为空")
        dna = _create_dna_numbered(
            db,
            name=name,
            hook_prototype=payload.hook_prototype.strip(),
            core_mechanic=payload.core_mechanic.strip(),
            narrative_structure=payload.narrative_structure.strip(),
            keywords=payload.keywords,
        )

    dna_repo = DnaRepository(db)
    creative_repo = CreativeRepository(db)
    edit_logs = EditLogRepository(db)
    assigned = []
    # 只挂仍是散点的成员（提案生成后可能已被其他流程归族，绝不抢）
    for member_id in dict.fromkeys(payload.member_ids):
        creative = creative_repo.get(member_id)
        if creative is None or creative.dna_id is not None:
            continue
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
    db.commit()

    graph_sync.sync_dna_subgraph(settings, dna=dna, creatives=assigned)
    rebuild_mirror(db)

    action = "已并入家族" if attach_code else "已创建家族"
    return ok(
        DnaOut(id=dna.id, code=dna.code, name=dna.name),
        message=f"{action} {dna.code} {dna.name}（{len(assigned)} 个成员）",
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

    dna_repo = DnaRepository(db)
    old_label = ""
    if creative.dna_id:
        old_dna = dna_repo.get(creative.dna_id)
        old_label = f"{old_dna.code} {old_dna.name}" if old_dna else str(creative.dna_id)

    dna = dna_repo.get(payload.dna_id) if payload.dna_id else None
    if payload.dna_id and dna is None:
        raise ApiError(404, f"DNA 家族不存在：{payload.dna_id}")

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
    db.commit()

    if dna is not None:
        graph_sync.sync_dna_subgraph(
            settings, dna=dna, creatives=dna_repo.list_creatives(dna.id)
        )
    rebuild_mirror(db)

    if dna is None:
        return ok(None, message=f"{creative.name} 已取消归族")
    return ok(DnaOut(id=dna.id, code=dna.code, name=dna.name), message="已归族")
