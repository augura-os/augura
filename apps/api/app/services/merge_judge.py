"""Pairwise merge judge（merge-judge-v4.1：指纹卡 + 代码规则 + 判异复核闸）。

v3 → v4（2026-10-22，架构升级）：成对 LLM 三视角投票 → 每素材一次 LLM
打"骨架指纹卡"（fingerprint_card.py，按证据哈希缓存）+ 代码规则匹配。
same_concept 废弃，只留三态：same_variant_family / different_concept /
灰区（返回 None）。

v4 → v4.1（2026-10-26，B2 回测修复）：
- 收窄 slg-zero-to-prosperity（v4 默认引力码）；
- track_candidates 软分层选码（降低选码抖动）；
- 判异复核闸：match_cards 判 different_concept 的对子全部先经
  recheck_split 靶向复核（验证分歧点，不重投整对）；复核推翻或调用失败
  → None（灰区人工），只有 split_confirmed=true 才落判异；
- 收编 3 个 skeleton_new 提案进骨架库（ip_pack.SKELETONS["slg"]）。

设计依据（详见设计文档 Augura_AI自动判异设计方案_20261020.md）：
- 判同轴 = family（品类）→ track（赛道）→ skeleton（叙事骨架，含结局）；
- 变体维度（换前贴/演员/文案/场景/BGM/口播/真人贴片/presentation_tags）
  永不拆族；same_concept 中间态在 B2 回测中从未被用对，废弃；
- A/B 段边界 = 核心玩法实机画面首次持续 ≥3s 出现的那一帧，A 段硬上限
  15s（den-creative-framework A-Segment 规则；hook 功能边界而非秒数）。

机制要点：
- votes 固定记 2——v4.x 不开放自动合并闸门（judge_pipeline 要求
  votes==3），待 B2 回测验证后再议；
- LLM 限流/解析失败、skeleton_new（库外新结构）、复核推翻 → 返回
  None（灰区人工）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.services import ip_pack
from app.services.fingerprint_card import (
    FingerprintCard,
    divergence_point,
    get_card,
    match_cards,
    recheck_split,
)
from app.services.settings import AIConfig

logger = logging.getLogger(__name__)

PROMPT_VERSION = ip_pack.MERGE_JUDGE_PROMPT_VERSION

RELATIONS = ("same_variant_family", "different_concept")


@dataclass
class PairEvidence:
    """一侧 creative 的分析证据文本（v4：打指纹卡的原料）。

    structure/visual/gameplay 三视图划分保留——既兼容既有调用方与测试，
    也让证据缺口的处理显式化；打卡时拼为 full_text。
    """

    structure: str = ""  # 全片结构：summary/hook/conflict/gameplay/reward
    visual: str = ""  # 视觉构成：characters/environment/emotion/tags
    gameplay: str = ""  # 机制本体：gameplay/conflict/variant_factors

    def view(self, name: str) -> str:
        return getattr(self, name, "") or ""

    def full_text(self) -> str:
        return "\n".join(
            p for p in (self.structure, self.visual, self.gameplay) if p
        ).strip()


def evidence_from_analysis(analysis: dict[str, Any]) -> PairEvidence:
    """analysis_results 行字段 → 三视角证据。缺字段的视角留空。

    键：summary/hook/conflict/gameplay/reward/characters/environment/
    emotion/tags/variant_factors（list 字段会 json 序列化为紧凑文本）。
    """

    def _join(parts: list[str]) -> str:
        return "\n".join(p for p in parts if p)

    def _list_text(value: Any) -> str:
        if not isinstance(value, list):
            return ""
        return "、".join(str(v) for v in value if v)

    return PairEvidence(
        structure=_join(
            [
                f"摘要：{analysis.get('summary') or ''}",
                f"钩子：{analysis.get('hook') or ''}",
                f"冲突：{analysis.get('conflict') or ''}",
                f"玩法：{analysis.get('gameplay') or ''}",
                f"回报：{analysis.get('reward') or ''}",
            ]
        ).strip(),
        visual=_join(
            [
                f"角色：{_list_text(analysis.get('characters'))}",
                f"环境：{_list_text(analysis.get('environment'))}",
                f"情绪：{_list_text(analysis.get('emotion'))}",
                f"标签：{_list_text(analysis.get('tags'))}",
            ]
        ).strip(),
        gameplay=_join(
            [
                f"玩法：{analysis.get('gameplay') or ''}",
                f"冲突：{analysis.get('conflict') or ''}",
                f"裂变因子：{_list_text(analysis.get('variant_factors'))}",
            ]
        ).strip(),
    )


@dataclass
class MergeJudgement:
    relation: str  # RELATIONS 之一
    votes: int  # v4.x 固定 2（不开放自动合并闸门，见模块 docstring）
    reason: str  # 人读摘要
    core: dict[str, str] = field(default_factory=dict)  # v4 弃用，留兼容
    variant: dict[str, str] = field(default_factory=dict)  # v4 弃用，留兼容
    cards: dict[str, dict[str, Any]] = field(default_factory=dict)  # a/b 指纹卡
    recheck: dict[str, Any] | None = None  # v4.1 判异复核结论（仅判异路径）
    model_id: str = ""
    prompt_version: str = PROMPT_VERSION

    @property
    def same_creative(self) -> bool:
        """creative 层（variant family）是否同一条：仅 same_variant_family。"""
        return self.relation == "same_variant_family"

    def reason_params(self) -> dict[str, Any]:
        """落库 judge_suggestions.reason_params 的结构化解释。"""
        return {
            "relation": self.relation,
            "cards": self.cards,
            "recheck": self.recheck,
            "model_id": self.model_id,
            "prompt_version": self.prompt_version,
        }


def _reason(relation: str, a: FingerprintCard, b: FingerprintCard) -> str:
    if relation == "same_variant_family":
        return f"同骨架 {a.skeleton}（结局一致：{a.ending}）"
    if a.family != b.family:
        return f"品类族不同 {a.family} ≠ {b.family}"
    if a.track != b.track:
        return f"赛道不同 {a.track} ≠ {b.track}"
    if a.skeleton != b.skeleton:
        return f"骨架不同 {a.skeleton} ≠ {b.skeleton}"
    return f"结局不同 {a.ending} ≠ {b.ending}"


def judge_pair(
    config: AIConfig,
    *,
    evidence_a: PairEvidence,
    evidence_b: PairEvidence,
    card_cache: dict[str, FingerprintCard | None] | None = None,
) -> MergeJudgement | None:
    """打卡（按证据哈希缓存）→ 代码规则匹配 →（判异时）复核闸。

    任一卡打不出（LLM 失败/证据为空）或 skeleton_new → 返回 None（灰区，
    纯人工）。判异必须经 recheck_split 复核确认；复核推翻或调用失败同样
    灰区。card_cache 由调用方持有一次批量运行共享，避免同素材重复
    调 LLM（限流环境下是关键成本项）。
    """
    card_a = get_card(config, evidence_a.full_text(), card_cache)
    card_b = get_card(config, evidence_b.full_text(), card_cache)
    if card_a is None or card_b is None:
        return None
    relation = match_cards(card_a, card_b)
    if relation is None:
        return None
    recheck: dict[str, Any] | None = None
    if relation == "different_concept":
        divergence = divergence_point(card_a, card_b) or {}
        recheck = recheck_split(
            config,
            card_a=card_a, card_b=card_b,
            evidence_a=evidence_a.full_text(), evidence_b=evidence_b.full_text(),
            divergence=divergence,
        )
        if recheck is None or not recheck["split_confirmed"]:
            return None  # 复核失败/推翻 → 灰区人工，不落判异
    reason = _reason(relation, card_a, card_b)
    if recheck:
        reason += f"（复核确认：{recheck['reason']}）"
    return MergeJudgement(
        relation=relation,
        votes=2,
        reason=reason,
        cards={"a": card_a.to_dict(), "b": card_b.to_dict()},
        recheck=recheck,
        model_id=config.vision_model,
    )
