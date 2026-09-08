"""Tests for market-prefix autodetect（services/market_detect）+ 默认映射清空。"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CreativeAsset, JudgeSuggestion
from app.repositories.settings import SettingsRepository
from app.services import markets
from app.services.market_detect import detect_prefixes, suggest_detected_prefixes


def _assets(db: Session, prefix: str, count: int) -> None:
    for i in range(count):
        db.add(
            CreativeAsset(
                id=str(uuid.uuid4()),
                filename=f"{prefix}-260630-{i}-素材.mp4",
                file_type="video",
                storage_key=f"test/{uuid.uuid4()}",
            )
        )
    db.flush()


class TestDefaultTagMap:
    def test_default_map_is_empty(self) -> None:
        """公开代码零项目痕迹：默认映射必须为空（真实映射进 settings）。"""
        assert markets.DEFAULT_MARKET_TAG_MAP == {}

    def test_resolve_falls_back_to_empty(self, db_session: Session) -> None:
        assert markets.resolve_market_tag_map(db_session) == {}


class TestDetectPrefixes:
    def test_frequency_threshold(self, db_session: Session) -> None:
        _assets(db_session, "BR", 3)  # 达标
        _assets(db_session, "MX", 2)  # 不足 3 次
        detected = detect_prefixes(db_session)
        assert detected == [("BR", 3)]

    def test_configured_prefix_excluded(self, db_session: Session) -> None:
        _assets(db_session, "BR", 5)
        SettingsRepository(db_session).set("market_prefixes", "BR,KS_EN")
        db_session.flush()
        assert detect_prefixes(db_session) == []

    def test_pattern_rules(self, db_session: Session) -> None:
        # 小写开头 / 超长 / 无分隔符 都不是候选
        for name in ("br-260630-a.mp4", "TOOLONGPREFIX-260630-a.mp4",
                     "BR260630-a.mp4", "素材-no-prefix.mp4"):
            db_session.add(
                CreativeAsset(
                    id=str(uuid.uuid4()), filename=name, file_type="video",
                    storage_key=f"test/{uuid.uuid4()}",
                )
            )
        db_session.flush()
        assert detect_prefixes(db_session) == []

    def test_suggestion_upsert_and_cleanup(self, db_session: Session) -> None:
        _assets(db_session, "BR", 4)
        detected = suggest_detected_prefixes(db_session)
        assert detected == [("BR", 4)]
        rows = db_session.scalars(
            select(JudgeSuggestion).where(JudgeSuggestion.kind == "market_detect")
        ).all()
        assert len(rows) == 1
        assert rows[0].verdict == "BR"
        assert "4 次" in rows[0].reason
        # 再跑幂等
        suggest_detected_prefixes(db_session)
        assert len(db_session.scalars(
            select(JudgeSuggestion).where(JudgeSuggestion.kind == "market_detect")
        ).all()) == 1
        # 前缀被配置后建议自动清除（收件箱只进不出，靠状态消除）
        SettingsRepository(db_session).set("market_prefixes", "BR")
        db_session.flush()
        assert suggest_detected_prefixes(db_session) == []
        assert db_session.scalars(
            select(JudgeSuggestion).where(JudgeSuggestion.kind == "market_detect")
        ).all() == []
