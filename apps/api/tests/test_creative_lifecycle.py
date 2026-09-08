"""Tests for creative score + lifecycle（services/creative_score, lifecycle）。

评分函数纯计算直接测；流转/收件箱条目用测试库种子数据。
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AnalysisResult,
    Creative,
    CreativeAsset,
    CreativeVariant,
    EditLog,
    Performance,
)
from app.services.creative_score import (
    confidence_score,
    evolution_score,
    freshness_score,
    performance_score,
    score_creative,
)
from app.services.lifecycle import (
    apply_auto_transitions,
    compute_auto_state,
    restore_creative,
    set_lifecycle,
)
from app.services.recommendation import CreativeMetrics
from app.services.settings import ScoreConfig

_CONFIG = ScoreConfig()  # 默认：40/25/20/15，阈值 30 分 / 7 天


def _metrics(
    *,
    cpp: float | None = 50.0,
    roas: float | None = 0.03,
    days_idle: int | None = 0,
    derivation_count: int = 0,
    judged_count: int = 0,
    positive_count: int = 0,
    variant_count: int = 1,
) -> CreativeMetrics:
    return CreativeMetrics(
        creative_id="c",
        creative_name="c",
        dna_code=None,
        dna_name=None,
        spend=100.0,
        payers=2,
        installs=10,
        cpp=cpp,
        roas=roas,
        cpi=None,
        ipm=None,
        row_count=1,
        days_idle=days_idle,
        recent_spend=0.0,
        recent_cpp=None,
        variant_count=variant_count,
        derivation_count=derivation_count,
        judged_count=judged_count,
        positive_count=positive_count,
    )


class TestPerformanceScore:
    def test_no_cpp_data_is_neutral(self) -> None:
        assert performance_score(None, None, _CONFIG) == 50.0

    def test_efficient_cpp_is_100(self) -> None:
        assert performance_score(30.0, None, _CONFIG) == 100.0

    def test_pause_line_cpp_is_0(self) -> None:
        assert performance_score(200.0, None, _CONFIG) == 0.0

    def test_linear_interpolation(self) -> None:
        # efficient=60, pause=180：cpp=120 正好中点 → 50
        assert performance_score(120.0, None, _CONFIG) == 50.0

    def test_roas_assists(self) -> None:
        # cpp 满分 + roas 达绿线 → 仍 100；roas 为 0 拉低
        assert performance_score(30.0, 0.05, _CONFIG) == 100.0
        assert performance_score(30.0, 0.0, _CONFIG) == 70.0  # 0.7*100 + 0.3*0


class TestFreshnessScore:
    def test_no_data_neutral(self) -> None:
        assert freshness_score(None) == 50.0

    def test_decay(self) -> None:
        assert freshness_score(0) == 100.0
        assert freshness_score(15) == 50.0
        assert freshness_score(30) == 0.0
        assert freshness_score(45) == 0.0


class TestEvolutionScore:
    def test_no_derivation_neutral(self) -> None:
        assert evolution_score(0, 0, 0, 1) == 50.0

    def test_positive_ratio(self) -> None:
        assert evolution_score(4, 4, 3, 4) == 85.0  # 75 + 多变体 10

    def test_all_failed(self) -> None:
        assert evolution_score(3, 3, 0, 2) == 10.0


class TestConfidenceScore:
    def test_no_analysis(self) -> None:
        assert confidence_score(None) == 30.0

    def test_mean_scaled(self) -> None:
        assert confidence_score(0.85) == 85.0


class TestScoreCreative:
    def test_weighted_total(self) -> None:
        # 全满分要素 → total 100；权重不成 100 也按总和归一
        breakdown = score_creative(
            _metrics(cpp=30.0, roas=0.05, days_idle=0,
                     derivation_count=2, judged_count=2, positive_count=2,
                     variant_count=2),
            1.0,
            _CONFIG,
        )
        assert breakdown.total == 100.0

    def test_bad_creative_low_score(self) -> None:
        breakdown = score_creative(
            _metrics(cpp=500.0, roas=0.0, days_idle=30),
            None,
            _CONFIG,
        )
        # 0*0.4 + 0*0.25 + 50*0.2 + 30*0.15 = 14.5
        assert breakdown.total == 14.5


class TestComputeAutoState:
    def test_archive_candidate(self) -> None:
        assert compute_auto_state(20.0, 10, _CONFIG) == "archived"

    def test_low_score_watch(self) -> None:
        assert compute_auto_state(40.0, 0, _CONFIG) == "watch"
        # 分数低但闲置天数不够 → 不建议归档，只 watch
        assert compute_auto_state(20.0, 3, _CONFIG) == "watch"
        assert compute_auto_state(20.0, None, _CONFIG) == "watch"

    def test_healthy_active(self) -> None:
        assert compute_auto_state(80.0, 10, _CONFIG) == "active"


class TestApplyAutoTransitions:
    def _creative(self, db: Session, name: str, state: str = "active") -> Creative:
        creative = Creative(
            id=str(uuid.uuid4()), name=name, lifecycle_state=state
        )
        db.add(creative)
        db.flush()
        return creative

    def test_active_to_watch_only(self, db_session: Session) -> None:
        low = self._creative(db_session, "low")
        high = self._creative(db_session, "high")
        archived = self._creative(db_session, "arch", state="archived")
        changed = apply_auto_transitions(
            db_session,
            {low.id: (40.0, 0), high.id: (90.0, 0), archived.id: (0.0, 30)},
            _CONFIG,
        )
        assert changed == 1
        db_session.flush()
        assert low.lifecycle_state == "watch"
        assert high.lifecycle_state == "active"
        # archived 是人工领地，永不自动流转
        assert archived.lifecycle_state == "archived"
        log = db_session.scalars(
            select(EditLog).where(
                EditLog.entity_id == low.id,
                EditLog.field == "lifecycle_state",
            )
        ).one()
        assert "auto:" in log.new_value

    def test_disabled_is_noop(self, db_session: Session) -> None:
        low = self._creative(db_session, "low")
        config = ScoreConfig(auto_enabled=False)
        changed = apply_auto_transitions(db_session, {"low": (10.0, 30)}, config)
        assert changed == 0
        assert low.lifecycle_state == "active"


class TestSetLifecycle:
    def test_archive_and_restore_leave_edit_logs(self, db_session: Session) -> None:
        creative = Creative(id=str(uuid.uuid4()), name="c")
        db_session.add(creative)
        db_session.flush()

        set_lifecycle(db_session, creative.id, "archived", reason="确认归档")
        assert creative.lifecycle_state == "archived"
        restore_creative(db_session, creative.id)
        assert creative.lifecycle_state == "active"
        logs = db_session.scalars(
            select(EditLog).where(EditLog.entity_id == creative.id)
        ).all()
        assert [log.new_value for log in logs] == [
            "archived（确认归档）",
            "active（人工恢复）",
        ]

    def test_invalid_state_rejected(self, db_session: Session) -> None:
        import pytest

        with pytest.raises(ValueError):
            set_lifecycle(db_session, "x", "deleted")

    def test_missing_creative_returns_none(self, db_session: Session) -> None:
        assert set_lifecycle(db_session, str(uuid.uuid4()), "watch") is None


class TestArchiveSuggestionItems:
    def _seed_creative(
        self,
        db: Session,
        *,
        name: str,
        stem: str,
        spend: float,
        payers: int,
        row_date: date,
        confidence: float = 0.3,
        state: str = "active",
    ) -> Creative:
        creative = Creative(
            id=str(uuid.uuid4()), name=name, lifecycle_state=state
        )
        asset = CreativeAsset(
            id=str(uuid.uuid4()), filename=f"{stem}.mp4", file_type="video",
            storage_key=f"test/{uuid.uuid4()}",
        )
        db.add_all([creative, asset])
        db.flush()
        db.add(
            CreativeVariant(
                id=str(uuid.uuid4()), creative_id=creative.id,
                asset_id=asset.id, name=stem,
            )
        )
        db.add(
            AnalysisResult(
                id=str(uuid.uuid4()), asset_id=asset.id, confidence=confidence
            )
        )
        db.add(
            Performance(
                id=str(uuid.uuid4()),
                creative_name=stem.lower(),
                date=row_date,
                spend=spend,
                installs=10,
                raw={"付费人数": payers, "D1_Roas": 0.0},
            )
        )
        db.flush()
        return creative

    def test_archive_suggestion_surfaced(self, db_session: Session) -> None:
        from app.services.review import archive_suggestion_items

        # 差创意：cpp 远超暂停线 + 30 天前最后数据 + 低置信 → 低分
        bad = self._seed_creative(
            db_session, name="bad-one", stem="KS_EN-260630-58-制作人甲-屡次失败重开Ai片头超长素材V1-竖",
            spend=1000.0, payers=1, row_date=date(2026, 8, 1),
        )
        # 好创意：成本低 + 最新数据（把全库 max_date 顶到 8/31）
        good = self._seed_creative(
            db_session, name="good-one", stem="KS_EN-260831-58-制作人甲-四季解说Ai片头超长素材V1-竖",
            spend=600.0, payers=20, row_date=date(2026, 8, 31), confidence=0.95,
        )
        items = archive_suggestion_items(db_session)
        ids = [item.creative_id for item in items]
        assert bad.id in ids
        assert good.id not in ids
        item = next(item for item in items if item.creative_id == bad.id)
        assert item.kind == "archive_suggestion"
        assert "评分" in item.reason and "效果" in item.reason
        assert "30 天无消耗" in item.reason

    def test_archived_not_suggested_again(self, db_session: Session) -> None:
        from app.services.review import archive_suggestion_items

        self._seed_creative(
            db_session, name="bad-one", stem="KS_EN-260630-58-制作人甲-屡次失败重开Ai片头超长素材V1-竖",
            spend=1000.0, payers=1, row_date=date(2026, 8, 1), state="archived",
        )
        self._seed_creative(
            db_session, name="good-one", stem="KS_EN-260831-58-制作人甲-四季解说Ai片头超长素材V1-竖",
            spend=600.0, payers=20, row_date=date(2026, 8, 31),
        )
        assert archive_suggestion_items(db_session) == []


class TestLifecycleRoute:
    def test_put_lifecycle(self, db_session: Session) -> None:
        from app.api.routes.creatives import update_lifecycle
        from app.schemas.creative import LifecycleUpdate

        creative = Creative(id=str(uuid.uuid4()), name="c")
        db_session.add(creative)
        db_session.flush()
        result = update_lifecycle(
            creative.id, LifecycleUpdate(state="watch", reason="保留观察"),
            db=db_session,
        )
        assert result.success is True
        assert result.data["lifecycle_state"] == "watch"
