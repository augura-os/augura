"""Tests for derivation-factor pre-adjudication (services/derivation_judge)."""
from __future__ import annotations

from app.services.derivation_judge import judge_factor
from app.services.settings import AIConfig


class _FakeConfig(AIConfig):
    def __init__(self, *, with_key: bool = True) -> None:
        super().__init__(
            api_key="sk-fake" if with_key else "",
            base_url="",
            vision_model="",
            embedding_model="",
        )


def _judge(config: AIConfig):
    return judge_factor(
        config,
        source_name="v1",
        target_name="v2",
        current_factor="aspect-ratio",
        evidence="对齐比例 80%；中段发散 1 段",
        source_analysis="钩子：失败重开",
        target_analysis="钩子：失败重开",
    )


class TestJudgeFactor:
    def test_no_api_key_returns_none(self) -> None:
        assert _judge(_FakeConfig(with_key=False)) is None

    def test_majority_factor_returned(self, monkeypatch) -> None:  # noqa: ANN001
        monkeypatch.setattr(
            "app.services.derivation_judge.vote_json",
            lambda config, *, system, user, key, n: (
                "character-reskin", 3, ["角色全换但玩法一致"],
            ),
        )
        judgement = _judge(_FakeConfig())
        assert judgement is not None
        assert judgement.factor == "character-reskin"
        assert judgement.votes == 3
        assert judgement.reason

    def test_not_a_derivation_is_a_valid_choice(self, monkeypatch) -> None:  # noqa: ANN001
        monkeypatch.setattr(
            "app.services.derivation_judge.vote_json",
            lambda config, *, system, user, key, n: (
                "not-a-derivation", 2, ["内容与测量均不同源"],
            ),
        )
        judgement = _judge(_FakeConfig())
        assert judgement is not None
        assert judgement.factor == "not-a-derivation"
        assert judgement.votes == 2

    def test_low_votes_or_invalid_factor_returns_none(
        self, monkeypatch
    ) -> None:  # noqa: ANN001
        monkeypatch.setattr(
            "app.services.derivation_judge.vote_json",
            lambda config, *, system, user, key, n: ("character-reskin", 1, []),
        )
        assert _judge(_FakeConfig()) is None
        monkeypatch.setattr(
            "app.services.derivation_judge.vote_json",
            lambda config, *, system, user, key, n: ("explode", 3, []),
        )
        assert _judge(_FakeConfig()) is None
        monkeypatch.setattr(
            "app.services.derivation_judge.vote_json",
            lambda config, *, system, user, key, n: ("unknown", 3, []),
        )
        assert _judge(_FakeConfig()) is None
