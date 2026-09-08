"""DNA family classifier — rules first, LLM (self-consistency N=3) fallback.

规则层是弱监督 labeling functions：analysis 关键词 → 钩子原型 → 家族。
LLM 层用自洽性采样（app.services.llm_judge.vote_json），3/3 一致才给
自动归族级置信；2/3 给建议；分裂则不判定（纯人工）。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Creative, CreativeDNA
from app.services import ip_pack
from app.services.llm_judge import vote_json
from app.services.settings import AIConfig

logger = logging.getLogger(__name__)

# 弱监督 labeling functions 与归族 prompt 集中在 ip_pack（公私分离隔离点）
_HOOK_KEYWORDS = ip_pack.HOOK_PROTOTYPE_KEYWORDS
_MECHANIC_KEYWORDS = ip_pack.MECHANIC_KEYWORDS
_FAMILY_SYSTEM = ip_pack.DNA_CLASSIFIER_SYSTEM


@dataclass
class DnaSuggestion:
    dna: CreativeDNA
    votes: int  # 3 = 自动级（3/3 一致），2 = 建议级（2/3）
    reason: str


def _rule_suggest(hook_text: str, gameplay_text: str, db: Session) -> DnaSuggestion | None:
    """机制优先：机制关键词命中定族（机制定族、钩子校验）；钩子单独命中只给建议级。"""
    text = f"{hook_text} {gameplay_text}".lower()
    dnas = list(db.scalars(select(CreativeDNA).where(CreativeDNA.status == "active")).all())

    mech_hit = next((m for m, pattern in _MECHANIC_KEYWORDS if re.search(pattern, text)), None)
    hook_hit = next((h for h, pattern in _HOOK_KEYWORDS if re.search(pattern, text)), None)

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
