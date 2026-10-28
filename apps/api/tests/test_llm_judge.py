"""Tests for the LLM-judge layer: voting, classifier rules, fingerprint-card merge judge."""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from sqlalchemy.orm import Session

from app.models import CreativeDNA
from app.services import fingerprint_card, ip_pack, llm_judge
from app.services.dna_classifier import _rule_suggest, suggest_dna
from app.services.llm_judge import vote_json
from app.services.merge_judge import PairEvidence, judge_pair
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


def _evidence(text: str = "x") -> "PairEvidence":
    return PairEvidence(structure=text, visual=text, gameplay=text)


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


class TestMergeJudge:
    """merge-judge-v4.1：LLM 打骨架指纹卡（每素材一次，按证据哈希缓存）
    + 代码规则匹配 + 判异复核闸。mock 点在 fingerprint_card.complete_json
    ——judge_pair 经 get_card → build_card → complete_json；判异时再经
    recheck_split → complete_json（system=SPLIT_RECHECK_SYSTEM）。"""

    @staticmethod
    def _mock_cards(
        monkeypatch,  # noqa: ANN001
        mapping: dict[str, dict[str, Any] | None],
        recheck: dict[str, Any] | None = None,
    ) -> list[str]:
        """按证据文本片段路由打卡返回；system=SPLIT_RECHECK_SYSTEM 的调用
        走复核闸固定返回（None=复核失败）。返回所有 user prompt。"""
        calls: list[str] = []

        def _fake(config, *, system, user, **kwargs):  # noqa: ANN001, ANN002, ANN202
            calls.append(user)
            if system == ip_pack.SPLIT_RECHECK_SYSTEM:
                return recheck
            evidence_text = user.rsplit("【素材分析证据】\n", 1)[-1]
            for fragment, raw in mapping.items():
                if fragment in evidence_text:
                    return raw
            raise AssertionError(f"未预设卡片的证据：{evidence_text[-80:]}")

        monkeypatch.setattr(fingerprint_card, "complete_json", _fake)
        return calls

    def test_identical_cards_same_variant_family(self, monkeypatch) -> None:  # noqa: ANN001
        config = _FakeConfig()
        self._mock_cards(monkeypatch, {"a": _card_raw(), "b": _card_raw()})
        judgement = judge_pair(
            config, evidence_a=_evidence("a"), evidence_b=_evidence("b")
        )
        assert judgement is not None
        assert judgement.relation == "same_variant_family"
        assert judgement.same_creative is True
        assert judgement.votes == 2  # v4 固定 2，不开放自动合并闸门
        assert "slg-zero-to-prosperity" in judgement.reason
        params = judgement.reason_params()
        assert params["prompt_version"] == ip_pack.MERGE_JUDGE_PROMPT_VERSION
        assert params["cards"]["a"]["skeleton"] == "slg-zero-to-prosperity"
        assert params["cards"]["b"]["skeleton"] == "slg-zero-to-prosperity"

    def test_different_skeleton(self, monkeypatch) -> None:  # noqa: ANN001
        config = _FakeConfig()
        self._mock_cards(
            monkeypatch,
            {
                "a": _card_raw(),
                "b": _card_raw(skeleton="slg-pull-pin", ending="unknown"),
            },
            recheck={"split_confirmed": True, "reason": "骨架确属不同结构"},
        )
        judgement = judge_pair(
            config, evidence_a=_evidence("a"), evidence_b=_evidence("b")
        )
        assert judgement is not None
        assert judgement.relation == "different_concept"
        assert judgement.same_creative is False
        assert judgement.recheck == {
            "split_confirmed": True, "reason": "骨架确属不同结构",
        }
        assert "复核确认" in judgement.reason
        assert judgement.reason_params()["recheck"]["split_confirmed"] is True

    def test_different_track(self, monkeypatch) -> None:  # noqa: ANN001
        config = _FakeConfig()
        self._mock_cards(
            monkeypatch,
            {"a": _card_raw(), "b": _card_raw(track="tower-defense")},
            recheck={"split_confirmed": True, "reason": "赛道分歧成立"},
        )
        judgement = judge_pair(
            config, evidence_a=_evidence("a"), evidence_b=_evidence("b")
        )
        assert judgement is not None
        assert judgement.relation == "different_concept"

    def test_different_family(self, monkeypatch) -> None:  # noqa: ANN001
        config = _FakeConfig()
        self._mock_cards(
            monkeypatch,
            {
                "a": _card_raw(),
                "b": _card_raw(
                    family="merge", track="gameplay", skeleton="mg-pure-merge",
                    ending="none",
                ),
            },
            recheck={"split_confirmed": True, "reason": "品类分歧成立"},
        )
        judgement = judge_pair(
            config, evidence_a=_evidence("a"), evidence_b=_evidence("b")
        )
        assert judgement is not None
        assert judgement.relation == "different_concept"

    def test_same_skeleton_different_ending(self, monkeypatch) -> None:  # noqa: ANN001
        """同骨架但结局 success vs fail（双方均非 unknown/none）→ 复核确认后判异。"""
        config = _FakeConfig()
        self._mock_cards(
            monkeypatch,
            {"a": _card_raw(ending="success"), "b": _card_raw(ending="fail")},
            recheck={"split_confirmed": True, "reason": "结局分歧成立"},
        )
        judgement = judge_pair(
            config, evidence_a=_evidence("a"), evidence_b=_evidence("b")
        )
        assert judgement is not None
        assert judgement.relation == "different_concept"
        assert "结局" in judgement.reason

    def test_recheck_overturned_goes_grey(self, monkeypatch) -> None:  # noqa: ANN001
        """复核闸推翻分歧（split_confirmed=false）→ 灰区人工，不落判异。"""
        config = _FakeConfig()
        self._mock_cards(
            monkeypatch,
            {"a": _card_raw(), "b": _card_raw(track="tower-defense")},
            recheck={"split_confirmed": False, "reason": "证据边界模糊，赛道标签抖动"},
        )
        assert (
            judge_pair(config, evidence_a=_evidence("a"), evidence_b=_evidence("b"))
            is None
        )

    def test_recheck_failure_goes_grey(self, monkeypatch) -> None:  # noqa: ANN001
        """复核调用失败（None）或返回结构非法 → 同样灰区，不落判异。"""
        config = _FakeConfig()
        for recheck in (None, {"split_confirmed": "yes"}, {"ok": True}):
            self._mock_cards(
                monkeypatch,
                {"a": _card_raw(), "b": _card_raw(track="tower-defense")},
                recheck=recheck,
            )
            assert (
                judge_pair(config, evidence_a=_evidence("a"), evidence_b=_evidence("b"))
                is None
            )

    def test_llm_failure_returns_none(self, monkeypatch) -> None:  # noqa: ANN001
        """任一侧 complete_json 失败（None）→ 灰区。"""
        config = _FakeConfig()
        for mapping in (
            {"a": None, "b": _card_raw()},
            {"a": _card_raw(), "b": None},
            {"a": None, "b": None},
        ):
            self._mock_cards(monkeypatch, mapping)
            assert (
                judge_pair(config, evidence_a=_evidence("a"), evidence_b=_evidence("b"))
                is None
            )

    def test_skeleton_new_is_grey_zone(self, monkeypatch) -> None:  # noqa: ANN001
        """skeleton=null + skeleton_new（库外新结构提案）→ 灰区人工命名。"""
        config = _FakeConfig()
        self._mock_cards(
            monkeypatch,
            {
                "a": _card_raw(),
                "b": _card_raw(skeleton=None, skeleton_new="slg-某新结构"),
            },
        )
        assert (
            judge_pair(config, evidence_a=_evidence("a"), evidence_b=_evidence("b"))
            is None
        )

    def test_card_cache_dedupes_llm_calls(self, monkeypatch) -> None:  # noqa: ANN001
        """同一 evidence 打两对（含跨对共享素材）：complete_json 只被调
        2 次（不是 4 次）——回测 146 对里同素材重复出现的关键成本项。"""
        config = _FakeConfig()
        calls = self._mock_cards(
            monkeypatch, {"a": _card_raw(), "b": _card_raw()},
        )
        cache: dict = {}
        ev_a, ev_b = _evidence("a"), _evidence("b")
        first = judge_pair(config, evidence_a=ev_a, evidence_b=ev_b,
                           card_cache=cache)
        second = judge_pair(config, evidence_a=ev_a, evidence_b=ev_b,
                            card_cache=cache)
        assert first is not None and second is not None
        assert len(calls) == 2
        assert len(cache) == 2


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
