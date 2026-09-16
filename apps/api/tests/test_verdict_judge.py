"""Tests for verdict pre-adjudication (services/verdict_judge)."""
from __future__ import annotations

from app.schemas.derivation import VariantBrief
from app.services.market_stats import MarketBaseline
from app.services.settings import AIConfig
from app.services.verdict_judge import judge_verdict


class _FakeConfig(AIConfig):
    def __init__(self, *, with_key: bool = True) -> None:
        super().__init__(
            api_key="sk-fake" if with_key else "",
            base_url="",
            vision_model="",
            embedding_model="",
        )


def _brief(spend: float, payers: int, roas: float | None) -> VariantBrief:
    return VariantBrief(
        variant_id="v",
        name="v",
        filename="KS_EN-demo-竖.mp4",
        spend=spend,
        payers=payers,
        cpp=spend / payers if payers else None,
        roas=roas,
    )


class TestJudgeVerdict:
    def test_no_api_key_returns_none(self) -> None:
        assert (
            judge_verdict(
                _FakeConfig(with_key=False),
                creative_name="c",
                factor="language-market",
                source=_brief(500, 10, 0.02),
                target=_brief(600, 12, 0.03),
            )
            is None
        )

    def test_majority_verdict_returned(self, monkeypatch) -> None:  # noqa: ANN001
        monkeypatch.setattr(
            "app.services.verdict_judge.vote_json",
            lambda config, *, system, user, key, n: ("positive", 3, ["成本更低"]),
        )
        judgement = judge_verdict(
            _FakeConfig(),
            creative_name="c",
            factor="aspect-ratio",
            source=_brief(500, 10, 0.02),
            target=_brief(600, 14, 0.03),
        )
        assert judgement is not None
        assert judgement.verdict == "positive"
        assert judgement.votes == 3
        assert judgement.reason

    def test_low_votes_or_invalid_verdict_returns_none(
        self, monkeypatch
    ) -> None:  # noqa: ANN001
        monkeypatch.setattr(
            "app.services.verdict_judge.vote_json",
            lambda config, *, system, user, key, n: ("positive", 1, []),
        )
        assert (
            judge_verdict(
                _FakeConfig(), creative_name="c", factor="aspect-ratio",
                source=_brief(500, 10, 0.02), target=_brief(600, 14, 0.03),
            )
            is None
        )
        monkeypatch.setattr(
            "app.services.verdict_judge.vote_json",
            lambda config, *, system, user, key, n: ("explode", 3, []),
        )
        assert (
            judge_verdict(
                _FakeConfig(), creative_name="c", factor="aspect-ratio",
                source=_brief(500, 10, 0.02), target=_brief(600, 14, 0.03),
            )
            is None
        )


class TestCrossMarketEvidence:
    """language-market 因子 + 可靠市场基准 → 证据组装为市场内相对口径。"""

    def _capture(self, monkeypatch) -> dict[str, str]:  # noqa: ANN001
        captured: dict[str, str] = {}

        def _fake_vote(config, *, system, user, key, n):  # noqa: ANN001, ANN202
            captured["system"] = system
            captured["user"] = user
            return "positive", 3, ["ok"]

        monkeypatch.setattr("app.services.verdict_judge.vote_json", _fake_vote)
        return captured

    def _baseline(self, *, count: int = 5) -> MarketBaseline:
        return MarketBaseline(
            code="EN", cpp_median=150.0, roas_median=0.018, creative_count=count
        )

    def test_reliable_baseline_adds_cross_market_rules(self, monkeypatch) -> None:
        captured = self._capture(monkeypatch)
        judge_verdict(
            _FakeConfig(),
            creative_name="c",
            factor="language-market",
            source=_brief(500, 10, 0.02),  # source CPP $50（便宜市场）
            target=_brief(1600, 10, 0.015),  # target CPP $160（贵市场）
            market_baseline=self._baseline(),
        )
        assert "目标市场基准" in captured["user"]
        assert "$150.00" in captured["user"]
        assert "市场内" in captured["system"] or "目标市场" in captured["system"]
        assert "中位数×1.2" in captured["system"]

    def test_unreliable_baseline_falls_back(self, monkeypatch) -> None:
        captured = self._capture(monkeypatch)
        judge_verdict(
            _FakeConfig(),
            creative_name="c",
            factor="language-market",
            source=_brief(500, 10, 0.02),
            target=_brief(600, 12, 0.03),
            market_baseline=self._baseline(count=2),  # 样本不足 → 退回旧规则
        )
        assert "目标市场基准" not in captured["user"]
        assert "中位数×1.2" not in captured["system"]

    def test_non_language_market_factor_unchanged(self, monkeypatch) -> None:
        captured = self._capture(monkeypatch)
        judge_verdict(
            _FakeConfig(),
            creative_name="c",
            factor="aspect-ratio",
            source=_brief(500, 10, 0.02),
            target=_brief(600, 12, 0.03),
            market_baseline=self._baseline(),  # 非跨市场因子：基准不进场
        )
        assert "目标市场基准" not in captured["user"]
        assert "中位数×1.2" not in captured["system"]
