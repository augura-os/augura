"""Pure-rule and prompt-contract tests for the isolated family experiment."""
from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from app.services import merge_family_experiment as experiment


def _raw(**overrides: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "content_mode": "gameplay",
        "game_category": "slg",
        "track_candidates": ["sim-builder"],
        "core_skeleton": "slg-zero-to-prosperity",
        "core_skeleton_new": None,
        "core_evidence": "连续建造空地并升级为繁荣城镇",
        "ending": "success",
        "ending_evidence": "最后建成城堡并庆祝",
        "loop_signature": "build-upgrade",
        "hook_fingerprint": "真人短剧前贴",
        "gameplay_fingerprint": "建造升级实机画面",
        "presentation_tags": ["live-action-drama"],
        "variant_notes": "换演员",
    }
    raw.update(overrides)
    return raw


def _card(**overrides: Any) -> experiment.ExperimentCard:
    card = experiment.parse_experiment_card(_raw(**overrides))
    assert card is not None
    return card


class TestExperimentCard:
    def test_round_trip(self) -> None:
        card = _card()
        assert experiment.parse_experiment_card(card.to_dict()) == card

    @pytest.mark.parametrize("code", ["slg-reverse-choice-fail", "slg-low-iq-fail"])
    def test_user_confirmed_failure_split_codes(self, code: str) -> None:
        assert _card(core_skeleton=code, ending="fail").core_skeleton == code

    def test_old_combined_failure_parent_is_uncertain_not_an_error(self) -> None:
        card = experiment.parse_experiment_card(_raw(
            core_skeleton="slg-deliberate-fail", ending="fail",
        ))
        assert card is not None
        assert card.core_skeleton is None
        assert card.core_skeleton_new == "slg-deliberate-fail"
        assert experiment.match_experiment_cards(card, card).relation == "uncertain"

    def test_narrative_body_can_use_narrative_skeleton(self) -> None:
        card = _card(
            content_mode="narrative",
            core_skeleton="survival-crisis-drama-hook",
            gameplay_fingerprint=None,
        )
        assert card.core_skeleton == "survival-crisis-drama-hook"

    @pytest.mark.parametrize("mode", ["gameplay", "hybrid", "narrative", "unknown"])
    def test_hook_only_code_never_describes_body(self, mode: str) -> None:
        card = experiment.parse_experiment_card(_raw(
            content_mode=mode, core_skeleton="slg-fake-drama-hook",
        ))
        assert card is not None
        assert card.core_skeleton is None
        assert card.core_skeleton_new == "slg-fake-drama-hook"

    def test_explicit_empty_identity_is_uncertain_not_an_error(self) -> None:
        card = _card(core_skeleton=None, core_skeleton_new=None, core_evidence="")
        assert card.core_skeleton is None
        assert experiment.match_experiment_cards(card, card).relation == "uncertain"
        raw = _raw()
        del raw["core_skeleton"]
        del raw["core_skeleton_new"]
        assert experiment.parse_experiment_card(raw) is None
        card = _card(core_skeleton=None, core_skeleton_new="new-body-loop", core_evidence="")
        assert card.core_skeleton_new == "new-body-loop"

    def test_missing_identity_evidence_becomes_uncertain(self) -> None:
        no_core_evidence = experiment.parse_experiment_card(_raw(core_evidence=""))
        assert no_core_evidence is not None
        assert no_core_evidence.core_skeleton is None
        assert no_core_evidence.core_skeleton_new == "slg-zero-to-prosperity"
        no_ending_evidence = _card(ending_evidence="")
        assert no_ending_evidence.ending == "unknown"
        assert experiment.match_experiment_cards(
            no_ending_evidence, no_ending_evidence,
        ).relation == "uncertain"

    def test_descriptive_field_errors_are_cleaned_not_card_failures(self) -> None:
        card = experiment.parse_experiment_card(_raw(
            game_category="other",
            track_candidates=["slg-survival-dilemma", "slg-fake-drama-hook"],
            core_skeleton="slg-survival-dilemma",
            presentation_tags="live-action-drama",
        ))
        assert card is not None
        assert card.game_category == "slg"
        assert card.track_candidates == []
        assert card.presentation_tags == []

    def test_build_allows_reasoning_and_json_output_tokens(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: dict[str, Any] = {}

        def fake_complete(*args: Any, **kwargs: Any) -> dict[str, Any]:
            captured.update(kwargs)
            return _raw()

        monkeypatch.setattr(experiment, "complete_json", fake_complete)
        card = experiment.build_experiment_card(object(), "证据")
        assert card is not None
        assert captured["max_tokens"] == 2400


class TestExperimentMatch:
    def test_track_hook_and_presentation_do_not_split(self) -> None:
        a = _card()
        b = _card(
            track_candidates=["idle-growth"],
            hook_fingerprint="完全不同的动画前贴",
            presentation_tags=["ai-generated"],
        )
        assert experiment.match_experiment_cards(a, b).relation == "same"

    def test_core_skeleton_conflict_splits(self) -> None:
        assert experiment.match_experiment_cards(
            _card(), _card(core_skeleton="slg-survival-dilemma"),
        ).relation == "diff"

    def test_reverse_choice_and_low_iq_failures_are_different_families(self) -> None:
        reverse = _card(core_skeleton="slg-reverse-choice-fail", ending="fail")
        low_iq = _card(core_skeleton="slg-low-iq-fail", ending="fail")
        assert experiment.match_experiment_cards(reverse, low_iq).relation == "diff"

    @pytest.mark.parametrize("ending", ["fail", "open", "twist-up", "twist-down"])
    def test_explicit_ending_conflict_splits(self, ending: str) -> None:
        assert experiment.match_experiment_cards(
            _card(), _card(ending=ending),
        ).relation == "diff"

    @pytest.mark.parametrize("ending", ["unknown", "none"])
    def test_unknown_or_unclear_ending_is_not_a_wildcard(self, ending: str) -> None:
        assert experiment.match_experiment_cards(
            _card(), _card(ending=ending, ending_evidence=""),
        ).relation == "uncertain"

    def test_new_skeleton_is_uncertain_not_same(self) -> None:
        new = _card(core_skeleton=None, core_skeleton_new="novel-loop", core_evidence="")
        assert experiment.match_experiment_cards(new, new).relation == "uncertain"

    def test_missing_evidence_is_uncertain(self) -> None:
        a = replace(_card(), core_evidence="")
        assert experiment.match_experiment_cards(a, _card()).relation == "uncertain"


class TestBlindReviewContract:
    def test_difference_prompt_does_not_contain_prior_relation(self) -> None:
        prompt = experiment.difference_user_prompt(_card(), _card(), "证据甲", "证据乙")
        assert "same_variant_family" not in prompt
        assert "different_concept" not in prompt
        assert "prior_verdict" not in prompt
        assert "证据甲" in prompt and "证据乙" in prompt

    def test_adjudication_uses_differences_and_empty_examples_for_holdout(self) -> None:
        differences = {
            "core_differences": [],
            "variant_differences": ["换前贴"],
            "evidence_gaps": [],
        }
        prompt = experiment.adjudication_user_prompt(differences)
        assert "换前贴" in prompt
        assert "[]" in prompt
        assert "different_concept" not in prompt

    def test_confirmed_examples_are_rules_not_surface_answers(self) -> None:
        prompt = experiment.adjudication_user_prompt(
            {"core_differences": [], "variant_differences": [], "evidence_gaps": []},
            [{"relation": "same", "decisive_rule": "场景数量不拆族"}],
        )
        assert "场景数量不拆族" in prompt
        assert "apply their rules, not surface matching" in prompt

    def test_parse_differences_and_adjudication(self) -> None:
        differences = experiment.parse_differences({
            "core_differences": [{
                "axis": "ending", "a": "success", "b": "fail",
                "evidence_a": "建成", "evidence_b": "失败",
            }],
            "variant_differences": ["换演员"],
            "evidence_gaps": [],
        })
        assert differences is not None
        assert differences["core_differences"][0]["axis"] == "ending"
        result = experiment.parse_adjudication({
            "relation": "diff", "decisive_axis": "ending", "reason": "结局不同",
        })
        assert result is not None and result.relation == "diff"
        assert experiment.parse_adjudication({"relation": "same", "reason": ""}) is None


class TestCacheAndBudget:
    def test_cache_key_is_unordered_and_versioned(self, monkeypatch: pytest.MonkeyPatch) -> None:
        key = experiment.pair_cache_key("differences", "甲", "乙", "model-a")
        assert key == experiment.pair_cache_key("differences", "乙", "甲", "model-a")
        assert key != experiment.pair_cache_key("differences", "甲", "乙", "model-b")
        monkeypatch.setattr(experiment, "EXPERIMENT_VERSION", "new-prompt")
        assert key != experiment.pair_cache_key("differences", "乙", "甲", "model-a")
        monkeypatch.setattr(experiment, "TAXONOMY_VERSION", "new-taxonomy")
        assert key != experiment.pair_cache_key("differences", "乙", "甲", "model-a")

    def test_budget_blocks_request_101(self) -> None:
        budget = experiment.RequestBudget(limit=100)
        for _ in range(100):
            budget.consume()
        assert budget.used == 100
        assert budget.remaining == 0
        with pytest.raises(experiment.RequestBudgetExceeded):
            budget.consume()
        assert budget.used == 100
        assert budget.blocked is True
