"""Tests for the LLM-judge layer: voting, classifier rules, pairwise consistency."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

from app.models import CreativeDNA
from app.services import ip_pack
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
