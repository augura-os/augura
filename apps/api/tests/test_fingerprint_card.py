"""Tests for fingerprint_card：parse_card 校验 + match_cards 规则矩阵。

parse_card 校验 LLM 返回（不合法 → None 灰区）；match_cards 是纯函数，
不 mock，直接构 FingerprintCard 跑全规则矩阵。
"""
from __future__ import annotations

from typing import Any

from app.services import ip_pack
from app.services.fingerprint_card import (
    FingerprintCard,
    cache_key,
    match_cards,
    parse_card,
)


def _raw(**overrides: Any) -> dict[str, Any]:
    """一张合法卡片的 LLM 原始返回（值取骨架库内代码）。"""
    raw: dict[str, Any] = {
        "family": "slg",
        "track": "sim-builder",
        "skeleton": "slg-zero-to-prosperity",
        "skeleton_new": None,
        "ending": "success",
        "loop_signature": "base-builder",
        "hook_fingerprint": "前 3 秒灾难钩",
        "gameplay_fingerprint": "建造升级实机画面",
        "presentation_tags": ["ugc-voiceover"],
        "variant_notes": "换皮",
        "misleading": False,
    }
    raw.update(overrides)
    return raw


def _card(**overrides: Any) -> FingerprintCard:
    card = parse_card(_raw(**overrides))
    assert card is not None
    return card


class TestParseCard:
    def test_valid_card(self) -> None:
        card = parse_card(_raw())
        assert card is not None
        assert card.family == "slg"
        assert card.track == "sim-builder"
        assert card.skeleton == "slg-zero-to-prosperity"
        assert card.ending == "success"
        assert card.presentation_tags == ["ugc-voiceover"]
        assert card.to_dict()["card_version"] == ip_pack.CARD_PROMPT_VERSION

    def test_not_a_dict(self) -> None:
        assert parse_card(None) is None
        assert parse_card("slg") is None
        assert parse_card([]) is None

    def test_illegal_family(self) -> None:
        assert parse_card(_raw(family="rpg")) is None

    def test_track_outside_family(self) -> None:
        # drama 是 merge 的 track，不属于 slg
        assert parse_card(_raw(track="drama")) is None
        # family/track 必须配套
        assert parse_card(_raw(family="merge", track="sim-builder")) is None

    def test_new_v41_skeletons_accepted(self) -> None:
        # v4.1 收编进库的 3 个 skeleton_new 提案
        for code in (
            "slg-humiliation-level-hook",
            "survival-crisis-drama-hook",
            "slg-fake-drama-hook",
        ):
            assert parse_card(_raw(skeleton=code)) is not None

    def test_track_candidates_first_candidate_wins(self) -> None:
        """软分层：最终 track = 首个候选（裸 track 字段让位）。"""
        card = parse_card(_raw(
            track="other",
            track_candidates=[
                {"track": "idle-growth", "evidence": "放置升级画面为主"},
                {"track": "sim-builder", "evidence": "含少量建造镜头"},
            ],
        ))
        assert card is not None
        assert card.track == "idle-growth"
        assert card.track_candidates == [
            {"track": "idle-growth", "evidence": "放置升级画面为主"},
            {"track": "sim-builder", "evidence": "含少量建造镜头"},
        ]
        assert card.to_dict()["track_candidates"][0]["track"] == "idle-growth"

    def test_track_candidates_missing_tolerated(self) -> None:
        """track_candidates 缺省容忍为 []，回退裸 track 字段。"""
        card = parse_card(_raw())
        assert card is not None
        assert card.track == "sim-builder"
        assert card.track_candidates == []

    def test_track_candidates_invalid_structure(self) -> None:
        # 非 list → 结构非法 → None
        assert parse_card(_raw(track_candidates="sim-builder")) is None

    def test_track_candidates_filter_invalid_items(self) -> None:
        # 非法候选项（库外 track / 非 dict）被过滤；全过滤光且裸 track 非法 → None
        card = parse_card(_raw(track_candidates=[
            {"track": "not-a-track", "evidence": "x"},
            "junk",
            {"track": "idle-growth", "evidence": "放置升级"},
        ]))
        assert card is not None
        assert card.track == "idle-growth"
        assert len(card.track_candidates) == 1
        # 候选全非法 + 裸 track 也非法 → None
        assert parse_card(_raw(
            track="not-a-track",
            track_candidates=[{"track": "also-bad", "evidence": "x"}],
        )) is None

    def test_skeleton_outside_library_without_proposal(self) -> None:
        # 返回了库外代码却没走 skeleton_new 通道 → 不可信
        assert parse_card(_raw(skeleton="slg-invented-code")) is None

    def test_no_skeleton_no_proposal(self) -> None:
        # 既没选码也没提案 → 无效卡
        assert parse_card(_raw(skeleton=None, skeleton_new=None)) is None
        assert parse_card(_raw(skeleton=None, skeleton_new="")) is None

    def test_skeleton_new_proposal_accepted(self) -> None:
        card = parse_card(_raw(skeleton=None, skeleton_new=" slg-绝境谈判 "))
        assert card is not None
        assert card.skeleton is None
        assert card.skeleton_new == "slg-绝境谈判"  # 去首尾空白

    def test_presentation_tags_filtered(self) -> None:
        card = parse_card(_raw(presentation_tags=[
            "ugc-voiceover", "not-a-tag", "ai-generated", 42,
        ]))
        assert card is not None
        assert card.presentation_tags == ["ugc-voiceover", "ai-generated"]
        card = parse_card(_raw(presentation_tags="ugc-voiceover"))
        assert card is not None
        assert card.presentation_tags == []

    def test_invalid_ending_falls_back_to_unknown(self) -> None:
        assert _card(ending="happily-ever-after").ending == "unknown"

    def test_empty_gameplay_fingerprint_becomes_none(self) -> None:
        assert _card(gameplay_fingerprint="").gameplay_fingerprint is None
        assert _card(gameplay_fingerprint=None).gameplay_fingerprint is None

    def test_misleading_cast_to_bool(self) -> None:
        assert _card(misleading=1).misleading is True
        assert _card().misleading is False


class TestCacheKey:
    def test_stable_and_distinct(self) -> None:
        assert cache_key("同一份证据") == cache_key("同一份证据")
        assert cache_key("证据 a") != cache_key("证据 b")


class TestMatchCards:
    """规则矩阵：family/track/skeleton/ending 逐级短路，skeleton_new 灰区。"""

    def test_identical_cards_same_variant_family(self) -> None:
        assert match_cards(_card(), _card()) == "same_variant_family"

    def test_different_family_short_circuits(self) -> None:
        other = _card(family="merge", track="gameplay", skeleton="mg-pure-merge")
        assert match_cards(_card(), other) == "different_concept"

    def test_different_track(self) -> None:
        assert (
            match_cards(_card(), _card(track="tower-defense"))
            == "different_concept"
        )

    def test_different_skeleton(self) -> None:
        assert (
            match_cards(_card(), _card(skeleton="slg-pull-pin"))
            == "different_concept"
        )

    def test_different_ending_both_known(self) -> None:
        assert (
            match_cards(_card(ending="success"), _card(ending="fail"))
            == "different_concept"
        )
        assert (
            match_cards(_card(ending="twist-up"), _card(ending="twist-down"))
            == "different_concept"
        )

    def test_unknown_or_none_ending_never_splits(self) -> None:
        for ending in ("unknown", "none"):
            assert (
                match_cards(_card(ending="success"), _card(ending=ending))
                == "same_variant_family"
            )

    def test_skeleton_new_is_grey_zone(self) -> None:
        new = _card(skeleton=None, skeleton_new="slg-绝境谈判")
        assert match_cards(_card(), new) is None
        assert match_cards(new, _card()) is None
        assert match_cards(new, new) is None

    def test_variant_dimensions_never_split(self) -> None:
        """presentation_tags / hook / variant_notes 等变体维度永不拆族。"""
        a = _card(presentation_tags=["ugc-voiceover"], variant_notes="英文版")
        b = _card(
            presentation_tags=["live-action-drama"],
            hook_fingerprint="完全不同的钩子",
            gameplay_fingerprint="描述换了措辞",
            variant_notes="真人版",
            loop_signature="builder",
        )
        assert match_cards(a, b) == "same_variant_family"
