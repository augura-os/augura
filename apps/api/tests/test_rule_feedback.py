"""规则层回流（services/rule_feedback + dnas 路由 + dna_classifier 运行时词表）。"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routes.dnas import (
    RuleKeywordPayload,
    confirm_rule_keyword,
    dismiss_rule_keyword,
)
from app.exceptions import ApiError
from app.models import (
    AnalysisResult,
    Creative,
    CreativeAsset,
    CreativeDNA,
    CreativeVariant,
    EditLog,
    JudgeSuggestion,
)
from app.repositories.settings import SettingsRepository
from app.services import dna_classifier, markets, rule_feedback
from app.services import review as review_service
from app.services.judge_suggestions import upsert_suggestion

T0 = datetime(2026, 8, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _reset_keyword_cache():
    """词表缓存是进程级状态：每个用例前后都强制过期，防串扰。"""
    dna_classifier._tables.expires_at = 0.0
    yield
    dna_classifier._tables.expires_at = 0.0


def _dna(db: Session, code: str, name: str, mech: str = "") -> CreativeDNA:
    dna = CreativeDNA(
        id=str(uuid.uuid4()), code=code, name=name,
        hook_prototype="", core_mechanic=mech, narrative_structure="",
    )
    db.add(dna)
    db.flush()
    return dna


def _creative(
    db: Session, text: str, *, dna: CreativeDNA | None = None
) -> Creative:
    """带分析文本的 creative（hook/gameplay 都是 text）；可指定归族。"""
    creative = Creative(
        id=str(uuid.uuid4()), name=f"c-{text}",
        dna_id=dna.id if dna else None,
    )
    asset = CreativeAsset(
        id=str(uuid.uuid4()), filename=f"{text}.mp4", file_type="video",
        storage_key=f"test/{uuid.uuid4()}",
    )
    db.add_all([creative, asset])
    db.flush()
    db.add(
        CreativeVariant(
            id=str(uuid.uuid4()), creative_id=creative.id,
            asset_id=asset.id, name="v1",
        )
    )
    db.add(
        AnalysisResult(
            id=str(uuid.uuid4()), asset_id=asset.id, hook=text, gameplay=text,
        )
    )
    db.flush()
    return creative


def _log(
    db: Session,
    *,
    creative_id: str,
    new_value: str,
    at: datetime = T0,
) -> None:
    db.add(
        EditLog(
            id=str(uuid.uuid4()), entity_type="creative", entity_id=creative_id,
            action="update", field="dna_id", old_value="",
            new_value=new_value, created_at=at,
        )
    )
    db.flush()


def _correction(db: Session, creative: Creative, auto: str, human: str) -> None:
    _log(db, creative_id=creative.id, new_value=f"auto: {auto}（judge）", at=T0)
    _log(db, creative_id=creative.id, new_value=human,
         at=T0 + timedelta(hours=1))


def _suggestions(db: Session) -> list[JudgeSuggestion]:
    return list(
        db.scalars(
            select(JudgeSuggestion).where(JudgeSuggestion.kind == rule_feedback.KIND)
        ).all()
    )


class TestCorrectionSamples:
    """auto 归族 → 人工改判的配对挖掘。"""

    def test_pairing(self, db_session: Session) -> None:
        c1 = _creative(db_session, "t1")
        c2 = _creative(db_session, "t2")
        _correction(db_session, c1, "D3 自动族", "D7 人工族")
        samples = rule_feedback.correction_samples(db_session)
        assert [(s.creative_id, s.auto_label, s.human_label) for s in samples] == [
            (c1.id, "D3 自动族", "D7 人工族")
        ]
        assert not any(s.creative_id == c2.id for s in samples)

    def test_no_pair_without_human_followup(self, db_session: Session) -> None:
        creative = _creative(db_session, "t1")
        _log(db_session, creative_id=creative.id,
             new_value="auto: D3 自动族（judge）")
        assert rule_feedback.correction_samples(db_session) == []

    def test_same_label_is_confirmation_not_correction(
        self, db_session: Session
    ) -> None:
        creative = _creative(db_session, "t1")
        _correction(db_session, creative, "D3 同族", "D3 同族")
        assert rule_feedback.correction_samples(db_session) == []

    def test_human_without_preceding_auto_ignored(self, db_session: Session) -> None:
        creative = _creative(db_session, "t1")
        _log(db_session, creative_id=creative.id, new_value="D7 人工族")
        assert rule_feedback.correction_samples(db_session) == []


class TestScoreAndSuggest:
    """判别力分档、阈值过滤、建议 upsert 幂等。"""

    def _seed_library(self, db: Session) -> None:
        """D7×5 条 "alpha shared"（全部带改判样本），D3×4 条 "beta shared"。

        同族对 = C(5,2)+C(4,2) = 16；跨族对 = C(9,2)−16 = 20。
        alpha：P(同)=10/16=0.625、P(跨)=0 → 机制词；shared：两边全中
        → score 0 → 通用词；beta 不在修正样本词池里 → 不出建议。
        """
        d7 = _dna(db, "D7", "人工族")
        d3 = _dna(db, "D3", "自动族")
        for _ in range(5):
            creative = _creative(db, "alpha shared", dna=d7)
            _correction(db, creative, "D3 自动族", "D7 人工族")
        for _ in range(4):
            _creative(db, "beta shared", dna=d3)

    def test_score_bands(self, db_session: Session) -> None:
        self._seed_library(db_session)
        assert rule_feedback.suggest_keywords(db_session) == 2
        by_word = {
            row.left_id: json.loads(row.reason)
            for row in _suggestions(db_session)
        }
        mechanic = by_word["mechanic:alpha"]
        assert mechanic["target"] == "mechanic"
        assert mechanic["score"] == pytest.approx(0.625)
        assert mechanic["evidence"]["same_pair_rate"] == pytest.approx(0.625)
        assert mechanic["evidence"]["cross_pair_rate"] == 0.0
        assert mechanic["evidence"]["support"] == 5
        assert mechanic["learned_at"]
        generic = by_word["generic:shared"]
        assert generic["score"] == 0.0

    def test_below_min_corrections_skips(self, db_session: Session) -> None:
        d7 = _dna(db_session, "D7", "人工族")
        for _ in range(rule_feedback.MIN_CORRECTION_SAMPLES - 1):
            creative = _creative(db_session, "alpha shared", dna=d7)
            _correction(db_session, creative, "D3 自动族", "D7 人工族")
        assert rule_feedback.suggest_keywords(db_session) == 0
        assert _suggestions(db_session) == []

    def test_below_min_support_filtered(self, db_session: Session) -> None:
        # 修正样本够 5 条，但 rareword 不在任何已归族素材里（支撑 0 < 3）
        d1 = _dna(db_session, "D1", "族一")
        d2 = _dna(db_session, "D2", "族二")
        for _ in range(2):
            _creative(db_session, "x one", dna=d1)
            _creative(db_session, "y two", dna=d2)
        for _ in range(rule_feedback.MIN_CORRECTION_SAMPLES):
            creative = _creative(db_session, "rareword")  # 未归族
            _correction(db_session, creative, "D1 族一", "D2 族二")
        assert rule_feedback.suggest_keywords(db_session) == 0
        assert _suggestions(db_session) == []

    def test_upsert_idempotent(self, db_session: Session) -> None:
        self._seed_library(db_session)
        rule_feedback.suggest_keywords(db_session)
        again = rule_feedback.suggest_keywords(db_session)
        assert again == 2
        rows = _suggestions(db_session)
        assert len(rows) == 2  # 同一 target+word 只更新同一条
        assert {row.left_id for row in rows} == {"mechanic:alpha", "generic:shared"}

    def test_review_items_parse_payload(self, db_session: Session) -> None:
        self._seed_library(db_session)
        rule_feedback.suggest_keywords(db_session)
        items = review_service.rule_keyword_items(db_session)
        assert len(items) == 2
        by_word = {item.rule_keyword.word: item for item in items if item.rule_keyword}
        alpha = by_word["alpha"]
        assert alpha.kind == "rule_keyword"
        assert alpha.rule_keyword.target == "mechanic"
        assert alpha.rule_keyword.support == 5
        assert "判别力" in alpha.reason


class TestConfirmDismiss:
    """确认落 settings + 留痕 + 删建议；跳过只删建议。"""

    def _seed_suggestion(
        self, db: Session, *, target: str, word: str
    ) -> JudgeSuggestion:
        payload = {
            "target": target, "word": word, "score": 0.5,
            "evidence": {"same_pair_rate": 0.5, "cross_pair_rate": 0.0,
                         "support": 5},
            "learned_at": "2026-08-01T00:00:00+00:00",
        }
        upsert_suggestion(
            db, kind=rule_feedback.KIND, left_id=f"{target}:{word}",
            right_id=None, verdict=word, votes=5,
            reason=json.dumps(payload, ensure_ascii=False),
        )
        return db.scalar(
            select(JudgeSuggestion).where(
                JudgeSuggestion.kind == rule_feedback.KIND,
                JudgeSuggestion.left_id == f"{target}:{word}",
            )
        )

    def test_confirm_mechanic_writes_settings(self, db_session: Session) -> None:
        suggestion = self._seed_suggestion(
            db_session, target="mechanic", word="吸水"
        )
        result = confirm_rule_keyword(
            RuleKeywordPayload(suggestion_id=suggestion.id), db_session
        )
        assert result.success is True
        raw = SettingsRepository(db_session).get("rule_keywords:mechanic")
        assert json.loads(raw) == ["吸水"]
        # 留痕：edit_logs（不带 auto: 前缀——这是人工确认动作）
        log = db_session.scalars(
            select(EditLog).where(EditLog.action == "rule_keyword")
        ).one()
        assert log.entity_type == "judge"
        assert log.field == "mechanic"
        assert not log.new_value.startswith("auto:")
        assert "吸水" in log.new_value and "learned_at" in log.new_value
        # 建议已删
        assert db_session.get(JudgeSuggestion, suggestion.id) is None

    def test_confirm_generic_uses_csv_overlay(self, db_session: Session) -> None:
        suggestion = self._seed_suggestion(
            db_session, target="generic", word="clone"
        )
        confirm_rule_keyword(
            RuleKeywordPayload(suggestion_id=suggestion.id), db_session
        )
        # similarity_generic_tokens 沿用逗号分隔格式（不为它改格式）
        raw = SettingsRepository(db_session).get(markets.GENERIC_TOKENS_SETTING)
        assert raw == "clone"
        assert "clone" in markets.resolve_generic_tokens(db_session)

    def test_dismiss_only_deletes(self, db_session: Session) -> None:
        suggestion = self._seed_suggestion(
            db_session, target="hook", word="fail-retry"
        )
        result = dismiss_rule_keyword(
            RuleKeywordPayload(suggestion_id=suggestion.id), db_session
        )
        assert result.success is True
        assert db_session.get(JudgeSuggestion, suggestion.id) is None
        assert SettingsRepository(db_session).get("rule_keywords:hook") is None

    def test_confirm_unknown_suggestion_404(self, db_session: Session) -> None:
        with pytest.raises(ApiError):
            confirm_rule_keyword(
                RuleKeywordPayload(suggestion_id=str(uuid.uuid4())), db_session
            )


class TestRuntimeKeywordTables:
    """dna_classifier 运行时读词表：settings 优先、缓存 60s、过期重读。"""

    def test_settings_words_take_effect(self, db_session: Session) -> None:
        _dna(db_session, "D7", "吸水族", mech="吸水机制")
        SettingsRepository(db_session).set(
            "rule_keywords:mechanic", json.dumps(["吸水"], ensure_ascii=False)
        )
        suggestion = dna_classifier._rule_suggest("吸水怪来袭", "", db_session)
        assert suggestion is not None
        assert suggestion.dna.code == "D7"
        assert "吸水" in suggestion.reason

    def test_cache_hit_within_ttl(self, db_session: Session) -> None:
        _dna(db_session, "D7", "吸水族", mech="吸水机制")
        repo = SettingsRepository(db_session)
        repo.set("rule_keywords:mechanic", json.dumps(["吸水"], ensure_ascii=False))
        assert dna_classifier._rule_suggest("吸水怪来袭", "", db_session) is not None
        # 60s 内改 settings 不生效（缓存命中，仍用旧词表）
        repo.set("rule_keywords:mechanic", json.dumps(["喷火"], ensure_ascii=False))
        assert dna_classifier._rule_suggest("吸水怪来袭", "", db_session) is not None
        assert dna_classifier._rule_suggest("喷火龙来袭", "", db_session) is None

    def test_expired_cache_rereads(self, db_session: Session) -> None:
        # core_mechanic 必须同时含两个词：mech_hit 命中词表后还要
        # 落在某个族的 core_mechanic 里才出建议（机制定族的匹配语义）
        _dna(db_session, "D7", "吸水喷火族", mech="吸水喷火机制")
        repo = SettingsRepository(db_session)
        repo.set("rule_keywords:mechanic", json.dumps(["吸水"], ensure_ascii=False))
        assert dna_classifier._rule_suggest("吸水怪来袭", "", db_session) is not None
        repo.set("rule_keywords:mechanic", json.dumps(["喷火"], ensure_ascii=False))
        dna_classifier._tables.expires_at = 0.0  # 模拟缓存过期
        assert dna_classifier._rule_suggest("喷火龙来袭", "", db_session) is not None
        # settings 是替换不是合并：旧词失效
        assert dna_classifier._rule_suggest("吸水怪来袭", "", db_session) is None

    def test_empty_settings_falls_back(self, db_session: Session) -> None:
        sentinel = [("fallback-word", "fallback")]
        resolved = dna_classifier._resolve_keywords(db_session, "mechanic", sentinel)
        assert resolved is sentinel
        # 非法 JSON 同样回落
        SettingsRepository(db_session).set("rule_keywords:mechanic", "not-json")
        assert dna_classifier._resolve_keywords(
            db_session, "mechanic", sentinel
        ) is sentinel
