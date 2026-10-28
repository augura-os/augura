"""骨架指纹卡（merge-judge-v4.1）：LLM 打卡 + 代码规则匹配 + 判异复核闸。

架构（2026-10-22 定稿，2026-10-26 v4.1 修订）：
- 每个 creative 由 LLM 打一次"骨架指纹卡"（fingerprint card），结果按
  证据文本哈希缓存——成对判定不再重复调用 LLM；
- 成对关系由代码规则从两张卡推出（零成本、可复现、可回归）：
    family 不同        → different_concept
    track 不同         → different_concept
    skeleton 不同      → different_concept
    ending 双方非空且不同 → different_concept（结局是骨架身份的一部分）
    以上全同           → same_variant_family
  任一卡打不出 / skeleton_new（骨架库外新结构）→ 灰区（None，纯人工）；
- v4.1 软分层：track_candidates（1-2 个候选赛道+证据）先列出，skeleton
  从候选赛道的骨架并集中选码——只改善选码稳定性，match_cards 规则不变；
- v4.1 判异复核闸：different_concept 必须经 recheck_split 靶向复核
  （验证分歧点而非重投整对），复核推翻或失败 → 灰区人工；
- 变体维度（换角色/场景/美术/前贴/BGM/口播/真人贴片/presentation_tags）
  永不参与拆族——只记录，不判决；
- loop_signature 在 v4.x 只记录不参与判决（调参观察位，见 B2 回测）。
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from app.services import ip_pack
from app.services.llm_judge import complete_json
from app.services.settings import AIConfig

logger = logging.getLogger(__name__)

CARD_VERSION = ip_pack.CARD_PROMPT_VERSION

ENDING_VALUES = (
    "success", "fail", "twist-up", "twist-down", "open", "none", "unknown",
)


@dataclass
class FingerprintCard:
    family: str
    track: str
    skeleton: str | None  # None 表示打不出（灰区）
    skeleton_new: str | None  # 骨架库外的新结构提案（进人工命名）
    ending: str
    loop_signature: str
    hook_fingerprint: str
    gameplay_fingerprint: str | None
    presentation_tags: list[str] = field(default_factory=list)
    track_candidates: list[dict[str, str]] = field(default_factory=list)  # v4.1 软分层
    variant_notes: str = ""
    misleading: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "track": self.track,
            "skeleton": self.skeleton,
            "skeleton_new": self.skeleton_new,
            "ending": self.ending,
            "loop_signature": self.loop_signature,
            "hook_fingerprint": self.hook_fingerprint,
            "gameplay_fingerprint": self.gameplay_fingerprint,
            "presentation_tags": self.presentation_tags,
            "track_candidates": self.track_candidates,
            "variant_notes": self.variant_notes,
            "misleading": self.misleading,
            "card_version": CARD_VERSION,
        }


def cache_key(evidence_text: str) -> str:
    """卡片缓存键：证据文本哈希（同一份分析证据永远打到同一张卡）。"""
    return hashlib.sha256(evidence_text.encode("utf-8")).hexdigest()[:24]


def _taxonomy_brief() -> str:
    """把骨架库渲染进 user prompt（family → tracks → skeletons）。"""
    lines: list[str] = []
    for family in ip_pack.CREATIVE_FAMILIES:
        tracks = ip_pack.FAMILY_TRACKS.get(family, ())
        lines.append(f"family={family} tracks={','.join(tracks)}")
        for code, desc in ip_pack.SKELETONS.get(family, {}).items():
            lines.append(f"  {code}: {desc}")
    lines.append(
        "presentation_tags: " + ",".join(ip_pack.PRESENTATION_TAGS)
    )
    return "\n".join(lines)


def parse_card(raw: Any) -> FingerprintCard | None:
    """校验 LLM 返回；结构不合法 → None（灰区，不硬猜）。"""
    if not isinstance(raw, dict):
        return None
    family = raw.get("family")
    if family not in ip_pack.CREATIVE_FAMILIES:
        return None
    valid_tracks = ip_pack.FAMILY_TRACKS.get(family, ())
    # v4.1 软分层：track_candidates 每项 {track, evidence}，track 必须在本
    # family 赛道表内；缺省容忍为 []，结构非法（非 list）→ None。
    raw_candidates = raw.get("track_candidates")
    track_candidates: list[dict[str, str]] = []
    if raw_candidates is not None:
        if not isinstance(raw_candidates, list):
            return None
        for item in raw_candidates:
            if not isinstance(item, dict):
                continue
            candidate = item.get("track")
            if candidate not in valid_tracks:
                continue
            evidence = item.get("evidence")
            track_candidates.append({
                "track": candidate,
                "evidence": evidence.strip() if isinstance(evidence, str) else "",
            })
    # 最终 track = 首个候选；候选缺省时回退到裸 track 字段（兼容旧卡）
    if track_candidates:
        track = track_candidates[0]["track"]
    else:
        track = raw.get("track")
        if track not in valid_tracks:
            return None
    skeleton = raw.get("skeleton")
    skeleton_new = raw.get("skeleton_new")
    if skeleton is not None and skeleton not in ip_pack.SKELETONS.get(family, {}):
        return None  # 返回了库外代码却没走 skeleton_new 通道 → 不可信
    if skeleton is None and not (isinstance(skeleton_new, str) and skeleton_new):
        return None  # 既没选码也没提案 → 无效卡
    ending = raw.get("ending")
    if ending not in ENDING_VALUES:
        ending = "unknown"
    tags = raw.get("presentation_tags")
    if not isinstance(tags, list):
        tags = []
    tags = [t for t in tags if t in ip_pack.PRESENTATION_TAGS]

    def _str(key: str) -> str:
        v = raw.get(key)
        return v.strip() if isinstance(v, str) else ""

    gameplay_fp = _str("gameplay_fingerprint")
    return FingerprintCard(
        family=family,
        track=track,
        skeleton=skeleton,
        skeleton_new=(
            skeleton_new.strip() if isinstance(skeleton_new, str) and skeleton_new else None
        ),
        ending=ending,
        loop_signature=_str("loop_signature"),
        hook_fingerprint=_str("hook_fingerprint"),
        gameplay_fingerprint=gameplay_fp or None,
        presentation_tags=tags,
        track_candidates=track_candidates,
        variant_notes=_str("variant_notes"),
        misleading=bool(raw.get("misleading")),
    )


def build_card(
    config: AIConfig, evidence_text: str, *, max_tokens: int = 1200,
) -> FingerprintCard | None:
    """调 LLM 打一个 creative 的指纹卡；失败（限流/解析错）→ None。"""
    if not evidence_text.strip():
        return None
    user = f"【骨架库】\n{_taxonomy_brief()}\n\n【素材分析证据】\n{evidence_text}"
    raw = complete_json(config, system=ip_pack.FINGERPRINT_CARD_SYSTEM,
                        user=user, max_tokens=max_tokens)
    return parse_card(raw)


def get_card(
    config: AIConfig,
    evidence_text: str,
    cache: dict[str, FingerprintCard | None] | None = None,
    *,
    max_tokens: int = 1200,
) -> FingerprintCard | None:
    """带缓存的打卡；cache 键为证据哈希。None 也缓存（避免重复烧限流）。"""
    if cache is None:
        return build_card(config, evidence_text, max_tokens=max_tokens)
    key = cache_key(evidence_text)
    if key not in cache:
        cache[key] = build_card(config, evidence_text, max_tokens=max_tokens)
    return cache[key]


def match_cards(a: FingerprintCard, b: FingerprintCard) -> str | None:
    """代码规则匹配：返回 relation，或 None（灰区，交人工）。

    skeleton_new（库外新结构）参与判同太危险 → 灰区人工命名后再判。
    """
    if a.skeleton is None or b.skeleton is None:
        return None  # 打不出骨架（含 skeleton_new）→ 灰区
    if a.family != b.family:
        return "different_concept"
    if a.track != b.track:
        return "different_concept"
    if a.skeleton != b.skeleton:
        return "different_concept"
    if (
        a.ending not in ("unknown", "none")
        and b.ending not in ("unknown", "none")
        and a.ending != b.ending
    ):
        return "different_concept"
    return "same_variant_family"


def divergence_point(a: FingerprintCard, b: FingerprintCard) -> dict[str, str] | None:
    """按 match_cards 的短路顺序定位首个分歧层（family/track/skeleton/ending）。

    仅用于判异复核闸的输入；无分歧（同族）→ None。
    """
    if a.family != b.family:
        return {"field": "family", "a": a.family, "b": b.family}
    if a.track != b.track:
        return {"field": "track", "a": a.track, "b": b.track}
    if a.skeleton != b.skeleton:
        return {"field": "skeleton", "a": a.skeleton or "", "b": b.skeleton or ""}
    if (
        a.ending not in ("unknown", "none")
        and b.ending not in ("unknown", "none")
        and a.ending != b.ending
    ):
        return {"field": "ending", "a": a.ending, "b": b.ending}
    return None


def recheck_split(
    config: AIConfig,
    *,
    card_a: FingerprintCard,
    card_b: FingerprintCard,
    evidence_a: str,
    evidence_b: str,
    divergence: dict[str, str],
) -> dict[str, Any] | None:
    """判异复核闸：靶向验证分歧点是否成立（不是重投整对判定）。

    返回 {"split_confirmed": bool, "reason": str}；LLM 失败/返回结构非法
    → None（调用方路由为灰区人工，不落判异）。
    """
    user = (
        f"【分歧点】{divergence['field']}: A={divergence['a']} "
        f"vs B={divergence['b']}\n\n"
        f"【指纹卡 A】\n{json.dumps(card_a.to_dict(), ensure_ascii=False)}\n\n"
        f"【指纹卡 B】\n{json.dumps(card_b.to_dict(), ensure_ascii=False)}\n\n"
        f"【素材 A 分析证据】\n{evidence_a}\n\n"
        f"【素材 B 分析证据】\n{evidence_b}"
    )
    raw = complete_json(config, system=ip_pack.SPLIT_RECHECK_SYSTEM,
                        user=user, max_tokens=600)
    if not isinstance(raw, dict):
        return None
    confirmed = raw.get("split_confirmed")
    if not isinstance(confirmed, bool):
        return None
    reason = raw.get("reason")
    return {
        "split_confirmed": confirmed,
        "reason": reason.strip() if isinstance(reason, str) else "",
    }
