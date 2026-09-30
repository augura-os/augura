"""Tests for services/daily_brief 与 recommendations 路由的读写分离。

GET /creatives/recommendations 纯读（不流转、不写 edit_logs）；
POST /creatives/recommendations/refresh 才是有状态刷新（active→watch
自动流转 + edit_logs 留痕）。
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


def _seed_bad_creative(db: Session) -> Creative:
    """低分 creative：cpp 远超暂停线 + 低置信 → total < 50（watch 线）。

    只种子一个 creative 时 max_date 就是数据日，days_idle=0 →
    total = 0*0.4 + 100*0.25 + 50*0.2 + 30*0.15 = 39.5。
    """
    stem = "KS_EN-260101-58-制作人甲-反复挑战重试Ai片头超长素材V1-竖"
    creative = Creative(id=str(uuid.uuid4()), name="bad-one")
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
            id=str(uuid.uuid4()), asset_id=asset.id, confidence=0.3
        )
    )
    db.add(
        Performance(
            id=str(uuid.uuid4()),
            creative_name=stem.lower(),
            date=date(2026, 8, 1),
            spend=1000.0,
            installs=10,
            raw={"付费人数": 1, "D1_Roas": 0.0},
        )
    )
    db.flush()
    return creative


class TestRecommendationsReadOnly:
    def test_get_is_pure_read(self, db_session: Session) -> None:
        from app.api.routes.creatives import creative_recommendations

        creative = _seed_bad_creative(db_session)
        result = creative_recommendations(db=db_session)

        assert result.success is True
        item = next(
            item for item in result.data.items if item.creative_id == creative.id
        )
        # score 展示字段保留（计算即得），lifecycle_state 读 DB 当前值
        assert item.score is not None and item.score < 50.0
        assert set(item.score_breakdown) == {
            "performance", "freshness", "evolution", "confidence",
        }
        assert item.lifecycle_state == "active"
        # GET 不得产生写副作用：状态未流转、无审计留痕
        assert creative.lifecycle_state == "active"
        assert db_session.scalars(select(EditLog)).all() == []

    def test_refresh_transitions_and_logs(self, db_session: Session) -> None:
        from app.api.routes.creatives import (
            creative_recommendations,
            refresh_recommendations,
        )

        creative = _seed_bad_creative(db_session)
        result = refresh_recommendations(db=db_session)

        assert result.success is True
        assert result.data["transitions"] == 1
        assert creative.lifecycle_state == "watch"
        log = db_session.scalars(
            select(EditLog).where(
                EditLog.entity_id == creative.id,
                EditLog.field == "lifecycle_state",
            )
        ).one()
        assert "auto:" in log.new_value

        # 刷新后 GET 读到 DB 里的新状态
        report = creative_recommendations(db=db_session)
        item = next(
            item for item in report.data.items if item.creative_id == creative.id
        )
        assert item.lifecycle_state == "watch"

    def test_refresh_without_changes_is_noop(self, db_session: Session) -> None:
        from app.api.routes.creatives import refresh_recommendations

        _seed_bad_creative(db_session)
        assert refresh_recommendations(db=db_session).data["transitions"] == 1
        # 第二次刷新：状态已就位，不再流转也不再留痕
        assert refresh_recommendations(db=db_session).data["transitions"] == 0
        assert len(db_session.scalars(select(EditLog)).all()) == 1
