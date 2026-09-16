"""Tests for market KPI baselines (services/market_stats) + 分市场阈值解析。"""
from __future__ import annotations

import json
import uuid

from sqlalchemy.orm import Session

from app.models import Performance
from app.repositories.settings import SettingsRepository
from app.services.market_stats import (
    MIN_CREATIVES_FOR_RELIABLE,
    compute_baselines,
    main_market_for_filenames,
    main_market_for_rows,
    market_baselines,
)
from app.services.settings import (
    DEFAULT_THRESHOLDS,
    GENRE_SETTING,
    MARKET_THRESHOLDS_SETTING,
    METRIC_THRESHOLDS_SETTING,
    resolve_thresholds,
)

PREFIXES = ("KS_EN", "KS_PT")


def _row(
    creative_name: str, spend: float, payers: int, roas: float | None
) -> Performance:
    raw: dict[str, object] = {"付费人数": payers}
    if roas is not None:
        raw["d1_roas"] = roas
    return Performance(
        id=str(uuid.uuid4()),
        creative_name=creative_name,
        spend=spend,
        installs=0,
        raw=raw,
    )


class TestComputeBaselines:
    def test_groups_by_market_and_takes_median(self) -> None:
        rows = [
            # EN 市场三个创意：CPP 50/100/150 → 中位数 100
            _row("KS_EN-a.mp4", 500, 10, 0.02),
            _row("KS_EN-b.mp4", 1000, 10, 0.04),
            _row("KS_EN-c.mp4", 1500, 10, 0.06),
            # 同一创意的多行先聚合（a 共 1500 消耗 25 付费 → CPP 60）
            _row("KS_EN-a.mp4", 1000, 15, 0.04),
        ]
        baselines = compute_baselines(rows, PREFIXES)
        # a=60, b=100, c=150 → 中位数 100
        assert baselines["US"].cpp_median == 100.0
        assert baselines["US"].creative_count == 3
        assert baselines["US"].reliable
        # a 的 ROAS 按消耗加权：(500*0.02+1000*0.04)/1500
        assert abs(baselines["US"].roas_median - 0.04) < 1e-9

    def test_small_sample_marked_unreliable(self) -> None:
        rows = [
            _row("KS_PT-a.mp4", 300, 10, 0.02),
            _row("KS_PT-b.mp4", 300, 10, 0.02),
        ]
        baselines = compute_baselines(rows, PREFIXES)
        assert baselines["PT"].creative_count == MIN_CREATIVES_FOR_RELIABLE - 1
        assert not baselines["PT"].reliable

    def test_no_payers_cpp_none_and_rows_without_prefix_skipped(self) -> None:
        rows = [
            _row("KS_EN-a.mp4", 500, 0, None),
            _row("no-prefix-here.mp4", 999, 99, 0.99),
        ]
        baselines = compute_baselines(rows, PREFIXES)
        assert baselines["US"].cpp_median is None
        assert baselines["US"].roas_median is None
        assert "" not in baselines

    def test_db_entry_point(self, db_session: Session) -> None:
        db_session.add_all(
            [
                _row("KS_EN-a.mp4", 500, 10, 0.02),
                _row("KS_EN-b.mp4", 1000, 10, 0.03),
                _row("KS_EN-c.mp4", 1500, 10, 0.04),
            ]
        )
        db_session.flush()
        baselines = market_baselines(db_session)
        assert baselines["US"].cpp_median == 100.0


class TestMainMarket:
    def test_highest_spend_market_wins(self) -> None:
        rows = [
            _row("KS_EN-a.mp4", 100, 5, 0.02),
            _row("KS_PT-a.mp4", 900, 5, 0.02),
        ]
        assert main_market_for_rows(rows, PREFIXES) == "PT"

    def test_no_rows_returns_empty(self) -> None:
        assert main_market_for_rows([], PREFIXES) == ""

    def test_filename_fallback(self) -> None:
        assert main_market_for_filenames(["plain.mp4", "KS_EN-x.mp4"], PREFIXES) == "US"
        assert main_market_for_filenames(["plain.mp4"], PREFIXES) == ""


class TestResolveThresholds:
    """优先级：市场覆盖 → 用户全局覆盖/品类档 → 全局默认。"""

    def test_no_market_behaves_as_before(self, db_session: Session) -> None:
        assert resolve_thresholds(db_session) == DEFAULT_THRESHOLDS
        assert resolve_thresholds(db_session, None) == DEFAULT_THRESHOLDS
        assert resolve_thresholds(db_session, "") == DEFAULT_THRESHOLDS

    def test_market_override_wins(self, db_session: Session) -> None:
        repo = SettingsRepository(db_session)
        repo.set(METRIC_THRESHOLDS_SETTING, json.dumps({"cpp_red_line": 100}))
        repo.set(
            MARKET_THRESHOLDS_SETTING,
            json.dumps({"US": {"cpp_red_line": 200}}),
        )
        assert resolve_thresholds(db_session, "US")["cpp_red_line"] == 200.0
        # 未覆盖的键沿用用户全局覆盖
        assert resolve_thresholds(db_session, "US")["cpp_pause_line"] == 180.0
        # 别的市场不受 EN 覆盖影响
        assert resolve_thresholds(db_session, "PT")["cpp_red_line"] == 100.0

    def test_market_override_beats_genre(self, db_session: Session) -> None:
        repo = SettingsRepository(db_session)
        repo.set(GENRE_SETTING, "match3")
        repo.set(
            MARKET_THRESHOLDS_SETTING,
            json.dumps({"US": {"roas_green_line": 0.05}}),
        )
        thresholds = resolve_thresholds(db_session, "US")
        assert thresholds["roas_green_line"] == 0.05
        # 未覆盖的键仍吃品类档
        assert thresholds["d1_retention_weak_line"] == 0.40

    def test_invalid_market_entries_ignored(self, db_session: Session) -> None:
        SettingsRepository(db_session).set(
            MARKET_THRESHOLDS_SETTING,
            json.dumps(
                {"US": {"cpp_red_line": -1, "unknown": 5}, "": {"cpp_red_line": 1}}
            ),
        )
        thresholds = resolve_thresholds(db_session, "US")
        assert thresholds["cpp_red_line"] == 120.0
        assert "unknown" not in thresholds

    def test_broken_json_falls_back(self, db_session: Session) -> None:
        SettingsRepository(db_session).set(MARKET_THRESHOLDS_SETTING, "{not json")
        assert resolve_thresholds(db_session, "US")["cpp_red_line"] == 120.0


class TestMarketThresholdsRoute:
    """Settings 路由读写 market_thresholds（直接调路由函数，同 db_session 夹具）。"""

    def test_put_get_roundtrip(self, db_session: Session) -> None:
        from app.api.routes.settings import update_settings
        from app.config import Settings
        from app.schemas.settings import SettingsUpdate

        info = update_settings(
            SettingsUpdate(market_thresholds={"en": {"cpp_red_line": 200.0}}),
            db_session,
            Settings(),
        )
        assert info.data is not None
        # 市场标签规范化为大写
        assert info.data.market_thresholds == {"US": {"cpp_red_line": 200.0}}
        # 分市场阈值生效
        assert resolve_thresholds(db_session, "US")["cpp_red_line"] == 200.0

    def test_put_rejects_unknown_key(self, db_session: Session) -> None:
        import pytest

        from app.api.routes.settings import update_settings
        from app.config import Settings
        from app.exceptions import ApiError
        from app.schemas.settings import SettingsUpdate

        with pytest.raises(ApiError):
            update_settings(
                SettingsUpdate(market_thresholds={"EN": {"nope": 1.0}}),
                db_session,
                Settings(),
            )
