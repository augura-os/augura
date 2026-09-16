"""DNA family classifier — rules first, LLM (self-consistency N=3) fallback.

规则层是弱监督 labeling functions：analysis 关键词 → 钩子原型 → 家族。
LLM 层用自洽性采样（app.services.llm_judge.vote_json），3/3 一致才给
自动归族级置信；2/3 给建议；分裂则不判定（纯人工）。

词表运行时读取（规则层回流 L4）：settings 键 rule_keywords:mechanic /
rule_keywords:hook（JSON 数组，收件箱确认写入）优先，空则回落 ip_pack
常量——人工确认的新词下一轮判定即生效，绝不写回代码。
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Creative, CreativeDNA
from app.repositories.settings import SettingsRepository
from app.services import ip_pack
from app.services.llm_judge import vote_json
from app.services.settings import AIConfig

logger = logging.getLogger(__name__)

# 归族 prompt 集中在 ip_pack（公私分离隔离点）
_FAMILY_SYSTEM = ip_pack.DNA_CLASSIFIER_SYSTEM

# 词表进程内缓存 TTL：确认 → 生效的延迟上限
_KEYWORD_CACHE_TTL = 60.0


class _KeywordTables:
    """带过期时间戳的词表缓存。

    坑：模块级裸常量（import 时求值一次，如旧的 _MECHANIC_KEYWORDS =
    ip_pack.MECHANIC_KEYWORDS）让 settings 里确认的新词永远进不了运行
    时——进程不重启就不生效，规则层回流形同虚设。60s TTL 是"确认 →
    生效"可接受的延迟，过期后下一次判定重读 settings。
    """

    def __init__(self) -> None:
        self.expires_at = 0.0
        self.mechanic: list[tuple[str, str]] = []
        self.hook: list[tuple[str, str]] = []


_tables = _KeywordTables()


def _resolve_keywords(
    db: Session, target: str, fallback: list[tuple[str, str]]
) -> list[tuple[str, str]]:
    """settings 键 rule_keywords:{target}（JSON 数组）优先，空/非法回落 ip_pack。

    settings 词的匹配 pattern 就是转义后的词本身（ip_pack 真版的 pattern
    可含同义写法正则，学习到的词没有这层人工知识，逐字匹配）。
    """
    raw = SettingsRepository(db).get(f"rule_keywords:{target}")
    if raw:
        try:
            words = json.loads(raw)
        except (TypeError, ValueError):
            words = None
        if isinstance(words, list):
            resolved = [
                (word.strip(), re.escape(word.strip().lower()))
                for word in words
                if isinstance(word, str) and word.strip()
            ]
            if resolved:
                return resolved
    return fallback


def _keyword_tables(db: Session) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """(机制词表, 钩子词表)：60s 内命中进程缓存，过期重读 settings。"""
    now = time.monotonic()
    if now >= _tables.expires_at:
        _tables.mechanic = _resolve_keywords(db, "mechanic", ip_pack.MECHANIC_KEYWORDS)
        _tables.hook = _resolve_keywords(db, "hook", ip_pack.HOOK_PROTOTYPE_KEYWORDS)
        _tables.expires_at = now + _KEYWORD_CACHE_TTL
    return _tables.mechanic, _tables.hook


@dataclass
class DnaSuggestion:
    dna: CreativeDNA
    votes: int  # 3 = 自动级（3/3 一致），2 = 建议级（2/3）
    reason: str


def _rule_suggest(hook_text: str, gameplay_text: str, db: Session) -> DnaSuggestion | None:
    """机制优先：机制关键词命中定族（机制定族、钩子校验）；钩子单独命中只给建议级。"""
    text = f"{hook_text} {gameplay_text}".lower()
    dnas = list(db.scalars(select(CreativeDNA).where(CreativeDNA.status == "active")).all())
    mechanic_keywords, hook_keywords = _keyword_tables(db)

    mech_hit = next((m for m, pattern in mechanic_keywords if re.search(pattern, text)), None)
    hook_hit = next((h for h, pattern in hook_keywords if re.search(pattern, text)), None)

    if mech_hit is not None:
        # 机制命中族（机制定族）；钩子一致则理由更足，不一致也归（钩子在标签层）
        for dna in dnas:
            if mech_hit in dna.core_mechanic:
                reason = f"机制命中：{mech_hit}"
                if hook_hit:
                    reason += f"；钩子参考：{hook_hit}"
                return DnaSuggestion(dna=dna, votes=2, reason=reason)

    if hook_hit is None:
        return None
    # 机制未命中 → 钩子最接近的家族，建议级
    for dna in dnas:
        if hook_hit in dna.hook_prototype:
            return DnaSuggestion(dna=dna, votes=2, reason=f"钩子命中（机制未命中）：{hook_hit}")
    return None


def suggest_dna(
    creative: Creative,
    hook_text: str,
    gameplay_text: str,
    db: Session,
    config: AIConfig | None = None,
) -> DnaSuggestion | None:
    """Rule layer first; LLM self-consistency fallback."""
    ruled = _rule_suggest(hook_text, gameplay_text, db)
    if ruled is not None:
        return ruled
    if config is None or not config.api_key:
        return None

    dnas = list(db.scalars(select(CreativeDNA).where(CreativeDNA.status == "active")).all())
    catalog = "\n".join(
        f"- {d.code} {d.name}：钩子 {d.hook_prototype} / "
        f"机制 {d.core_mechanic} / 叙事 {d.narrative_structure}"
        for d in dnas
    )
    user = (
        f"候选家族：\n{catalog}\n\n"
        f"Creative 名称：{creative.name}\n钩子：{hook_text}\n玩法：{gameplay_text}"
    )
    winner, count, reasons = vote_json(
        config, system=_FAMILY_SYSTEM, user=user, key="dna_code", n=3
    )
    if winner is None or count < 2:
        return None
    dna = next((d for d in dnas if d.code == winner), None)
    if dna is None:
        return None
    reason = reasons[0] if reasons else "LLM 自洽性投票"
    return DnaSuggestion(dna=dna, votes=count, reason=f"{reason}（{count}/3 票）")
