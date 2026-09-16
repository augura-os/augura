"""Unit tests for app.services.clustering (cosine + token-Jaccard fallback)."""
from __future__ import annotations

import pytest

from app.models import Creative
from app.services.clustering import (
    best_creative_match_by_text,
    cosine_similarity,
    decide_cluster,
    running_mean,
    token_similarity,
    top_creative_matches,
    top_creative_matches_by_text,
)


class TestCosineSimilarity:
    def test_identical(self) -> None:
        assert cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)

    def test_orthogonal(self) -> None:
        assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_degenerate_inputs(self) -> None:
        assert cosine_similarity([], [1.0]) == 0.0
        assert cosine_similarity([1.0], [1.0, 2.0]) == 0.0
        assert cosine_similarity([0.0, 0.0], [0.0, 0.0]) == 0.0


class TestTokenSimilarity:
    def test_identical(self) -> None:
        assert token_similarity("fail retry island", "fail retry island") == 1.0

    def test_disjoint(self) -> None:
        assert token_similarity("aaa bbb", "ccc ddd") == 0.0

    def test_cjk_shared_chars_and_bigrams(self) -> None:
        # 老人掉水 / 女孩掉水 share 掉水 bigram + unigrams → partial similarity.
        score = token_similarity("老人掉水", "女孩掉水")
        assert 0.0 < score < 1.0

    def test_empty(self) -> None:
        assert token_similarity("", "abc") == 0.0


class TestBestCreativeMatchByText:
    def test_picks_name_weighted_best(self) -> None:
        target = Creative(name="fail-retry-island-survival",
                          representative_text="fail-retry-island-survival fail retry")
        other = Creative(name="garbage-mountain-town-management",
                         representative_text="garbage-mountain-town-management")
        best, score = best_creative_match_by_text(
            "fail-retry-island-survival", "fail retry island", [other, target]
        )
        assert best is target
        assert score > 0.5

    def test_skips_empty_reference(self) -> None:
        empty = Creative(name="", representative_text="")
        best, score = best_creative_match_by_text("some-name", "text", [empty])
        assert best is None
        assert score == 0.0

    def test_falls_back_to_name_as_reference(self) -> None:
        creative = Creative(name="no-ads-pure-building", representative_text="")
        best, _ = best_creative_match_by_text("no-ads-pure-building", "", [creative])
        assert best is creative


class TestTopCreativeMatches:
    def test_top2_descending(self) -> None:
        a = Creative(name="a", representative_embedding=[1.0, 0.0])
        b = Creative(name="b", representative_embedding=[0.9, 0.1])
        c = Creative(name="c", representative_embedding=[0.0, 1.0])
        matches = top_creative_matches([1.0, 0.0], [c, b, a], limit=2)
        assert [m[0] for m in matches] == [a, b]
        assert matches[0][1] > matches[1][1]

    def test_skips_missing_embedding(self) -> None:
        creative = Creative(name="a", representative_embedding=None)
        assert top_creative_matches([1.0], [creative]) == []

    def test_text_top2(self) -> None:
        target = Creative(name="fail-retry-island-survival",
                          representative_text="fail retry island survival")
        near = Creative(name="fail-retry-island",
                        representative_text="fail retry island")
        far = Creative(name="garbage-town",
                       representative_text="garbage town management")
        matches = top_creative_matches_by_text(
            "fail-retry-island-survival", "fail retry island survival",
            [far, near, target], limit=2,
        )
        assert [m[0] for m in matches] == [target, near]

    def test_text_skips_empty_reference(self) -> None:
        empty = Creative(name="", representative_text="")
        assert top_creative_matches_by_text("some-name", "text", [empty]) == []


class TestDecideCluster:
    """margin 决策矩阵：阈值 × 候选数 × margin。"""

    @staticmethod
    def _c(name: str) -> Creative:
        return Creative(name=name)

    def test_no_candidates_create(self) -> None:
        decision = decide_cluster([], 0.85)
        assert decision.action == "create"
        assert decision.creative is None
        assert decision.score == 0.0
        assert decision.margin is None

    def test_below_threshold_create(self) -> None:
        creative = self._c("a")
        decision = decide_cluster([(creative, 0.5)], 0.85)
        assert decision.action == "create"
        assert decision.score == 0.5

    def test_clear_winner_attach(self) -> None:
        a, b = self._c("a"), self._c("b")
        decision = decide_cluster([(a, 0.90), (b, 0.80)], 0.85)
        assert decision.action == "attach"
        assert decision.creative is a
        assert decision.margin == pytest.approx(0.10)

    def test_single_candidate_attach(self) -> None:
        a = self._c("a")
        decision = decide_cluster([(a, 0.86)], 0.85)
        assert decision.action == "attach"
        assert decision.margin is None

    def test_twins_inbox(self) -> None:
        a, b = self._c("a"), self._c("b")
        decision = decide_cluster([(a, 0.90), (b, 0.87)], 0.85)
        assert decision.action == "inbox"
        assert decision.creative is a
        assert decision.margin == pytest.approx(0.03)

    def test_margin_just_above_boundary_attach(self) -> None:
        a, b = self._c("a"), self._c("b")
        decision = decide_cluster([(a, 0.90), (b, 0.84)], 0.85)
        assert decision.action == "attach"

    def test_both_below_threshold_create(self) -> None:
        a, b = self._c("a"), self._c("b")
        decision = decide_cluster([(a, 0.30), (b, 0.29)], 0.34)
        assert decision.action == "create"


class TestRunningMean:
    def test_first_variant_uses_new(self) -> None:
        assert running_mean(None, [1.0, 2.0], 0) == [1.0, 2.0]

    def test_mean_with_count(self) -> None:
        assert running_mean([1.0, 3.0], [3.0, 1.0], 1) == [2.0, 2.0]

    def test_count_scales_old_value(self) -> None:
        # 已有 3 个变体均值 1.0，新值 5.0 → (1*3 + 5) / 4 = 2.0
        assert running_mean([1.0], [5.0], 3) == [2.0]

    def test_dimension_mismatch_falls_back(self) -> None:
        assert running_mean([1.0], [1.0, 2.0], 3) == [1.0, 2.0]
