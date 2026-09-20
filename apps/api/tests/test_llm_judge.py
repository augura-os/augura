"""Tests for the LLM-judge layer: voting, classifier rules, pairwise consistency."""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy.orm import Session

from app.models import CreativeDNA
from app.services import ip_pack, llm_judge
from app.services.dna_classifier import _rule_suggest, suggest_dna
from app.services.llm_judge import vote_json
from app.services.merge_judge import judge_pair
from app.services.settings import AIConfig

# 规则层关键词表（钩子原型/机制）属行业方法论，只在私有库 ip_pack 真版中；
# OSS 占位版为空表，依赖关键词命中的用例在公开仓库跳过
requires_ip_pack = pytest.mark.skipif(
    not ip_pack.HOOK_PROTOTYPE_KEYWORDS,
    reason="OSS 占位 ip_pack 无规则关键词表",
)


@pytest.fixture(autouse=True)
def _clear_temperature_memory() -> None:
    """端点温度兼容记忆是进程级状态，用例间必须隔离。"""
    llm_judge._TEMPERATURE_REJECTED.clear()


class _FakeConfig(AIConfig):
    def __init__(self) -> None:
        super().__init__(api_key="", base_url="", vision_model="", embedding_model="")


def _dna(db: Session, code: str, name: str, hook: str, mech: str) -> CreativeDNA:
    dna = CreativeDNA(
        id=str(uuid.uuid4()), code=code, name=name,
        hook_prototype=hook, core_mechanic=mech, narrative_structure="",
    )
    db.add(dna)
    db.flush()
    return dna


class TestRuleLayer:
    @requires_ip_pack
    def test_fail_retry_maps_to_d01(self, db_session: Session) -> None:
        dna = _dna(db_session, "D01", "失败重开×岛屿生存", "H11 失败重开", "采集+建造")
        suggestion = _rule_suggest("角色反复死亡失败重开", "沙漠挖掘建造", db_session)
        assert suggestion is not None and suggestion.dna.id == dna.id
        assert suggestion.votes == 2  # 规则层建议级

    @requires_ip_pack
    def test_no_ads_maps_to_d02(self, db_session: Session) -> None:
        dna = _dna(db_session, "D02", "无广告宣言×岛屿采集", "H08 无广告宣言", "采集+建造")
        suggestion = _rule_suggest("这游戏没广告", "采集建造", db_session)
        assert suggestion is not None and suggestion.dna.id == dna.id

    def test_unknown_hook_returns_none(self, db_session: Session) -> None:
        _dna(db_session, "D01", "失败重开×岛屿生存", "H11 失败重开", "采集+建造")
        assert _rule_suggest("量子物理讲座", "抽象思维", db_session) is None

    @requires_ip_pack
    def test_suggest_dna_without_api_key_falls_back_to_rule(
        self, db_session: Session
    ) -> None:
        from app.models import Creative

        dna = _dna(db_session, "D01", "失败重开×岛屿生存", "H11 失败重开", "采集+建造")
        creative = Creative(id=str(uuid.uuid4()), name="x")
        suggestion = suggest_dna(creative, "反复死亡重开", "建造", db_session, _FakeConfig())
        assert suggestion is not None and suggestion.dna.id == dna.id


class TestVoteJson:
    def test_majority_win(self, monkeypatch) -> None:  # noqa: ANN001
        config = _FakeConfig()
        answers = iter([{"dna_code": "D01"}, {"dna_code": "D01"}, {"dna_code": "D02"}])
        monkeypatch.setattr(
            "app.services.llm_judge.complete_json", lambda *a, **k: next(answers)
        )
        winner, count, _ = vote_json(config, system="", user="", key="dna_code")
        assert winner == "D01" and count == 2

    def test_no_majority_returns_none(self, monkeypatch) -> None:  # noqa: ANN001
        config = _FakeConfig()
        answers = iter([{"dna_code": "D01"}, {"dna_code": "D02"}, {"dna_code": "D03"}])
        monkeypatch.setattr(
            "app.services.llm_judge.complete_json", lambda *a, **k: next(answers)
        )
        winner, count, _ = vote_json(config, system="", user="", key="dna_code")
        assert winner == "D01" or winner is not None
        assert count == 1  # 全部不同 → 最高票只有 1（调用方按 <2 丢弃）

    def test_all_failures_returns_none(self, monkeypatch) -> None:  # noqa: ANN001
        config = _FakeConfig()
        monkeypatch.setattr(
            "app.services.llm_judge.complete_json", lambda *a, **k: None
        )
        winner, count, _ = vote_json(config, system="", user="", key="dna_code")
        assert winner is None and count == 0


class TestMergeJudge:
    def test_unanimous_merge(self, monkeypatch) -> None:  # noqa: ANN001
        config = _FakeConfig()
        monkeypatch.setattr(
            "app.services.merge_judge.complete_json",
            lambda *a, **k: {"same_creative": True, "reason": "Q4 仅语言差异"},
        )
        judgement = judge_pair(config, analysis_a="a", analysis_b="b")
        assert judgement is not None
        assert judgement.same_creative is True
        assert judgement.votes == 3

    def test_split_vote_returns_none(self, monkeypatch) -> None:  # noqa: ANN001
        config = _FakeConfig()
        answers = iter([
            {"same_creative": True, "reason": "x"},
            {"same_creative": False, "reason": "y"},
            {"same_creative": True, "reason": "x"},
        ])
        monkeypatch.setattr(
            "app.services.merge_judge.complete_json", lambda *a, **k: next(answers)
        )
        assert judge_pair(config, analysis_a="a", analysis_b="b") is None

    def test_all_fail_returns_none(self, monkeypatch) -> None:  # noqa: ANN001
        config = _FakeConfig()
        monkeypatch.setattr(
            "app.services.merge_judge.complete_json", lambda *a, **k: None
        )
        assert judge_pair(config, analysis_a="a", analysis_b="b") is None


class TestTemperature:
    """complete_json 必须显式透传 temperature（默认 config.judge_temperature）——
    不传时服务商默认可能是 0，3/3 自洽投票退化为同一票。"""

    def _patch_openai(self, monkeypatch) -> "_CaptureCompletions":  # noqa: ANN001
        completions = _CaptureCompletions()
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        monkeypatch.setattr(llm_judge, "OpenAI", lambda **_kw: client)
        return completions

    def test_default_from_config(self, monkeypatch) -> None:  # noqa: ANN001
        completions = self._patch_openai(monkeypatch)
        config = AIConfig(
            api_key="k", base_url="", vision_model="m", embedding_model=""
        )
        result = llm_judge.complete_json(config, system="", user="")
        assert result == {"ok": True}
        assert completions.kwargs["temperature"] == 0.7

    def test_config_value_used(self, monkeypatch) -> None:  # noqa: ANN001
        completions = self._patch_openai(monkeypatch)
        config = AIConfig(
            api_key="k", base_url="", vision_model="m", embedding_model="",
            judge_temperature=0.9,
        )
        llm_judge.complete_json(config, system="", user="")
        assert completions.kwargs["temperature"] == 0.9

    def test_explicit_override(self, monkeypatch) -> None:  # noqa: ANN001
        completions = self._patch_openai(monkeypatch)
        config = AIConfig(
            api_key="k", base_url="", vision_model="m", embedding_model=""
        )
        llm_judge.complete_json(config, system="", user="", temperature=0.2)
        assert completions.kwargs["temperature"] == 0.2


class _CaptureCompletions:
    def __init__(self) -> None:
        self.kwargs: dict = {}

    def create(self, **kwargs):  # noqa: ANN002, ANN003, ANN201
        self.kwargs = kwargs
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))]
        )


class TestTemperatureFallback:
    """端点拒绝显式 temperature（如 Kimi k3 仅允许 1）时：不带温度重试一次。"""

    @staticmethod
    def _bad_request(message: str) -> llm_judge.BadRequestError:
        return llm_judge.BadRequestError(
            message,
            response=httpx.Response(400, request=httpx.Request("POST", "http://x")),
            body=None,
        )

    def _patch_openai(self, monkeypatch, failures: list[Exception]) -> list[dict]:  # noqa: ANN001
        calls: list[dict] = []

        class Completions:
            def create(self, **kwargs):  # noqa: ANN002, ANN003, ANN201
                calls.append(kwargs)
                if failures:
                    raise failures.pop(0)
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))
                    ]
                )

        client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
        monkeypatch.setattr(llm_judge, "OpenAI", lambda **_kw: client)
        return calls

    def test_temperature_rejected_retries_without(self, monkeypatch) -> None:  # noqa: ANN001
        calls = self._patch_openai(
            monkeypatch,
            [self._bad_request("invalid temperature: only 1 is allowed for this model")],
        )
        config = AIConfig(
            api_key="k", base_url="", vision_model="m", embedding_model=""
        )
        result = llm_judge.complete_json(config, system="", user="")
        assert result == {"ok": True}
        assert len(calls) == 2
        assert "temperature" in calls[0]
        assert "temperature" not in calls[1]

    def test_non_temperature_400_no_retry(self, monkeypatch) -> None:  # noqa: ANN001
        calls = self._patch_openai(
            monkeypatch, [self._bad_request("context length exceeded")]
        )
        config = AIConfig(
            api_key="k", base_url="", vision_model="m", embedding_model=""
        )
        assert llm_judge.complete_json(config, system="", user="") is None
        assert len(calls) == 1

    def test_retry_also_fails_returns_none(self, monkeypatch) -> None:  # noqa: ANN001
        self._patch_openai(
            monkeypatch,
            [
                self._bad_request("invalid temperature: only 1 is allowed"),
                self._bad_request("invalid temperature: still nope"),
            ],
        )
        config = AIConfig(
            api_key="k", base_url="", vision_model="m", embedding_model=""
        )
        assert llm_judge.complete_json(config, system="", user="") is None

    def test_rejection_remembered_skips_temperature_next_time(
        self, monkeypatch
    ) -> None:
        """第一次 400 拒绝后记住 (base_url, model)：后续调用直接不带
        temperature，不再白付 400。"""
        calls = self._patch_openai(
            monkeypatch,
            [self._bad_request("invalid temperature: only 1 is allowed")],
        )
        config = AIConfig(
            api_key="k", base_url="https://api.kimi.com/coding/v1",
            vision_model="k3-256k", embedding_model="",
        )
        assert llm_judge.complete_json(config, system="", user="") == {"ok": True}
        assert len(calls) == 2  # 400 + 不带温度重试
        assert ("https://api.kimi.com/coding/v1", "k3-256k") in (
            llm_judge._TEMPERATURE_REJECTED
        )
        # 第二次调用：只发 1 次请求，且根本不带 temperature
        assert llm_judge.complete_json(config, system="", user="") == {"ok": True}
        assert len(calls) == 3
        assert "temperature" not in calls[2]

    def test_model_switch_reprobes(self, monkeypatch) -> None:  # noqa: ANN001
        """真实用户会切模型：记忆按 (base_url, model) 做 key，换新模型后
        重新探测（第一次仍带 temperature）。"""
        calls = self._patch_openai(
            monkeypatch,
            [self._bad_request("invalid temperature: only 1 is allowed")],
        )
        config = AIConfig(
            api_key="k", base_url="https://api.kimi.com/coding/v1",
            vision_model="k3-256k", embedding_model="",
        )
        llm_judge.complete_json(config, system="", user="")
        switched = AIConfig(
            api_key="k", base_url="https://api.kimi.com/coding/v1",
            vision_model="k2.5", embedding_model="",
        )
        assert llm_judge.complete_json(switched, system="", user="") == {"ok": True}
        assert len(calls) == 3
        assert calls[2]["model"] == "k2.5"
        assert "temperature" in calls[2]  # 新 key 重新探测，仍带温度
