"""Tests for correction-driven threshold calibration（services/threshold_calibration）。

标注对全部用合成 creative 名构造（文本分确定性可算）；视觉分不参与
校准（下载视频太贵，见模块 docstring）。
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import EditLog, JudgeSuggestion, SplitRuling
from app.services import threshold_calibration
from app.services.threshold_calibration import (
    CALIBRATION_KIND,
    extract_labeled_pairs,
    suggest_threshold,
)


@pytest.fixture(autouse=True)
def _no_real_case_rulings(monkeypatch) -> None:
    """屏蔽仓库里真实的 docs/case-rulings.json——那是生产裁决数据，
    会污染测试构造的 F1 场景。"""
    monkeypatch.setattr(threshold_calibration, "_rulings", lambda: [])


def _merge_log(db: Session, source: str, target: str, *, auto: bool = False) -> None:
    db.add(
        EditLog(
            id=str(uuid.uuid4()), entity_type="creative",
            entity_id=str(uuid.uuid4()), action="merge", field="",
            old_value=f"{source} ({uuid.uuid4()})",
            new_value=f"{'auto: ' if auto else ''}{target} ({uuid.uuid4()})",
        )
    )
    db.flush()


def _split_ruling(db: Session, name_a: str, name_b: str) -> None:
    low, high = sorted((name_a, name_b))
    db.add(
        SplitRuling(
            id=str(uuid.uuid4()), name_a=low, name_b=high,
            reason="结案", source="inbox_close",
        )
    )
    db.flush()


class TestExtractLabeledPairs:
    def test_merge_and_split_sources(self, db_session: Session) -> None:
        _merge_log(db_session, "alpha-beta-gamma", "alpha-beta-x1", auto=True)
        _split_ruling(db_session, "omega-one", "omega-two")
        pairs = extract_labeled_pairs(db_session)
        assert len(pairs) == 2
        positive = next(p for p in pairs if p.should_merge)
        assert positive.source == "edit_log"
        assert positive.name_a == "alpha-beta-gamma"
        assert positive.name_b == "alpha-beta-x1"
        negative = next(p for p in pairs if not p.should_merge)
        assert negative.source == "split_ruling"

    def test_split_overrides_merge_on_conflict(self, db_session: Session) -> None:
        _merge_log(db_session, "pair-a", "pair-b")
        _split_ruling(db_session, "pair-a", "pair-b")
        pairs = extract_labeled_pairs(db_session)
        assert len(pairs) == 1
        assert pairs[0].should_merge is False  # 拆是更强的人工信号

    def test_text_score_computed(self, db_session: Session) -> None:
        _merge_log(db_session, "alpha-beta-gamma-delta", "alpha-beta-gamma-x1")
        pair = extract_labeled_pairs(db_session)[0]
        # tokens {alpha,beta,gamma,delta} vs {alpha,beta,gamma,x1} → 3/5
        assert pair.text_score == 0.6
        assert pair.analysis_score is None  # 双方 creative 都不存在（名字层面）


class TestSuggestThreshold:
    def test_suggests_when_f1_improves(self, db_session: Session) -> None:
        # 3 合（0.6 / 0.6 / 0.25——低分合对被当前 0.34 阈值漏判）
        # 3 拆（名字零交集 → 0.0）
        _merge_log(db_session, "alpha-beta-gamma-delta", "alpha-beta-gamma-x1")
        _merge_log(db_session, "omega-one-two-three", "omega-one-two-x9")
        _merge_log(db_session, "sigma-seven-eight-nine-ten", "sigma-seven-z1-z2-z3")
        _split_ruling(db_session, "alpha-qq", "omega-ww")
        _split_ruling(db_session, "beta-ee", "sigma-rr")
        _split_ruling(db_session, "gamma-tt", "delta-yy")
        suggestion = suggest_threshold(db_session)
        assert suggestion is not None
        # 阈值降到 0.25 → 全部 6 对判对（F1 0.8 → 1.0）
        assert suggestion.suggested == 0.25
        assert suggestion.f1_suggested == 1.0
        assert suggestion.f1_current < 1.0
        assert suggestion.sample_count == 6
        row = db_session.scalars(
            select(JudgeSuggestion).where(
                JudgeSuggestion.kind == CALIBRATION_KIND
            )
        ).one()
        assert row.verdict == "0.25"
        assert "6 条裁决" in row.reason and "3 合" in row.reason

    def test_no_suggestion_when_current_already_optimal(
        self, db_session: Session
    ) -> None:
        # 当前 0.34 已完美分开 → 不出建议
        _merge_log(db_session, "alpha-beta-gamma-delta", "alpha-beta-gamma-x1")
        _merge_log(db_session, "omega-one-two-three", "omega-one-two-x9")
        _split_ruling(db_session, "alpha-qq", "omega-ww")
        _split_ruling(db_session, "beta-ee", "sigma-rr")
        assert suggest_threshold(db_session) is None
        assert db_session.scalars(
            select(JudgeSuggestion).where(
                JudgeSuggestion.kind == CALIBRATION_KIND
            )
        ).all() == []

    def test_too_few_samples_no_suggestion(self, db_session: Session) -> None:
        _merge_log(db_session, "alpha-beta-gamma-delta", "alpha-beta-gamma-x1")
        _split_ruling(db_session, "alpha-qq", "omega-ww")
        assert suggest_threshold(db_session) is None

    def test_stale_suggestion_cleaned(self, db_session: Session) -> None:
        from app.services.judge_suggestions import upsert_suggestion

        upsert_suggestion(
            db_session, kind=CALIBRATION_KIND, left_id="merge-text-threshold",
            right_id=None, verdict="0.30", votes=0, reason="旧建议",
        )
        db_session.flush()
        # 样本不足 → 不出新建议，同时清掉旧的
        assert suggest_threshold(db_session) is None
        assert db_session.scalars(
            select(JudgeSuggestion).where(
                JudgeSuggestion.kind == CALIBRATION_KIND
            )
        ).all() == []


class TestInboxItem:
    def test_calibration_item_in_review_queue(self, db_session: Session) -> None:
        from app.services.review import threshold_calibration_items

        _merge_log(db_session, "alpha-beta-gamma-delta", "alpha-beta-gamma-x1")
        _merge_log(db_session, "omega-one-two-three", "omega-one-two-x9")
        _merge_log(db_session, "sigma-seven-eight-nine-ten", "sigma-seven-z1-z2-z3")
        _split_ruling(db_session, "alpha-qq", "omega-ww")
        _split_ruling(db_session, "beta-ee", "sigma-rr")
        _split_ruling(db_session, "gamma-tt", "delta-yy")
        suggest_threshold(db_session)
        items = threshold_calibration_items(db_session)
        assert len(items) == 1
        assert items[0].kind == "threshold_calibration"
        assert "0.34" in items[0].reason and "0.25" in items[0].reason
