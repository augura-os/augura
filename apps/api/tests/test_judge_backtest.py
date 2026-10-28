"""judge_backtest harness v4.2：语义灰区不重试、基础设施失败有限重试、
只清当前对缓存、预算上限 not_run、复核统计不混淆。

mock 点在 fingerprint_card.complete_json（同 test_llm_judge）——不触真实
LLM / DB / Neo4j；runner.run 单线程顺序执行，不用线程池，保证确定性。
"""
from __future__ import annotations

from typing import Any

import pytest

from app.services import fingerprint_card, ip_pack
from app.services.fingerprint_card import cache_key
from app.services.merge_judge import PairEvidence
from app.services.settings import AIConfig
from scripts.judge_backtest import (
    ABSTAIN_BUDGET,
    ABSTAIN_NO_EVIDENCE,
    ABSTAIN_RECHECK_OVERTURNED,
    ABSTAIN_SKELETON_NEW,
    FAIL_CARD,
    FAIL_RECHECK,
    BacktestRunner,
    classify_none,
    summarize_recheck,
    summarize_results,
)


class _FakeConfig(AIConfig):
    def __init__(self) -> None:
        super().__init__(api_key="", base_url="", vision_model="", embedding_model="")


def _evidence(text: str) -> PairEvidence:
    return PairEvidence(structure=text, visual=text, gameplay=text)


def _task(
    a: str, b: str, gold: str = "same"
) -> tuple[str, str, str, PairEvidence, PairEvidence]:
    return (f"{gold}:{a}:{b}", gold, f"{a}↔{b}", _evidence(a), _evidence(b))


def _card_raw(
    *,
    family: str = "slg",
    track: str = "sim-builder",
    skeleton: str | None = "slg-zero-to-prosperity",
    skeleton_new: str | None = None,
    ending: str = "success",
    **overrides: Any,
) -> dict[str, Any]:
    """符合 FingerprintCard schema 的 LLM 返回（值取骨架库内代码）。"""
    raw: dict[str, Any] = {
        "family": family,
        "track": track,
        "skeleton": skeleton,
        "skeleton_new": skeleton_new,
        "ending": ending,
        "loop_signature": "base-builder",
        "hook_fingerprint": "前 3 秒灾难钩",
        "gameplay_fingerprint": "建造升级实机画面",
        "presentation_tags": ["ugc-voiceover"],
        "variant_notes": "换皮",
        "misleading": False,
    }
    raw.update(overrides)
    return raw


class _Router:
    """按证据文本片段路由 complete_json 返回；recheck 走单独响应序列。

    每个片段首次命中后消费掉对应响应（失败→恢复场景按序给多个响应）；
    未预设的证据直接 AssertionError，防止意外真实路径。
    """

    def __init__(
        self,
        cards: dict[str, list[dict[str, Any] | None]],
        recheck: list[dict[str, Any] | None] | dict[str, Any] | None = None,
    ) -> None:
        self.cards = {k: list(v) for k, v in cards.items()}
        if recheck is None or isinstance(recheck, dict):
            self.recheck = recheck
        else:
            self.recheck = list(recheck)
        self.recheck_calls = 0
        self.card_calls: list[str] = []

    def __call__(self, config: Any, *, system: str, user: str, **kwargs: Any) -> Any:
        if system == ip_pack.SPLIT_RECHECK_SYSTEM:
            self.recheck_calls += 1
            if isinstance(self.recheck, list):
                return self.recheck.pop(0)
            return self.recheck
        evidence_text = user.rsplit("【素材分析证据】\n", 1)[-1]
        for fragment, queue in self.cards.items():
            if fragment in evidence_text:
                self.card_calls.append(fragment)
                if not queue:
                    raise AssertionError(f"片段 {fragment} 的响应已耗尽")
                return queue.pop(0)
        raise AssertionError(f"未预设卡片的证据：{evidence_text[-80:]}")


@pytest.fixture()
def runner() -> BacktestRunner:
    # sleep 注入 no-op：测试不重试等待
    return BacktestRunner(_FakeConfig(), max_attempts=4, sleep_seconds=20,
                          sleep=lambda _s: None)


class TestSemanticGreyNoRetry:
    """语义灰区（复核推翻 / skeleton_new）一次出结论，重试=重 roll LLM 口径。"""

    def test_recheck_overturned_no_retry(
        self, monkeypatch: pytest.MonkeyPatch, runner: BacktestRunner
    ) -> None:
        router = _Router(
            {"素材A": [_card_raw()], "素材B": [_card_raw(skeleton="slg-pull-pin")]},
            recheck={"split_confirmed": False, "reason": "分歧点不成立"},
        )
        monkeypatch.setattr(fingerprint_card, "complete_json", router)
        with runner:
            result = runner.run(_task("素材A", "素材B", gold="diff"))
        assert result["status"] == "grey"
        assert result["relation"] == "grey"  # 保持下游兼容
        assert result["attempts"] == 1  # 不重试
        assert result["abstain"] == ABSTAIN_RECHECK_OVERTURNED
        assert router.recheck_calls == 1  # 复核只调一次，不被重抽到确认为止
        assert runner.recheck_stats == {"overturned": 1}

    def test_skeleton_new_no_retry(
        self, monkeypatch: pytest.MonkeyPatch, runner: BacktestRunner
    ) -> None:
        router = _Router(
            {
                "素材A": [_card_raw()],
                "素材B": [_card_raw(skeleton=None, skeleton_new="新结构提案")],
            }
        )
        monkeypatch.setattr(fingerprint_card, "complete_json", router)
        with runner:
            result = runner.run(_task("素材A", "素材B"))
        assert result["status"] == "grey"
        assert result["attempts"] == 1
        assert result["abstain"] == ABSTAIN_SKELETON_NEW
        assert router.recheck_calls == 0  # 卡阶段已灰区，不触发复核
        assert runner.recheck_stats == {}

    def test_missing_evidence_no_llm_retry(
        self, monkeypatch: pytest.MonkeyPatch, runner: BacktestRunner
    ) -> None:
        def _unexpected_call(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("空证据不应调用 LLM")

        monkeypatch.setattr(fingerprint_card, "complete_json", _unexpected_call)
        with runner:
            result = runner.run(_task("", "素材B"))
        assert result["status"] == "grey"
        assert result["attempts"] == 0
        assert result["abstain"] == ABSTAIN_NO_EVIDENCE
        assert runner.llm_calls == 0

    def test_cards_valid_but_no_recheck_evidence_is_grey(
        self, monkeypatch: pytest.MonkeyPatch, runner: BacktestRunner
    ) -> None:
        """有效卡 + 无复核记录却返回 None：无基础设施失败证据，不重试。

        用假 judge_pair 造出这种与生产实现矛盾的 None（正常路径走不到）——
        先把两张有效卡写进缓存再返回 None，验证分类器的防御分支不误重试。
        """
        import scripts.judge_backtest as backtest_mod

        def _fake_judge_pair(config: Any, *, evidence_a: Any, evidence_b: Any,
                             card_cache: Any = None) -> Any:
            card_cache[cache_key(evidence_a.full_text())] = fingerprint_card.parse_card(
                _card_raw()
            )
            card_cache[cache_key(evidence_b.full_text())] = fingerprint_card.parse_card(
                _card_raw()
            )
            return None

        monkeypatch.setattr(backtest_mod, "judge_pair", _fake_judge_pair)
        with runner:
            result = runner.run(_task("素材A", "素材B"))
        assert result["status"] == "grey"
        assert result["attempts"] == 1
        assert result["abstain"] == ABSTAIN_NO_EVIDENCE


class TestInfraFailureRetries:
    """基础设施失败（打卡/复核调用失败）按对有限重试，只清当前对缓存。"""

    def test_card_failure_then_success(
        self, monkeypatch: pytest.MonkeyPatch, runner: BacktestRunner
    ) -> None:
        router = _Router(
            {"素材A": [None, _card_raw()], "素材B": [_card_raw()]}
        )
        monkeypatch.setattr(fingerprint_card, "complete_json", router)
        with runner:
            result = runner.run(_task("素材A", "素材B"))
        assert result["status"] == "same"
        assert result["relation"] == "same_variant_family"
        assert result["attempts"] == 2  # 第 1 次打卡失败，重试 1 次后恢复
        assert router.card_calls == ["素材A", "素材B", "素材A"]

    def test_recheck_failure_then_confirmed(
        self, monkeypatch: pytest.MonkeyPatch, runner: BacktestRunner
    ) -> None:
        router = _Router(
            {"素材A": [_card_raw()], "素材B": [_card_raw(skeleton="slg-pull-pin")]},
            recheck=[None, {"split_confirmed": True, "reason": "分歧成立"}],
        )
        monkeypatch.setattr(fingerprint_card, "complete_json", router)
        with runner:
            result = runner.run(_task("素材A", "素材B", gold="diff"))
        assert result["status"] == "diff"
        assert result["attempts"] == 2
        assert router.recheck_calls == 2
        assert runner.recheck_stats == {"failed": 1, "confirmed": 1}

    def test_retry_only_clears_current_pair_cards(
        self, monkeypatch: pytest.MonkeyPatch, runner: BacktestRunner
    ) -> None:
        """重试前只清当前对打卡失败的条目：其他对的失败缓存不动。"""
        from app.services.fingerprint_card import cache_key

        router = _Router({"素材A": [None, _card_raw()], "素材B": [_card_raw()]})
        monkeypatch.setattr(fingerprint_card, "complete_json", router)
        # 预置：其他对（素材C）的失败缓存 + 当前对无关条目不应被清
        foreign_failed_key = cache_key(_evidence("素材C").full_text())
        runner.card_cache[foreign_failed_key] = None
        with runner:
            result = runner.run(_task("素材A", "素材B"))
        assert result["status"] == "same"
        assert runner.card_cache.get(foreign_failed_key, "已消失") is None
        # 素材C 的缓存条目原样保留（不重建、不重打）——重试没动它
        assert foreign_failed_key in runner.card_cache
        assert router.card_calls == ["素材A", "素材B", "素材A"]

    def test_retry_keeps_skeleton_new_card(
        self, monkeypatch: pytest.MonkeyPatch, runner: BacktestRunner
    ) -> None:
        """一侧打卡失败 + 另一侧 skeleton_new：只重打失败卡，不重 roll 新结构卡。"""
        router = _Router(
            {
                "素材A": [None, _card_raw()],
                "素材B": [_card_raw(skeleton=None, skeleton_new="新结构提案")],
            }
        )
        monkeypatch.setattr(fingerprint_card, "complete_json", router)
        with runner:
            result = runner.run(_task("素材A", "素材B"))
        # 首次：A 打卡失败（infra 可重试）；重试后 A 恢复，B 是 skeleton_new
        # → 语义灰区出结论，不重试；B 的卡全程只打一次
        assert result["status"] == "grey"
        assert result["attempts"] == 2
        assert result["abstain"] == ABSTAIN_SKELETON_NEW
        assert router.card_calls == ["素材A", "素材B", "素材A"]

    def test_infra_exhausted_is_error_not_grey(
        self, monkeypatch: pytest.MonkeyPatch, runner: BacktestRunner
    ) -> None:
        router = _Router(
            {"素材A": [None, None, None, None], "素材B": [_card_raw()]}
        )
        monkeypatch.setattr(fingerprint_card, "complete_json", router)
        with runner:
            result = runner.run(_task("素材A", "素材B"))
        assert result["status"] == "error"
        assert result["relation"] == "error"
        assert result["attempts"] == 4  # 达到上限
        assert result["abstain"] == FAIL_CARD
        assert result["error"]  # error 信息非空
        # B 一次成功即缓存命中，之后 3 轮只重打 A
        assert router.card_calls == ["素材A", "素材B", "素材A", "素材A", "素材A"]
        assert summarize_results([result])["grey"] == 0  # 失败不入灰区


class TestBudgetNotRun:
    """--max-llm-calls 耗尽：未跑/未完的对标 not_run，不入灰区与分母。"""

    def test_budget_exhaustion_marks_not_run(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        router = _Router(
            {
                "素材A": [_card_raw()],
                "素材B": [_card_raw()],
                "素材C": [_card_raw()],
                "素材D": [_card_raw()],
            }
        )
        monkeypatch.setattr(fingerprint_card, "complete_json", router)
        runner = BacktestRunner(
            _FakeConfig(), max_attempts=4, sleep=lambda _s: None, max_llm_calls=2
        )
        with runner:
            r1 = runner.run(_task("素材A", "素材B"))
            r2 = runner.run(_task("素材C", "素材D"))
            r3 = runner.run(_task("素材E", "素材F"))
        assert r1["status"] == "same"  # 2 次打卡恰好用完预算
        assert r2["status"] == "not_run"
        assert r2["attempts"] == 0  # 首次打卡被预算闸拦下，judge_pair 未跑完
        assert r2["abstain"] == ABSTAIN_BUDGET
        assert r3["status"] == "not_run"
        assert runner.llm_calls == 2  # 硬上限被严格执行

    def test_budget_exhausted_mid_pair(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """判定中途耗尽预算：该对未完，记 not_run（不叫 grey/error）。"""
        router = _Router(
            {"素材A": [_card_raw()], "素材B": [_card_raw()], "素材C": [_card_raw()]}
        )
        monkeypatch.setattr(fingerprint_card, "complete_json", router)
        runner = BacktestRunner(
            _FakeConfig(), max_attempts=4, sleep=lambda _s: None, max_llm_calls=1
        )
        with runner:
            r1 = runner.run(_task("素材A", "素材B", gold="diff"))
        assert r1["status"] == "not_run"
        assert r1["attempts"] == 0  # 第 1 次打卡成功、第 2 次被闸，judge_pair 未完
        assert r1["abstain"] == ABSTAIN_BUDGET
        assert runner.llm_calls == 1

    def test_no_budget_keeps_legacy_behavior(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        router = _Router({"素材A": [_card_raw()], "素材B": [_card_raw()]})
        monkeypatch.setattr(fingerprint_card, "complete_json", router)
        runner = BacktestRunner(_FakeConfig(), sleep=lambda _s: None)
        with runner:
            result = runner.run(_task("素材A", "素材B"))
        assert result["status"] == "same"
        assert runner.llm_calls == 2


class TestRecheckStatsNotConfused:
    """复核统计三态分离：failed 单列，推翻率分母不含失败。"""

    def test_mixed_recheck_outcomes(
        self, monkeypatch: pytest.MonkeyPatch, runner: BacktestRunner
    ) -> None:
        # 三对判异：复核确认 / 复核推翻 / 复核失败后重试再确认
        monkeypatch.setattr(
            fingerprint_card,
            "complete_json",
            _Router(
                {
                    "素材A": [_card_raw()],
                    "素材B": [_card_raw(skeleton="slg-pull-pin")],
                    "素材C": [_card_raw()],
                    "素材D": [_card_raw(skeleton="slg-pull-pin")],
                    "素材E": [_card_raw()],
                    "素材F": [_card_raw(skeleton="slg-pull-pin")],
                },
                recheck=[
                    {"split_confirmed": True, "reason": "成立"},
                    {"split_confirmed": False, "reason": "不成立"},
                    None,  # 第 3 对首次复核调用失败（基础设施）
                    {"split_confirmed": True, "reason": "成立"},
                ],
            ),
        )
        with runner:
            r1 = runner.run(_task("素材A", "素材B", gold="diff"))
            r2 = runner.run(_task("素材C", "素材D", gold="diff"))
            r3 = runner.run(_task("素材E", "素材F", gold="diff"))
        assert (r1["status"], r2["status"], r3["status"]) == ("diff", "grey", "diff")
        assert r2["abstain"] == ABSTAIN_RECHECK_OVERTURNED
        summary = summarize_recheck(runner.recheck_stats)
        assert summary["confirmed"] == 2
        assert summary["overturned"] == 1
        assert summary["failed"] == 1  # 失败单列
        assert summary["total"] == 4
        assert summary["decided"] == 3  # 分母不含 failed
        assert summary["overturn_rate"] == pytest.approx(1 / 3)
        # 禁止把失败叫推翻：(overturned + failed) / total = 2/4 = 0.5 是旧口径错误
        assert summary["overturn_rate"] != pytest.approx(0.5)


class TestClassifyNone:
    """classify_none 纯函数：归因只依赖卡缓存状态 + 本次复核记录。"""

    def test_card_none_is_infra(self) -> None:
        kind, reason = classify_none({}, "ka", "kb", [])
        assert (kind, reason) == ("infra", FAIL_CARD)

    def test_valid_cards_with_failed_recheck_is_infra(self) -> None:
        cards = {
            "ka": fingerprint_card.parse_card(_card_raw()),
            "kb": fingerprint_card.parse_card(_card_raw(skeleton="slg-pull-pin")),
        }
        kind, reason = classify_none(cards, "ka", "kb", [None])
        assert (kind, reason) == ("infra", FAIL_RECHECK)

    def test_valid_cards_no_outcomes_no_evidence(self) -> None:
        cards = {
            "ka": fingerprint_card.parse_card(_card_raw()),
            "kb": fingerprint_card.parse_card(_card_raw()),
        }
        kind, reason = classify_none(cards, "ka", "kb", [])
        assert (kind, reason) == ("grey", ABSTAIN_NO_EVIDENCE)
