"""Tests for metric configuration resolution (services/settings MetricConfig)."""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.repositories.settings import SettingsRepository
from app.services.settings import (
    DEFAULT_JUDGE_METRICS,
    DEFAULT_METRIC_PROFILE,
    DEFAULT_THRESHOLDS,
    GENRE_SETTING,
    JUDGE_METRICS_SETTING,
    METRIC_PROFILE_SETTING,
    METRIC_THRESHOLDS_SETTING,
    resolve_genre,
    resolve_metric_config,
)


class TestMetricConfigDefaults:
    def test_defaults_without_rows(self, db_session: Session) -> None:
        config = resolve_metric_config(db_session)
        assert config.profile == DEFAULT_METRIC_PROFILE
        assert config.judge_metrics == DEFAULT_JUDGE_METRICS
        assert config.thresholds == DEFAULT_THRESHOLDS


class TestMetricConfigOverrides:
    def test_profile_roundtrip(self, db_session: Session) -> None:
        SettingsRepository(db_session).set(
            METRIC_PROFILE_SETTING, json.dumps(["spend", "d3_roas", "d1_retention"])
        )
        config = resolve_metric_config(db_session)
        assert config.profile == ["spend", "d3_roas", "d1_retention"]

    def test_unknown_profile_entries_filtered(self, db_session: Session) -> None:
        SettingsRepository(db_session).set(
            METRIC_PROFILE_SETTING, json.dumps(["spend", "not-a-metric"])
        )
        config = resolve_metric_config(db_session)
        assert config.profile == ["spend"]

    def test_all_unknown_falls_back_to_default(self, db_session: Session) -> None:
        SettingsRepository(db_session).set(
            METRIC_PROFILE_SETTING, json.dumps(["nope"])
        )
        assert resolve_metric_config(db_session).profile == DEFAULT_METRIC_PROFILE

    def test_threshold_override_merges(self, db_session: Session) -> None:
        SettingsRepository(db_session).set(
            METRIC_THRESHOLDS_SETTING, json.dumps({"cpp_red_line": 100})
        )
        thresholds = resolve_metric_config(db_session).thresholds
        assert thresholds["cpp_red_line"] == 100.0
        # 其余阈值保持默认
        assert thresholds["roas_green_line"] == DEFAULT_THRESHOLDS["roas_green_line"]

    def test_invalid_threshold_ignored(self, db_session: Session) -> None:
        SettingsRepository(db_session).set(
            METRIC_THRESHOLDS_SETTING,
            json.dumps({"cpp_red_line": -5, "unknown_key": 1}),
        )
        thresholds = resolve_metric_config(db_session).thresholds
        assert thresholds["cpp_red_line"] == DEFAULT_THRESHOLDS["cpp_red_line"]
        assert "unknown_key" not in thresholds

    def test_broken_json_falls_back(self, db_session: Session) -> None:
        SettingsRepository(db_session).set(JUDGE_METRICS_SETTING, "{not json")
        assert resolve_metric_config(db_session).judge_metrics == DEFAULT_JUDGE_METRICS


class TestGenreThresholds:
    """品类默认阈值档（v0.12）：休闲类留存/回报线抬高，DB 覆盖永远最高优先。"""

    def test_match3_raises_retention_and_roas(self, db_session: Session) -> None:
        SettingsRepository(db_session).set(GENRE_SETTING, "match3")
        thresholds = resolve_metric_config(db_session).thresholds
        assert thresholds["d1_retention_weak_line"] == 0.40
        assert thresholds["roas_green_line"] == 0.03
        # 未分化的项沿用全局默认
        assert thresholds["cpp_red_line"] == DEFAULT_THRESHOLDS["cpp_red_line"]

    def test_idle_tycoon_keeps_global_defaults(self, db_session: Session) -> None:
        SettingsRepository(db_session).set(GENRE_SETTING, "idle_tycoon")
        assert resolve_metric_config(db_session).thresholds == DEFAULT_THRESHOLDS

    def test_invalid_genre_falls_back(self, db_session: Session) -> None:
        SettingsRepository(db_session).set(GENRE_SETTING, "not-a-genre")
        assert resolve_metric_config(db_session).thresholds == DEFAULT_THRESHOLDS
        assert resolve_genre(db_session) == "other"

    def test_db_override_beats_genre_default(self, db_session: Session) -> None:
        repo = SettingsRepository(db_session)
        repo.set(GENRE_SETTING, "match3")
        repo.set(
            METRIC_THRESHOLDS_SETTING, json.dumps({"d1_retention_weak_line": 0.45})
        )
        assert resolve_metric_config(db_session).thresholds[
            "d1_retention_weak_line"
        ] == 0.45
