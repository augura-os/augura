"""Offline single-product family experiment; never called by the production judge."""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field
from typing import Any

from app.services import ip_pack
from app.services.llm_judge import complete_json
from app.services.settings import AIConfig

CONTENT_MODES = ("gameplay", "narrative", "hybrid", "unknown")
ENDING_VALUES = (
    "success", "fail", "twist-up", "twist-down", "open", "none", "unknown",
)
DIFFERENCE_AXES = ("mechanic", "goal", "loop", "narrative", "ending", "other")
HOOK_ONLY_SKELETONS = frozenset({"slg-fake-drama-hook", "pz-ai-spectacle"})
EXPERIMENT_VERSION = ip_pack.MERGE_FAMILY_EXPERIMENT_VERSION
TAXONOMY_VERSION = ip_pack.MERGE_FAMILY_EXPERIMENT_TAXONOMY_VERSION

EXPERIMENT_SLG_SPLITS = {
    "slg-reverse-choice-fail": (
        "多次二选一反向选择失败（ending=fail）。连续/多次呈现两个选项，"
        "以反向或错误选择形成失败、重开。必须有多次二选一结构；单次拖拽修/救"
        "选 slg-drag-item-fix；无连续二选一的低智商操作选 slg-low-iq-fail"
    ),
    "slg-low-iq-fail": (
        "低智商/明显错误操作失败（ending=fail）。以愚蠢或明显错误操作导致失败，"
        "不具有连续多次二选一反向选择结构；不得吸收多次二选一反向选择"
    ),
}


def experiment_skeletons(family: str) -> dict[str, str]:
    skeletons = dict(ip_pack.SKELETONS.get(family, {}))
    if family == "slg":
        skeletons.pop("slg-deliberate-fail", None)
        skeletons.update(EXPERIMENT_SLG_SPLITS)
    return skeletons


class RequestBudgetExceeded(RuntimeError):
    pass


@dataclass
class RequestBudget:
    limit: int = 100
    used: int = 0
    blocked: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def consume(self) -> None:
        with self._lock:
            if self.used >= self.limit:
                self.blocked = True
                raise RequestBudgetExceeded(f"LLM request budget exhausted: {self.limit}")
            self.used += 1

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)


@dataclass
class ExperimentCard:
    content_mode: str
    game_category: str
    track_candidates: list[str]
    core_skeleton: str | None
    core_skeleton_new: str | None
    core_evidence: str
    ending: str
    ending_evidence: str
    loop_signature: str
    hook_fingerprint: str
    gameplay_fingerprint: str | None
    presentation_tags: list[str] = field(default_factory=list)
    variant_notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "content_mode": self.content_mode,
            "game_category": self.game_category,
            "track_candidates": self.track_candidates,
            "core_skeleton": self.core_skeleton,
            "core_skeleton_new": self.core_skeleton_new,
            "core_evidence": self.core_evidence,
            "ending": self.ending,
            "ending_evidence": self.ending_evidence,
            "loop_signature": self.loop_signature,
            "hook_fingerprint": self.hook_fingerprint,
            "gameplay_fingerprint": self.gameplay_fingerprint,
            "presentation_tags": self.presentation_tags,
            "variant_notes": self.variant_notes,
            "card_version": EXPERIMENT_VERSION,
            "taxonomy_version": TAXONOMY_VERSION,
        }


@dataclass
class ExperimentDecision:
    relation: str
    reason: str
    source: str = "rule"


@dataclass
class Adjudication:
    relation: str
    decisive_axis: str
    reason: str


def _text(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    return value.strip() if isinstance(value, str) else ""


def _taxonomy() -> str:
    lines: list[str] = []
    for family in ip_pack.CREATIVE_FAMILIES:
        lines.append(f"{family} tracks: {','.join(ip_pack.FAMILY_TRACKS.get(family, ()))}")
        for code, description in experiment_skeletons(family).items():
            hook_only = " [HOOK-ONLY for a gameplay body]" if code in HOOK_ONLY_SKELETONS else ""
            lines.append(f"  {code}{hook_only}: {description}")
    lines.append("presentation_tags: " + ",".join(ip_pack.PRESENTATION_TAGS))
    return "\n".join(lines)


def _skeleton_family(skeleton: str | None) -> str | None:
    if skeleton is None:
        return None
    for family in ip_pack.CREATIVE_FAMILIES:
        if skeleton in experiment_skeletons(family):
            return family
    return None


def parse_experiment_card(raw: Any) -> ExperimentCard | None:
    """Clean descriptive fields; route uncertain identity to review instead of error."""
    if not isinstance(raw, dict):
        return None
    content_mode = raw.get("content_mode")
    if content_mode not in CONTENT_MODES:
        content_mode = "unknown"
    raw_skeleton = raw.get("core_skeleton")
    skeleton = raw_skeleton.strip() if isinstance(raw_skeleton, str) else ""
    skeleton = skeleton or None
    skeleton_new = _text(raw, "core_skeleton_new") or None
    skeleton_family = _skeleton_family(skeleton)
    if skeleton is not None and (skeleton_family is None or skeleton in HOOK_ONLY_SKELETONS):
        skeleton_new = skeleton_new or skeleton
        skeleton = None
        skeleton_family = None
    core_evidence = _text(raw, "core_evidence")
    if skeleton is not None and not core_evidence:
        skeleton_new = skeleton_new or skeleton
        skeleton = None
        skeleton_family = None
    if skeleton is None and skeleton_new is None and not (
        "core_skeleton" in raw or "core_skeleton_new" in raw
    ):
        return None
    raw_category = raw.get("game_category")
    if skeleton_family is not None:
        game_category = skeleton_family
    elif raw_category in ip_pack.CREATIVE_FAMILIES:
        game_category = raw_category
    else:
        game_category = "other"
    ending = raw.get("ending")
    if ending not in ENDING_VALUES:
        ending = "unknown"
    ending_evidence = _text(raw, "ending_evidence")
    if ending not in ("none", "unknown") and not ending_evidence:
        ending = "unknown"
    raw_tracks = raw.get("track_candidates")
    if not isinstance(raw_tracks, list):
        raw_tracks = []
    valid_tracks = ip_pack.FAMILY_TRACKS.get(game_category, ())
    tracks = list(dict.fromkeys(
        item.strip() for item in raw_tracks
        if isinstance(item, str) and item.strip() in valid_tracks
    ))[:2]
    raw_tags = raw.get("presentation_tags")
    if not isinstance(raw_tags, list):
        raw_tags = []
    tags = list(dict.fromkeys(
        item for item in raw_tags if item in ip_pack.PRESENTATION_TAGS
    ))
    gameplay = _text(raw, "gameplay_fingerprint")
    return ExperimentCard(
        content_mode=content_mode,
        game_category=game_category,
        track_candidates=tracks,
        core_skeleton=skeleton,
        core_skeleton_new=skeleton_new,
        core_evidence=core_evidence,
        ending=ending,
        ending_evidence=ending_evidence,
        loop_signature=_text(raw, "loop_signature"),
        hook_fingerprint=_text(raw, "hook_fingerprint"),
        gameplay_fingerprint=gameplay or None,
        presentation_tags=tags,
        variant_notes=_text(raw, "variant_notes"),
    )


def build_experiment_card(config: AIConfig, evidence_text: str) -> ExperimentCard | None:
    if not evidence_text.strip():
        return None
    raw = complete_json(
        config,
        system=ip_pack.MERGE_FAMILY_EXPERIMENT_CARD_SYSTEM,
        user=f"【Taxonomy】\n{_taxonomy()}\n\n【Creative evidence】\n{evidence_text}",
        max_tokens=2400,
    )
    return parse_experiment_card(raw)


def versioned_cache_key(stage: str, payload: str, model: str) -> str:
    material = json.dumps(
        {
            "stage": stage,
            "payload": payload,
            "model": model,
            "experiment_version": EXPERIMENT_VERSION,
            "taxonomy_version": TAXONOMY_VERSION,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def pair_cache_key(stage: str, evidence_a: str, evidence_b: str, model: str) -> str:
    payload = json.dumps(sorted([evidence_a, evidence_b]), ensure_ascii=False)
    return versioned_cache_key(stage, payload, model)


def match_experiment_cards(a: ExperimentCard, b: ExperimentCard) -> ExperimentDecision:
    if a.core_skeleton is None or b.core_skeleton is None:
        return ExperimentDecision("uncertain", "至少一方主体骨架未知或库外")
    if not a.core_evidence or not b.core_evidence:
        return ExperimentDecision("uncertain", "主体骨架证据不足")
    if a.core_skeleton != b.core_skeleton:
        return ExperimentDecision("diff", "主体骨架明确不同")
    if a.ending in ("unknown", "none") or b.ending in ("unknown", "none"):
        return ExperimentDecision("uncertain", "结局未知或语义不明，不能通配")
    if a.ending != b.ending:
        return ExperimentDecision("diff", "结局明确不同")
    if not a.ending_evidence or not b.ending_evidence:
        return ExperimentDecision("uncertain", "结局证据不足")
    return ExperimentDecision("same", "主体骨架与明确结局一致")


def difference_user_prompt(
    card_a: ExperimentCard,
    card_b: ExperimentCard,
    evidence_a: str,
    evidence_b: str,
) -> str:
    return (
        f"【Card A】\n{json.dumps(card_a.to_dict(), ensure_ascii=False)}\n\n"
        f"【Card B】\n{json.dumps(card_b.to_dict(), ensure_ascii=False)}\n\n"
        f"【Evidence A】\n{evidence_a}\n\n【Evidence B】\n{evidence_b}"
    )


def parse_differences(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    if any(not isinstance(raw.get(key), list) for key in (
        "core_differences", "variant_differences", "evidence_gaps",
    )):
        return None
    core: list[dict[str, str]] = []
    for item in raw["core_differences"]:
        if not isinstance(item, dict) or item.get("axis") not in DIFFERENCE_AXES:
            continue
        core.append({
            key: _text(item, key)
            for key in ("axis", "a", "b", "evidence_a", "evidence_b")
        })
    return {
        "core_differences": core,
        "variant_differences": [
            item.strip() for item in raw["variant_differences"]
            if isinstance(item, str) and item.strip()
        ],
        "evidence_gaps": [
            item.strip() for item in raw["evidence_gaps"]
            if isinstance(item, str) and item.strip()
        ],
    }


def generate_differences(
    config: AIConfig,
    *,
    card_a: ExperimentCard,
    card_b: ExperimentCard,
    evidence_a: str,
    evidence_b: str,
) -> dict[str, Any] | None:
    raw = complete_json(
        config,
        system=ip_pack.MERGE_FAMILY_EXPERIMENT_DIFF_SYSTEM,
        user=difference_user_prompt(card_a, card_b, evidence_a, evidence_b),
        max_tokens=1000,
    )
    return parse_differences(raw)


def parse_adjudication(raw: Any) -> Adjudication | None:
    if not isinstance(raw, dict) or raw.get("relation") not in (
        "same", "diff", "uncertain",
    ):
        return None
    reason = _text(raw, "reason")
    if not reason:
        return None
    return Adjudication(
        relation=raw["relation"],
        decisive_axis=_text(raw, "decisive_axis"),
        reason=reason,
    )


def adjudication_user_prompt(
    differences: dict[str, Any], confirmed_examples: list[dict[str, Any]] | None = None,
) -> str:
    return (
        "【Independent differences】\n"
        f"{json.dumps(differences, ensure_ascii=False, sort_keys=True)}\n\n"
        "【User-confirmed examples: apply their rules, not surface matching; may be empty】\n"
        f"{json.dumps(confirmed_examples or [], ensure_ascii=False)}"
    )


def adjudicate_differences(
    config: AIConfig,
    differences: dict[str, Any],
    confirmed_examples: list[dict[str, Any]] | None = None,
) -> Adjudication | None:
    raw = complete_json(
        config,
        system=ip_pack.MERGE_FAMILY_EXPERIMENT_ADJUDICATE_SYSTEM,
        user=adjudication_user_prompt(differences, confirmed_examples),
        max_tokens=500,
    )
    return parse_adjudication(raw)
