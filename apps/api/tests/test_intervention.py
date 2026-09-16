"""人工介入密度口径（services/intervention）：auto: 排除、周界、空分母。"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.models import Creative, EditLog
from app.services.intervention import WEEKS, attach_skew, intervention_density

# 2026-09-14 是周一（ISO 周起点）；NOW 落在该周周二
MONDAY = date(2026, 9, 14)
NOW = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)


def _log(
    db: Session,
    *,
    entity_type: str = "creative",
    action: str = "update",
    field: str = "",
    new_value: str = "",
    at: datetime = NOW,
) -> None:
    db.add(
        EditLog(
            id=str(uuid.uuid4()), entity_type=entity_type,
            entity_id=str(uuid.uuid4()), action=action, field=field,
            old_value="", new_value=new_value, created_at=at,
        )
    )
    db.flush()


def _creative(db: Session, *, at: datetime = NOW) -> None:
    db.add(Creative(id=str(uuid.uuid4()), name="素材", created_at=at))
    db.flush()


class TestRulingScope:
    """口径：只数裁决类操作里的人工记录（new_value 不带 auto: 前缀）。"""

    def test_auto_prefix_excluded(self, db_session: Session) -> None:
        _log(db_session, action="merge", new_value="auto: B (id-2)")
        _log(db_session, action="merge", new_value="B (id-2)")
        _log(db_session, field="dna_id", new_value="auto: D1 家族（judge）")
        _log(db_session, field="dna_id", new_value="D2 别的家族")
        current = intervention_density(db_session, now=NOW)[-1]
        assert current.human_rulings == 2

    def test_ruling_scopes_included(self, db_session: Session) -> None:
        _log(db_session, field="dna_id", new_value="D1 家族")          # 归族
        _log(db_session, action="merge", new_value="B (id-2)")         # 合并
        _log(db_session, action="split", new_value="C (id-3)")         # 拆分
        _log(db_session, field="observation_pair", new_value="A ↔ B 维持拆分登记（像）")
        _log(db_session, entity_type="derivation", action="create",
             field="factor", new_value="A -[换皮]-> B")                # 建链
        _log(db_session, entity_type="derivation", field="verdict",
             new_value="positive")                                    # 改判定
        current = intervention_density(db_session, now=NOW)[-1]
        assert current.human_rulings == 6

    def test_non_rulings_excluded(self, db_session: Session) -> None:
        # 生命周期流转（有独立 auto 机制，且语义是运营状态而非归族裁决）
        _log(db_session, field="lifecycle_state", new_value="archived（手动）")
        # 建族 = 分类法管理，不随新素材量伸缩
        _log(db_session, entity_type="dna", action="create",
             field="code", new_value="D9 新家族")
        # 素材内容标注而非裁决
        _log(db_session, entity_type="asset", field="tags", new_value='["a"]')
        # 系统扫描记录
        _log(db_session, entity_type="system", action="auto_scan",
             new_value="auto: 周期巩固扫描")
        current = intervention_density(db_session, now=NOW)[-1]
        assert current.human_rulings == 0


class TestWeekBucketing:
    def test_sunday_belongs_to_previous_week(self, db_session: Session) -> None:
        sunday = datetime(2026, 9, 13, 23, 59, 59, tzinfo=timezone.utc)
        monday = datetime(2026, 9, 14, 0, 0, 0, tzinfo=timezone.utc)
        _log(db_session, action="split", new_value="C (id-3)", at=sunday)
        _log(db_session, action="split", new_value="D (id-4)", at=monday)
        series = intervention_density(db_session, now=NOW)
        assert series[-2].week_start == MONDAY - timedelta(weeks=1)
        assert series[-2].human_rulings == 1
        assert series[-1].week_start == MONDAY
        assert series[-1].human_rulings == 1

    def test_series_is_12_weeks_ending_this_monday(self, db_session: Session) -> None:
        series = intervention_density(db_session, now=NOW)
        assert len(series) == WEEKS == 12
        assert series[-1].week_start == MONDAY
        assert series[0].week_start == MONDAY - timedelta(weeks=11)
        # 空库：全零、密度 None（不是 0）
        assert all(w.human_rulings == 0 and w.new_creatives == 0 for w in series)
        assert all(w.density is None for w in series)

    def test_logs_before_window_not_counted(self, db_session: Session) -> None:
        before = datetime(2026, 6, 28, 23, 59, tzinfo=timezone.utc)  # 窗口外
        _log(db_session, action="split", new_value="C (id-3)", at=before)
        series = intervention_density(db_session, now=NOW)
        assert sum(w.human_rulings for w in series) == 0


class TestDensity:
    def test_none_when_no_new_creatives(self, db_session: Session) -> None:
        _log(db_session, action="merge", new_value="B (id-2)")
        current = intervention_density(db_session, now=NOW)[-1]
        assert current.human_rulings == 1
        assert current.new_creatives == 0
        assert current.density is None

    def test_density_is_rulings_over_creatives(self, db_session: Session) -> None:
        for _ in range(3):
            _log(db_session, action="split", new_value="C (id)")
        _creative(db_session)
        _creative(db_session)
        # 上周的素材不计入本周分母
        _creative(db_session, at=NOW - timedelta(weeks=1))
        series = intervention_density(db_session, now=NOW)
        assert series[-1].density == 1.5
        assert series[-2].new_creatives == 1
        assert series[-2].density == 0.0  # 有分母无裁决 = 0（与 None 区分）


class TestAttachSkew:
    """hub 偏斜监控（embedding 设计 §3.5）：自动 attach 次数按族的分布。"""

    def _cluster_log(self, db: Session, creative_id: str, *, auto: bool) -> None:
        db.add(
            EditLog(
                id=str(uuid.uuid4()), entity_type="creative",
                entity_id=creative_id, action="update", field="cluster",
                old_value="",
                new_value=(
                    "auto: cluster 素材（文本 0.40）" if auto
                    else "cluster 素材（人工移动）"
                ),
                created_at=NOW,
            )
        )
        db.flush()

    def test_distribution_and_top_share(self, db_session: Session) -> None:
        creative_a = Creative(id=str(uuid.uuid4()), name="大族甲")
        creative_b = Creative(id=str(uuid.uuid4()), name="小族乙")
        db_session.add_all([creative_a, creative_b])
        db_session.flush()
        for _ in range(3):
            self._cluster_log(db_session, creative_a.id, auto=True)
        self._cluster_log(db_session, creative_b.id, auto=True)
        # 人工移动（无 auto: 前缀）不计入——只监控自动 attach
        self._cluster_log(db_session, creative_b.id, auto=False)

        skew = attach_skew(db_session)
        assert skew.attach_total == 4
        assert skew.creatives_with_attaches == 2
        assert skew.top_creative_name == "大族甲"
        assert skew.top_count == 3
        assert skew.top_share == pytest.approx(0.75)

    def test_empty(self, db_session: Session) -> None:
        skew = attach_skew(db_session)
        assert skew.attach_total == 0
        assert skew.creatives_with_attaches == 0
        assert skew.top_creative_name is None
        assert skew.top_count == 0
        assert skew.top_share == 0.0

    def test_review_queue_carries_hub_skew(self, db_session: Session) -> None:
        from app.api.routes.review import review_queue

        creative = Creative(id=str(uuid.uuid4()), name="族甲")
        db_session.add(creative)
        db_session.flush()
        self._cluster_log(db_session, creative.id, auto=True)
        result = review_queue(db_session)
        assert result.data is not None
        assert result.data.hub_skew.attach_total == 1
        assert result.data.hub_skew.top_creative_name == "族甲"
        assert result.data.hub_skew.top_share == 1.0
