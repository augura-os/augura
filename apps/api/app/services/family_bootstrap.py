"""智能建族（family bootstrap）：小库冷启动的 LLM 批量家族划分。

手动建族（POST /dnas）解决"一个个建"，这里解决"库里一个族都没有"：
取全部未归族且有分析结果的 creative，分批喂 LLM 做互斥聚类
（机制优先于题材；散点允许不入族），提案写入 judge_suggestions
（kind="family_bootstrap"），收件箱逐族人工确认/跳过——确认才落库，
本服务绝不直接建族（registry §2：归族是人类决定）。

质量增强（PR3）：
- existing 注入已确认入库的 CreativeDNA（带 code），LLM 可把素材并入
  已有家族（assign_to_existing 给 existing_dna_code）而不是新建语义
  重复的新族；这类提案是"只挂接不建族"的特殊提案
- 每个新族提案带 keywords（识别特征词，含同义写法/换皮词），确认时落库
- 批次循环后追加两个 lite 步骤（各自失败静默跳过）：
  散点回收（落散素材再问一次是否其实属于某族）与互斥体检
  （提案两两机制重叠时在 note 标注，不自动合并）
- 分批前按代表向量贪心预分组（E2）：语义相近的素材排进同批；
  无向量时保持名字排序，行为与现状一致

存储（不改 judge_suggestions 表结构）：left_id=提案 uuid，right_id=None，
verdict=家族名，votes=成员数，reason=JSON 负载
（name/三要素/keywords/member_ids/note，挂接提案另带 existing_dna_code）。
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.models import (
    AnalysisResult,
    Creative,
    CreativeDNA,
    CreativeVariant,
    JudgeSuggestion,
)
from app.services.clustering import cosine_matrix
from app.services.judge_suggestions import upsert_suggestion
from app.services.llm_judge import complete_json
from app.services.settings import AIConfig

logger = logging.getLogger(__name__)

KIND = "family_bootstrap"
BATCH_SIZE = 8
# 稳态增量触发阈值：散点攒够约一批（BATCH_SIZE）就提示运行智能建族——
# 少于一批时 LLM 提案质量差（家族互斥划分需要足够样本），不值得打扰
SUGGEST_THRESHOLD = 8
# 单成员提案不是"模式"，只是还没找到同伴的散点——不进收件箱
# （挂接已有家族的提案不受此限：单个散点并入已有族正是回收目标）
MIN_MEMBERS = 2
MAX_KEYWORDS = 12
MAX_KEYWORD_LEN = 24

SYSTEM_PROMPT = """你是买量素材的 DNA 家族规划师。DNA 家族 = 钩子原型 × 核心机制 × 叙事结构。
聚类原则：
- 核心机制优先于题材/设定：同一机制换皮（雪地→沙漠、末日→宫廷）属于同一家族；
  同题材不同机制必须拆开。
- 家族互斥：一个素材最多进入一个家族。
- 允许散点：机制独特、找不到同伴的素材不进任何家族，不要硬塞。
- 家族名用中文、不超过 12 个字、体现核心机制（如「围栏防御」「反复挑战重试」）；
  不要输出编号或 code（code 由系统分配）。
你只输出 JSON，不输出任何解释性文字。"""

USER_TEMPLATE = """已确认入库的家族（带 code）和本次已提案的新家族（不带 code）：
{existing}

本批未归族素材：
{profiles}

输出 JSON：
{{
  "new_families": [
    {{
      "name": "家族名",
      "core_mechanic": "核心机制",
      "hook_prototype": "钩子原型",
      "narrative_structure": "叙事结构",
      "keywords": ["5-10 个识别特征词，含常见同义写法/换皮词"],
      "member_ids": ["本批素材 id"],
      "note": "一句话机制说明：为什么这些素材是一家"
    }}
  ],
  "assign_to_existing": [
    {{"family": "本次新提案家族名", "member_ids": ["本批素材 id"]}},
    {{"existing_dna_code": "D03", "member_ids": ["本批素材 id"]}}
  ]
}}

约束：
- member_ids 只能引用本批素材的 id；同一个 id 在本次输出中最多出现一次；
- 并入已确认家族用 existing_dna_code 给 code（不要给 family）；
  并入本次新提案用 family 给家族名（不要给 code）；两种方式二选一；
- 机制与某个已确认家族一致的素材优先并入它，不要新建语义重复的家族；
- 拿不准的素材不出现在任何列表里。"""

RESCUE_USER_TEMPLATE = """已有家族（含已确认入库的与本次新提案的）：
{existing}

以下是暂未归入任何家族的散点素材：
{profiles}

判断这些散点是否其实属于上面某个家族。输出 JSON：
{{
  "assign_to_existing": [
    {{"family": "本次新提案家族名", "member_ids": ["散点素材 id"]}},
    {{"existing_dna_code": "D03", "member_ids": ["散点素材 id"]}}
  ]
}}

都不属于任何家族就输出 {{"assign_to_existing": []}}。
约束：member_ids 只能引用上面的散点 id；同一个 id 最多出现一次；
引用已确认家族用 existing_dna_code，引用新提案用 family。"""

OVERLAP_USER_TEMPLATE = """以下是拟建的 DNA 家族提案：
{families}

检查是否有机制重叠、应当合并的家族对。输出 JSON：
{{"overlaps": [{{"family_a": "家族名", "family_b": "家族名", "why": "一句话说明"}}]}}

没有重叠就输出 {{"overlaps": []}}。family_a/family_b 必须引用上面的家族名。"""


def clean_keywords(value: Any) -> list[str]:
    """特征词清洗：去空白、去重、每词 ≤24 字符、总数 ≤12（confirm 落库同用）。"""
    if not isinstance(value, list):
        return []
    keywords: list[str] = []
    for word in value:
        if not isinstance(word, str):
            continue
        word = word.strip()[:MAX_KEYWORD_LEN]
        if word and word not in keywords:
            keywords.append(word)
        if len(keywords) >= MAX_KEYWORDS:
            break
    return keywords


def _has_analysis_clause():
    """creative 至少有一条 analysis_results（经 variants 关联）——散点口径的
    "有分析"条件，计数与画像取数共用这一个出处，别复制两遍。"""
    return (
        select(CreativeVariant.id)
        .join(AnalysisResult, AnalysisResult.asset_id == CreativeVariant.asset_id)
        .where(CreativeVariant.creative_id == Creative.id)
        .exists()
    )


def _unassigned_creatives(db: Session) -> list[Creative]:
    """未归族（dna_id IS NULL）且有分析结果的 creative——智能建族散点口径。"""
    stmt = (
        select(Creative)
        .where(Creative.dna_id.is_(None), _has_analysis_clause())
        .order_by(Creative.name)
    )
    return list(db.scalars(stmt).all())


def count_unassigned(db: Session) -> int:
    """散点计数（review queue 的建族提示用，与 _unassigned_creatives 同口径）。"""
    stmt = select(func.count(Creative.id)).where(
        Creative.dna_id.is_(None), _has_analysis_clause()
    )
    return int(db.scalar(stmt) or 0)


def _unassigned_profiles(db: Session) -> list[dict[str, str]]:
    """未归族且有分析结果的 creative 的 LLM 输入画像。"""
    profiles: list[dict[str, str]] = []
    for creative in _unassigned_creatives(db):
        row = db.execute(
            text(
                "select a.hook, a.gameplay, a.summary from analysis_results a "
                "join creative_variants v on v.asset_id = a.asset_id "
                "where v.creative_id = :cid limit 1"
            ),
            {"cid": creative.id},
        ).first()
        if row is None:
            continue
        profiles.append(
            {
                "id": creative.id,
                "name": creative.name,
                "hook": (row[0] or "")[:200],
                "gameplay": (row[1] or "")[:200],
                "summary": (row[2] or "")[:200],
            }
        )
    return profiles


def _confirmed_families(db: Session) -> list[dict[str, str]]:
    """已确认入库的家族（existing 的初始内容，LLM 可引用 code 并入）。"""
    return [
        {"code": d.code, "name": d.name, "core_mechanic": d.core_mechanic}
        for d in db.scalars(select(CreativeDNA).order_by(CreativeDNA.code)).all()
    ]


def _existing_desc(
    confirmed: list[dict[str, str]], proposals: list[dict[str, Any]]
) -> str:
    entries = [
        {"code": c["code"], "name": c["name"], "core_mechanic": c["core_mechanic"]}
        for c in confirmed
    ] + [
        {"name": p["name"], "core_mechanic": p["core_mechanic"]} for p in proposals
    ]
    if not entries:
        return "（无）"
    return json.dumps(entries, ensure_ascii=False)


def _parse_response(
    raw: dict[str, Any] | None, batch_ids: set[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | None:
    """校验 LLM 输出结构；member_ids 只保留本批 id 且批内去重。"""
    if raw is None:
        return None
    new_families = raw.get("new_families")
    assignments = raw.get("assign_to_existing")
    if not isinstance(new_families, list) or not isinstance(assignments, list):
        return None

    seen: set[str] = set()

    def _clean_ids(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        ids: list[str] = []
        for member_id in value:
            if (
                isinstance(member_id, str)
                and member_id in batch_ids
                and member_id not in seen
            ):
                seen.add(member_id)
                ids.append(member_id)
        return ids

    families: list[dict[str, Any]] = []
    for item in new_families:
        if not isinstance(item, dict) or not str(item.get("name") or "").strip():
            return None
        families.append(
            {
                "name": str(item["name"]).strip()[:32],
                "core_mechanic": str(item.get("core_mechanic") or "").strip()[:64],
                "hook_prototype": str(item.get("hook_prototype") or "").strip()[:64],
                "narrative_structure": str(
                    item.get("narrative_structure") or ""
                ).strip()[:128],
                "keywords": clean_keywords(item.get("keywords")),
                "member_ids": _clean_ids(item.get("member_ids")),
                "note": str(item.get("note") or "").strip(),
            }
        )
    merges: list[dict[str, Any]] = []
    for item in assignments:
        if not isinstance(item, dict):
            return None
        # 两种引用方式二选一：新提案给 family 名，已确认家族给 code
        family = str(item.get("family") or "").strip()
        code = str(item.get("existing_dna_code") or "").strip().upper()
        if not family and not code:
            return None
        merges.append(
            {
                "family": family,
                "existing_dna_code": code,
                "member_ids": _clean_ids(item.get("member_ids")),
            }
        )
    return families, merges


def _propose_batch(
    config: AIConfig,
    existing_desc: str,
    batch: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """单批提案，失败重试一次；仍失败降级为本批无提案（不阻塞整体）。"""
    user = USER_TEMPLATE.format(
        existing=existing_desc,
        profiles=json.dumps(batch, ensure_ascii=False),
    )
    batch_ids = {p["id"] for p in batch}
    for attempt in range(2):
        parsed = _parse_response(
            complete_json(
                config, system=SYSTEM_PROMPT, user=user, max_tokens=2000
            ),
            batch_ids,
        )
        if parsed is not None:
            return parsed
        logger.debug("family bootstrap 批次解析失败（第 %d 次）", attempt + 1)
    return [], []


def _apply_merges(
    merges: list[dict[str, Any]],
    proposals: list[dict[str, Any]],
    attaches: list[dict[str, Any]],
    confirmed_by_code: dict[str, dict[str, str]],
) -> None:
    """并入指派：code → 挂接提案（只挂不建）；family 名 → 本次新提案。"""
    by_name = {p["name"]: p for p in proposals}
    by_code = {a["existing_dna_code"]: a for a in attaches}
    for merge in merges:
        code = merge["existing_dna_code"]
        if code:
            confirmed = confirmed_by_code.get(code)
            if confirmed is None:
                # LLM 引用了不存在的 code：丢弃，成员留作散点（回收步可再捞）
                continue
            attach = by_code.get(code)
            if attach is None:
                attach = {
                    "existing_dna_code": code,
                    "name": confirmed["name"],
                    "member_ids": [],
                    "note": "",
                }
                attaches.append(attach)
                by_code[code] = attach
            attach["member_ids"].extend(merge["member_ids"])
            continue
        target = by_name.get(merge["family"])
        if target is not None:
            target["member_ids"].extend(merge["member_ids"])


def _rescue_scatter(
    config: AIConfig,
    scatter: list[dict[str, str]],
    existing_desc: str,
    proposals: list[dict[str, Any]],
    attaches: list[dict[str, Any]],
    confirmed_by_code: dict[str, dict[str, str]],
) -> None:
    """散点回收：落散素材再问一次"是否其实属于某族"；失败静默跳过。"""
    if not scatter:
        return
    user = RESCUE_USER_TEMPLATE.format(
        existing=existing_desc,
        profiles=json.dumps(scatter, ensure_ascii=False),
    )
    parsed = _parse_response(
        complete_json(config, system=SYSTEM_PROMPT, user=user, max_tokens=1200),
        {p["id"] for p in scatter},
    )
    if parsed is None:
        logger.debug("family bootstrap 散点回收解析失败，跳过")
        return
    _new, merges = parsed  # 回收只接受并入指派，不从散点新建家族
    _apply_merges(merges, proposals, attaches, confirmed_by_code)


def _mark_overlaps(config: AIConfig, proposals: list[dict[str, Any]]) -> None:
    """互斥体检：提案两两机制重叠时在 note 标注（不自动合并）；失败静默。"""
    if len(proposals) < 2:
        return
    user = OVERLAP_USER_TEMPLATE.format(
        families=json.dumps(
            [
                {
                    "name": p["name"],
                    "core_mechanic": p["core_mechanic"],
                    "keywords": p["keywords"],
                }
                for p in proposals
            ],
            ensure_ascii=False,
        )
    )
    try:
        raw = complete_json(
            config, system=SYSTEM_PROMPT, user=user, max_tokens=800
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("family bootstrap 互斥体检调用失败，跳过: %s", exc)
        return
    if not isinstance(raw, dict) or not isinstance(raw.get("overlaps"), list):
        logger.debug("family bootstrap 互斥体检解析失败，跳过")
        return
    by_name = {p["name"]: p for p in proposals}
    for item in raw["overlaps"]:
        if not isinstance(item, dict):
            continue
        name_a = str(item.get("family_a") or "").strip()
        name_b = str(item.get("family_b") or "").strip()
        for name, other in ((name_a, name_b), (name_b, name_a)):
            proposal = by_name.get(name)
            if proposal is None or not other or other not in by_name:
                continue
            warning = f"⚠ 与「{other}」机制相近，确认前考虑合并"
            if warning not in proposal["note"]:
                proposal["note"] = (
                    f"{proposal['note']} {warning}".strip()
                )


def clear_bootstrap_suggestions(db: Session) -> None:
    """清掉未处理的 family_bootstrap 建议（确认/跳过会逐条删；这里是重跑重置）。"""
    db.execute(delete(JudgeSuggestion).where(JudgeSuggestion.kind == KIND))
    db.flush()


def _order_by_embedding(
    db: Session, profiles: list[dict[str, str]]
) -> list[dict[str, str]]:
    """向量预分组（E2 设计 §4.3）：分批前按代表向量贪心重排，让语义相近的
    素材排进同一批（每批都是语义连贯的候选族胚子，减轻 LLM 跨批合并负担）。

    贪心最近邻链：从首个画像出发，每步取与上一个余弦最近的未排画像。
    无向量的素材保持原相对顺序排在尾部；全库无向量时返回原顺序（名字排序），
    行为与现状完全一致。
    """
    ids = [p["id"] for p in profiles]
    vectors = dict(
        db.execute(
            select(Creative.id, Creative.representative_embedding).where(
                Creative.id.in_(ids)
            )
        ).all()
    )
    embedded_idx = [i for i, pid in enumerate(ids) if vectors.get(pid)]
    if len(embedded_idx) < 2:
        return profiles
    matrix = cosine_matrix([vectors[ids[i]] for i in embedded_idx])
    pos = {idx: rank for rank, idx in enumerate(embedded_idx)}
    remaining = embedded_idx[1:]
    chain = [embedded_idx[0]]
    while remaining:
        last = pos[chain[-1]]
        nxt = max(remaining, key=lambda idx: matrix[last][pos[idx]])
        chain.append(nxt)
        remaining.remove(nxt)
    ordered = [profiles[i] for i in chain]
    ordered.extend(p for i, p in enumerate(profiles) if i not in embedded_idx)
    return ordered


def suggest_families(db: Session, config: AIConfig) -> int:
    """LLM 批量提案 → judge_suggestions；返回提案族数（0 = 无可提案素材）。"""
    if not config.api_key:
        return 0
    profiles = _unassigned_profiles(db)
    if not profiles:
        return 0
    # 向量预分组（E2 §4.3）：语义相近的排进同批；无向量时保持名字排序
    profiles = _order_by_embedding(db, profiles)

    confirmed = _confirmed_families(db)
    confirmed_by_code = {c["code"]: c for c in confirmed}

    proposals: list[dict[str, Any]] = []  # 新建家族提案
    attaches: list[dict[str, Any]] = []  # 挂接已有家族提案（只挂不建）
    for start in range(0, len(profiles), BATCH_SIZE):
        batch = profiles[start : start + BATCH_SIZE]
        new_families, merges = _propose_batch(
            config, _existing_desc(confirmed, proposals), batch
        )
        _apply_merges(merges, proposals, attaches, confirmed_by_code)
        proposals.extend(new_families)

    # 散点回收（lite）：落散素材再问一次
    referenced = {
        member_id
        for group in (*proposals, *attaches)
        for member_id in group["member_ids"]
    }
    scatter = [p for p in profiles if p["id"] not in referenced]
    _rescue_scatter(
        config,
        scatter,
        _existing_desc(confirmed, proposals),
        proposals,
        attaches,
        confirmed_by_code,
    )

    proposals = [p for p in proposals if len(p["member_ids"]) >= MIN_MEMBERS]
    attaches = [a for a in attaches if a["member_ids"]]

    # 互斥体检（lite）：只标注不合并
    _mark_overlaps(config, proposals)

    if not proposals and not attaches:
        # LLM 全挂/全是散点：保留已有提案不清空（可安全重跑）
        return 0
    # 有新提案才清旧写新：重跑幂等，不累积重复建议
    clear_bootstrap_suggestions(db)
    for proposal in (*proposals, *attaches):
        payload: dict[str, Any] = {
            "name": proposal["name"],
            "core_mechanic": proposal.get("core_mechanic", ""),
            "hook_prototype": proposal.get("hook_prototype", ""),
            "narrative_structure": proposal.get("narrative_structure", ""),
            "keywords": proposal.get("keywords", []),
            "member_ids": proposal["member_ids"],
            "note": proposal["note"],
        }
        if proposal.get("existing_dna_code"):
            payload["existing_dna_code"] = proposal["existing_dna_code"]
        upsert_suggestion(
            db,
            kind=KIND,
            left_id=str(uuid.uuid4()),
            right_id=None,
            verdict=proposal["name"],
            votes=len(proposal["member_ids"]),
            reason=json.dumps(payload, ensure_ascii=False),
        )
    return len(proposals) + len(attaches)
