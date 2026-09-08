"""分类别校准 + 建议级采纳率（services/judge_calibration）。"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Creative, EditLog, JudgeSuggestion, Setting
from app.services.judge_calibration import (
    MIN_SAMPLE,
    calibration_by_kind,
    judge_auto_allowed,
    judge_stats,
    run_calibration,
)

T0 = datetime(2026, 8, 1, tzinfo=timezone.utc)


def _log(
    db: Session,
    *,
    entity_id: str,
    action: str = "update",
    field: str = "",
    new_value: str = "",
    old_value: str = "",
    entity_type: str = "creative",
    at: datetime,
) -> None:
    db.add(
        EditLog(
            id=str(uuid.uuid4()), entity_type=entity_type, entity_id=entity_id,
            action=action, field=field, old_value=old_value,
            new_value=new_value, created_at=at,
        )
    )
    db.flush()


def _suggest(
    db: Session,
    *,
    kind: str,
    left_id: str,
    verdict: str,
    right_id: str | None = None,
    at: datetime = T0,
) -> JudgeSuggestion:
    row = JudgeSuggestion(
        id=str(uuid.uuid4()), kind=kind, left_id=left_id, right_id=right_id,
        verdict=verdict, votes=3, reason="测试",
    )
    row.created_at = at
    db.add(row)
    db.flush()
    return row


def _gate(db: Session, key: str) -> str | None:
    row = db.scalar(select(Setting).where(Setting.key == key))
    return row.value if row else None


class TestPerKindGating:
    """单类别改判率超限只降该类别，总闸与其他类别不受影响。"""

    def _seed_logs(self, db: Session) -> None:
        # dna_assign：10 条 auto，4 条被人工改判 → 40% 超限
        for i in range(10):
            cid = str(uuid.uuid4())
            _log(db, entity_id=cid, field="dna_id",
                 new_value=f"auto: D1 家族（理由{i}）", at=T0 + timedelta(minutes=i))
            if i < 4:
                _log(db, entity_id=cid, field="dna_id",
                     new_value="D2 别的家族", at=T0 + timedelta(hours=1, minutes=i))
        # merge_pair：10 条 auto merge，1 条被 split 回退 → 10% 达标
        for i in range(10):
            cid = str(uuid.uuid4())
            _log(db, entity_id=cid, action="merge",
                 old_value=f"源{i} ({uuid.uuid4()})",
                 new_value=f"auto: 并入目标{i}", at=T0 + timedelta(minutes=30 + i))
            if i == 0:
                _log(db, entity_id=cid, action="split",
                     at=T0 + timedelta(hours=2))

    def test_kind_gate_isolation(self, db_session: Session) -> None:
        self._seed_logs(db_session)
        total, overrides, rate = run_calibration(db_session)
        assert total == 20 and overrides == 5 and rate == 0.25
        # 总闸：整体 25% ≤ 30%，不动
        assert _gate(db_session, "judge_auto_enabled") is None
        # 类别闸：只降 dna_assign
        assert _gate(db_session, "judge_auto_enabled:dna_assign") == "false"
        assert _gate(db_session, "judge_auto_enabled:merge_pair") is None
        # 生效判定：总闸开 AND 类别闸开
        assert judge_auto_allowed(db_session, "dna_assign") is False
        assert judge_auto_allowed(db_session, "merge_pair") is True
        assert judge_auto_allowed(db_session, "verdict") is True

    def test_master_gate_still_dominates(self, db_session: Session) -> None:
        # 总闸关时，即使类别闸开也不生效
        db_session.add(Setting(key="judge_auto_enabled", value="false"))
        db_session.flush()
        assert judge_auto_allowed(db_session, "dna_assign") is False

    def test_small_sample_no_downgrade(self, db_session: Session) -> None:
        # 3 条全改判（100%）但 < MIN_SAMPLE，不动开关
        for i in range(3):
            cid = str(uuid.uuid4())
            _log(db_session, entity_id=cid, field="dna_id",
                 new_value="auto: D1 家族", at=T0 + timedelta(minutes=i))
            _log(db_session, entity_id=cid, field="dna_id",
                 new_value="D9 人工", at=T0 + timedelta(hours=1, minutes=i))
        run_calibration(db_session)
        assert _gate(db_session, "judge_auto_enabled:dna_assign") is None
        assert _gate(db_session, "judge_auto_enabled") is None
        assert MIN_SAMPLE == 10


class TestDnaAssignAcceptance:
    """dna_assign 建议的采纳/驳回/未决三种情形。"""

    def test_accept_reject_pending(self, db_session: Session) -> None:
        # 采纳：人工归族到建议的 DNA（值口径 "D{code} {name}"）
        c_ok = Creative(id=str(uuid.uuid4()), name="c-ok")
        # 驳回：人工归族到别的 DNA
        c_no = Creative(id=str(uuid.uuid4()), name="c-no")
        # 未决：人工没动
        c_wait = Creative(id=str(uuid.uuid4()), name="c-wait")
        # auto 落的不算人工采纳，仍未决
        c_auto = Creative(id=str(uuid.uuid4()), name="c-auto")
        db_session.add_all([c_ok, c_no, c_wait, c_auto])
        db_session.flush()
        for creative in (c_ok, c_no, c_wait, c_auto):
            _suggest(db_session, kind="dna_assign", left_id=creative.id,
                     verdict="D1 家族甲")
        later = T0 + timedelta(hours=1)
        _log(db_session, entity_id=c_ok.id, field="dna_id",
             new_value="D1 家族甲", at=later)
        _log(db_session, entity_id=c_no.id, field="dna_id",
             new_value="D2 家族乙", at=later)
        _log(db_session, entity_id=c_auto.id, field="dna_id",
             new_value="auto: D1 家族甲（3/3 票）", at=later)

        stats = calibration_by_kind(db_session)["dna_assign"]
        assert stats.sugg_accepted == 1
        assert stats.sugg_rejected == 1
        assert stats.acceptance_rate == 0.5

    def test_merged_away_counts_rejected(self, db_session: Session) -> None:
        creative = Creative(id=str(uuid.uuid4()), name="c-gone")
        db_session.add(creative)
        db_session.flush()
        _suggest(db_session, kind="dna_assign", left_id=creative.id,
                 verdict="D1 家族甲")
        _log(db_session, entity_id=str(uuid.uuid4()), action="merge",
             old_value=f"c-gone ({creative.id})", new_value="幸存者 (x)",
             at=T0 + timedelta(hours=1))
        stats = calibration_by_kind(db_session)["dna_assign"]
        assert stats.sugg_rejected == 1
        assert stats.acceptance_rate == 0.0


class TestDerivationOutcomes:
    """verdict / derivation-factor 建议的映射口径。"""

    def test_verdict_accept_and_unlink_reject(self, db_session: Session) -> None:
        d1, d2 = str(uuid.uuid4()), str(uuid.uuid4())
        _suggest(db_session, kind="verdict", left_id=d1, verdict="positive")
        _suggest(db_session, kind="verdict", left_id=d2, verdict="positive")
        later = T0 + timedelta(hours=1)
        _log(db_session, entity_id=d1, entity_type="derivation",
             field="verdict", old_value="pending", new_value="positive", at=later)
        _log(db_session, entity_id=d2, entity_type="derivation",
             action="delete", field="factor", at=later)
        stats = calibration_by_kind(db_session)["verdict"]
        assert (stats.sugg_accepted, stats.sugg_rejected) == (1, 1)

    def test_factor_adopt_and_not_a_derivation(self, db_session: Session) -> None:
        d1, d2 = str(uuid.uuid4()), str(uuid.uuid4())
        _suggest(db_session, kind="derivation-factor", left_id=d1,
                 verdict="language-market")
        _suggest(db_session, kind="derivation-factor", left_id=d2,
                 verdict="not-a-derivation")
        later = T0 + timedelta(hours=1)
        _log(db_session, entity_id=d1, entity_type="derivation",
             field="factor", old_value="unknown", new_value="language-market",
             at=later)
        _log(db_session, entity_id=d2, entity_type="derivation",
             action="delete", field="factor", at=later)
        stats = calibration_by_kind(db_session)["derivation-factor"]
        assert stats.sugg_accepted == 2
        assert stats.acceptance_rate == 1.0


class TestMergePairAcceptance:
    def test_pair_merged_with_consistent_direction(self, db_session: Session) -> None:
        left, right = str(uuid.uuid4()), str(uuid.uuid4())
        _suggest(db_session, kind="merge_pair", left_id=left, right_id=right,
                 verdict="merge")
        _log(db_session, entity_id=left, action="merge",
             old_value=f"右 ({right})", new_value=f"左 ({left})",
             at=T0 + timedelta(hours=1))
        stats = calibration_by_kind(db_session)["merge_pair"]
        assert stats.sugg_accepted == 1

    def test_side_merged_into_third_party_rejected(self, db_session: Session) -> None:
        left, right, third = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        _suggest(db_session, kind="merge_pair", left_id=left, right_id=right,
                 verdict="merge")
        _log(db_session, entity_id=third, action="merge",
             old_value=f"左 ({left})", new_value=f"第三方 ({third})",
             at=T0 + timedelta(hours=1))
        stats = calibration_by_kind(db_session)["merge_pair"]
        assert stats.sugg_rejected == 1


class TestJudgeStatsSummary:
    def test_only_kinds_with_data(self, db_session: Session) -> None:
        creative = Creative(id=str(uuid.uuid4()), name="c-1")
        db_session.add(creative)
        db_session.flush()
        _suggest(db_session, kind="dna_assign", left_id=creative.id,
                 verdict="D1 家族甲")
        _log(db_session, entity_id=creative.id, field="dna_id",
             new_value="D1 家族甲", at=T0 + timedelta(hours=1))
        summary = judge_stats(db_session)
        assert list(summary) == ["dna_assign"]
        assert summary["dna_assign"]["acceptance_rate"] == 1.0
        assert summary["dna_assign"]["auto_total"] == 0

    def test_empty_db(self, db_session: Session) -> None:
        assert judge_stats(db_session) == {}


class TestReviewQueueJudgeStats:
    def test_route_returns_judge_stats(self, db_session: Session) -> None:
        from app.api.routes.review import review_queue

        creative = Creative(id=str(uuid.uuid4()), name="c-route")
        db_session.add(creative)
        db_session.flush()
        _suggest(db_session, kind="dna_assign", left_id=creative.id,
                 verdict="D1 家族甲")
        _log(db_session, entity_id=creative.id, field="dna_id",
             new_value="D1 家族甲", at=T0 + timedelta(hours=1))
        result = review_queue(db_session)
        assert result.success is True
        stats = result.data.judge_stats
        assert stats["dna_assign"].acceptance_rate == 1.0
        assert stats["dna_assign"].auto_total == 0


class TestClusterKind:
    """field="cluster" 的 auto 关联日志归入 cluster 类，按类降级。"""

    def test_cluster_override_rate_and_gate(self, db_session: Session) -> None:
        # 10 条 auto cluster，4 条被人工 split 回退 → 40% 超限
        for i in range(10):
            cid = str(uuid.uuid4())
            _log(db_session, entity_id=cid, field="cluster",
                 new_value=f"auto: cluster 素材{i}（文本 0.90）",
                 at=T0 + timedelta(minutes=i))
            if i < 4:
                _log(db_session, entity_id=cid, action="split",
                     at=T0 + timedelta(hours=1, minutes=i))
        stats = calibration_by_kind(db_session)
        assert stats["cluster"].auto_total == 10
        assert stats["cluster"].auto_overrides == 4
        assert stats["cluster"].override_rate == 0.4

        run_calibration(db_session)
        assert _gate(db_session, "judge_auto_enabled:cluster") == "false"
        assert judge_auto_allowed(db_session, "cluster") is False

    def test_cluster_no_override(self, db_session: Session) -> None:
        cid = str(uuid.uuid4())
        _log(db_session, entity_id=cid, field="cluster",
             new_value="auto: cluster 素材A（视频帧对齐 93%）", at=T0)
        stats = calibration_by_kind(db_session)
        assert stats["cluster"].auto_total == 1
        assert stats["cluster"].auto_overrides == 0
        # 无改判不进样本降级；judge_stats 只读展示包含 cluster
        assert "cluster" in judge_stats(db_session)
        run_calibration(db_session)
        assert _gate(db_session, "judge_auto_enabled:cluster") is None
