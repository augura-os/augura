"""规则层回流：人工改判归族 → 挖差异词 → 建议进收件箱 → 确认落 settings。

半RSI「裁决 → 参数」回路的规则层段（阈值学习已跑通，这里修词表）：
人纠正 auto 归族判定时，系统挖出"是哪个词带的偏"，候选词经
judge_suggestions（kind="rule_keyword"）进收件箱，人确认后词表生效。

口径：
- 修正样本：同一 creative 上 field="dna_id" 的 edit_logs 按时间配对——
  ``auto:`` 判定记录 + 其后首条人工记录，两边各过
  judge_calibration._dna_label 归一；标签不同才算改判（人工确认 auto
  结果的不算）。修正样本只用来找"哪些词导致误判"（候选词池）。
- 判别力（附录 B L1）：score(t) = P(t|同族对) − P(t|跨族对)，用**当前
  库状态**（creative.dna_id 分组，同组=同族对、跨组=跨族对）计算——
  不用修正时的历史族归属：改判发生后库状态已是人确认的"正确"分组，
  且随每次修正自我纠错。配对数用组合数代数计算，不物化 O(n²) 对。
- target 分档：score ≥ MECHANIC_SCORE_MIN → 机制词候选（判别力强）；
  score ≤ GENERIC_SCORE_MAX → 通用词候选（几乎无判别力，该进停用词）；
  中间 → 钩子词候选（有判别力但不够定族，弱监督次级信号）。

纪律（附录 A 三条破功动作）：词表绝不写回 ip_pack 或任何代码文件；
不跨客户聚合（单实例天然满足）；写入口只有 confirm 端点一条。
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from math import comb

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import Creative, EditLog
from app.repositories.settings import SettingsRepository
from app.services import ip_pack, markets
from app.services.clustering import _tokens
from app.services.judge_calibration import _dna_label
from app.services.judge_suggestions import upsert_suggestion

logger = logging.getLogger(__name__)

KIND = "rule_keyword"

# 修正样本少于此数不挖——几条改判挖出的"差异词"是个案不是模式
MIN_CORRECTION_SAMPLES = 5
# 候选词至少出现在这么多条已归族素材里才出建议（支撑度，防一词一票）
MIN_TOKEN_SUPPORT = 3
# 判别力分档（见模块 docstring）
MECHANIC_SCORE_MIN = 0.30
GENERIC_SCORE_MAX = 0.05
# 单次最多浮出的建议数（按 |score| 排序截取，防收件箱被刷）
MAX_RULE_SUGGESTIONS = 20

RULE_KEYWORDS_SETTING_TEMPLATE = "rule_keywords:{target}"  # mechanic|hook，JSON 数组


@dataclass
class Correction:
    """一条归族改判：auto 判的族 → 人改的族。"""

    creative_id: str
    auto_label: str
    human_label: str


@dataclass
class KeywordCandidate:
    target: str  # "mechanic" | "hook" | "generic"
    word: str
    score: float
    same_pair_rate: float
    cross_pair_rate: float
    support: int


def correction_samples(db: Session) -> list[Correction]:
    """edit_logs 里 auto 归族 → 人工改判的配对样本（时间序）。"""
    logs = list(
        db.scalars(
            select(EditLog)
            .where(EditLog.field == "dna_id")
            .order_by(EditLog.entity_id, EditLog.created_at)
        ).all()
    )
    corrections: list[Correction] = []
    pending: dict[str, str] = {}  # creative_id → 等待人工处置的 auto 判定标签
    for log in logs:
        label = _dna_label(log.new_value)
        if not label:
            continue
        if log.new_value.startswith("auto:"):
            pending[log.entity_id] = label
            continue
        auto_label = pending.pop(log.entity_id, None)
        if auto_label is not None and auto_label != label:
            corrections.append(
                Correction(
                    creative_id=log.entity_id,
                    auto_label=auto_label,
                    human_label=label,
                )
            )
    return corrections


def _creative_tokens(db: Session, creative_ids: set[str]) -> dict[str, set[str]]:
    """creative → hook/gameplay 文本的分词集合（首个有分析的 variant 取数，
    与 family_bootstrap 的画像取数同路径；CJK bigram 分词复用 clustering）。
    """
    if not creative_ids:
        return {}
    result: dict[str, set[str]] = {}
    for creative_id in creative_ids:
        row = db.execute(
            text(
                "select a.hook, a.gameplay from analysis_results a "
                "join creative_variants v on v.asset_id = a.asset_id "
                "where v.creative_id = :cid limit 1"
            ),
            {"cid": creative_id},
        ).first()
        if row is None:
            continue
        result[creative_id] = _tokens(f"{row[0] or ''} {row[1] or ''}")
    return result


def _known_words(db: Session) -> set[str]:
    """已在词表里的词（ip_pack 常量 + settings 覆盖层 + 通用停用词）——
    不重复建议。"""
    known = {word for word, _pattern in ip_pack.MECHANIC_KEYWORDS}
    known |= {word for word, _pattern in ip_pack.HOOK_PROTOTYPE_KEYWORDS}
    known |= markets.resolve_generic_tokens(db)
    repo = SettingsRepository(db)
    for target in ("mechanic", "hook"):
        raw = repo.get(RULE_KEYWORDS_SETTING_TEMPLATE.format(target=target))
        if raw:
            try:
                words = json.loads(raw)
            except (TypeError, ValueError):
                words = None
            if isinstance(words, list):
                known |= {str(word) for word in words}
    return known


def score_candidates(db: Session, corrections: list[Correction]) -> list[KeywordCandidate]:
    """候选词判别力（附录 B L1）：score = P(t|同族对) − P(t|跨族对)。

    库状态分组算命中率；组合数代数算对数，不物化素材对。
    同族对总数为 0（全是单员族）时无判别力证据，返回空。
    """
    assigned = list(
        db.scalars(select(Creative).where(Creative.dna_id.isnot(None))).all()
    )
    tokens_by_creative = _creative_tokens(db, {c.id for c in assigned})

    family_sizes: Counter[str] = Counter(c.dna_id for c in assigned)
    token_family_counts: dict[str, Counter[str]] = {}
    for creative in assigned:
        for token in tokens_by_creative.get(creative.id, ()):
            token_family_counts.setdefault(token, Counter())[creative.dna_id] += 1

    total = len(assigned)
    same_pairs = sum(comb(n, 2) for n in family_sizes.values())
    cross_pairs = comb(total, 2) - same_pairs
    if same_pairs == 0 or cross_pairs == 0:
        return []

    # 候选词池 = 修正样本素材的文本词（"是哪个词带的偏"）
    corrected_tokens = _creative_tokens(db, {c.creative_id for c in corrections})
    pool: set[str] = set()
    for tokens in corrected_tokens.values():
        pool |= tokens
    pool -= _known_words(db)

    candidates: list[KeywordCandidate] = []
    for token in sorted(pool):
        counts = token_family_counts.get(token)
        if counts is None:
            continue
        support = sum(counts.values())
        if support < MIN_TOKEN_SUPPORT:
            continue
        same_hit = sum(comb(c, 2) for c in counts.values())
        p_same = same_hit / same_pairs
        p_cross = (comb(support, 2) - same_hit) / cross_pairs
        score = p_same - p_cross
        if score >= MECHANIC_SCORE_MIN:
            target = "mechanic"
        elif score <= GENERIC_SCORE_MAX:
            target = "generic"
        else:
            target = "hook"
        candidates.append(
            KeywordCandidate(
                target=target, word=token, score=score,
                same_pair_rate=p_same, cross_pair_rate=p_cross, support=support,
            )
        )
    candidates.sort(key=lambda c: abs(c.score), reverse=True)
    return candidates[:MAX_RULE_SUGGESTIONS]


def suggest_keywords(db: Session, *, now: datetime | None = None) -> int:
    """挖规则词建议写 judge_suggestions（upsert 幂等）；返回建议数。

    修正样本不足 MIN_CORRECTION_SAMPLES 时直接跳过（返回 0）。
    """
    corrections = correction_samples(db)
    if len(corrections) < MIN_CORRECTION_SAMPLES:
        logger.debug(
            "规则词挖掘跳过：修正样本 %d/%d", len(corrections), MIN_CORRECTION_SAMPLES
        )
        return 0
    candidates = score_candidates(db, corrections)
    learned_at = (now or datetime.now(timezone.utc)).isoformat()
    for candidate in candidates:
        payload = {
            "target": candidate.target,
            "word": candidate.word,
            "score": round(candidate.score, 4),
            "evidence": {
                "same_pair_rate": round(candidate.same_pair_rate, 4),
                "cross_pair_rate": round(candidate.cross_pair_rate, 4),
                "support": candidate.support,
            },
            "learned_at": learned_at,
        }
        # 同一 target+word 反复只更新同一条（upsert 按 subject 幂等）
        upsert_suggestion(
            db,
            kind=KIND,
            left_id=f"{candidate.target}:{candidate.word}",
            right_id=None,
            verdict=candidate.word,
            votes=candidate.support,
            reason=json.dumps(payload, ensure_ascii=False),
        )
    return len(candidates)
