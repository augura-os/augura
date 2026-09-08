"""Tests for markets.resolve_market（文件名前缀 × 分析标签双层市场判定）
与 prefix_to_market_code（前缀 → 规范市场码）。"""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.repositories.settings import SettingsRepository
from app.services.markets import (
    MARKET_TAG_MAP_SETTING,
    prefix_tag,
    prefix_to_market_code,
    resolve_market,
    resolve_market_tag_map,
)

PREFIXES = ("KS_EN", "KS_PT")
TAG_MAP = {"brazil-pt": "KS_PT", "spanish-latam": "KS_ES"}


class TestPrefixToMarketCode:
    def test_mixed_prefix_suffix_hit(self) -> None:
        # 项目+市场混合前缀：最长后缀命中已知码
        assert prefix_to_market_code("KS_PT") == "PT"
        assert prefix_to_market_code("KS_ES") == "ES"

    def test_underscore_prefix_and_alias_one_hop(self) -> None:
        # KS_EN → 后缀 EN → 别名一跳到 US（EN/US 同市场）
        assert prefix_to_market_code("KS_EN") == "US"
        assert prefix_to_market_code("US") == "US"

    def test_alias_no_recursion(self) -> None:
        # 别名最多一跳：EN→US，即使 US 又配了别名也不再跳
        aliases = {"EN": "US", "US": "ZZ"}
        assert prefix_to_market_code("KS_EN", aliases) == "US"

    def test_unknown_prefix_returned_as_is(self) -> None:
        # 未命中码表 → 整段原样（向后兼容奇特约定）
        assert prefix_to_market_code("KSBR") == "KSBR"
        assert prefix_to_market_code("XX_QQ") == "XX_QQ"

    def test_settings_aliases_extensible(self) -> None:
        aliases = {"EN": "US", "BR": "PT"}
        assert prefix_to_market_code("KS_BR", aliases) == "PT"

    def test_prefix_tag_is_code(self) -> None:
        assert prefix_tag("KS_PT") == "PT"
        assert prefix_tag("KS_EN") == "US"


class TestResolveMarket:
    def test_consistent_high_confidence(self) -> None:
        market, confidence = resolve_market(
            "KS_PT-foo-竖.mp4", ["brazil-pt", "other-tag"], PREFIXES, TAG_MAP
        )
        assert (market, confidence) == ("PT", "high")

    def test_conflict_returns_declared_with_flag(self) -> None:
        market, confidence = resolve_market(
            "KS_PT-foo-竖.mp4", ["spanish-latam"], PREFIXES, TAG_MAP
        )
        assert (market, confidence) == ("PT", "conflict")

    def test_declared_only_medium(self) -> None:
        market, confidence = resolve_market("KS_EN-foo-竖.mp4", [], PREFIXES, TAG_MAP)
        assert (market, confidence) == ("US", "medium")
        # 未映射的分析标签不影响 declared
        market, confidence = resolve_market(
            "KS_EN-foo-竖.mp4", ["unknown-tag"], PREFIXES, TAG_MAP
        )
        assert (market, confidence) == ("US", "medium")

    def test_neither_returns_none(self) -> None:
        market, confidence = resolve_market("plain-name.mp4", ["x"], PREFIXES, TAG_MAP)
        assert (market, confidence) == (None, "none")

    def test_prefix_without_underscore(self) -> None:
        market, confidence = resolve_market(
            "KS_PT-foo.mp4", ["brazil-pt"], ("KS_PT", "KS_ES"),
            {"brazil-pt": "KS_PT"},
        )
        assert (market, confidence) == ("PT", "high")

    def test_tag_map_value_as_market_code(self) -> None:
        # 迁移后的写法：tag_map 的值直接是市场码
        market, confidence = resolve_market(
            "KS_PT-foo.mp4", ["brazil-pt"], ("KS_PT", "KS_ES"),
            {"brazil-pt": "PT"},
        )
        assert (market, confidence) == ("PT", "high")


class TestMarketTagMap:
    def test_values_may_be_prefix_or_code(self, db_session: Session) -> None:
        repo = SettingsRepository(db_session)
        repo.set("market_prefixes", "KS_EN,KS_PT")
        repo.set(
            MARKET_TAG_MAP_SETTING,
            json.dumps({"brazil-pt": "PT", "english": "KS_EN", "bad": "NO_SUCH"}),
        )
        tag_map = resolve_market_tag_map(db_session)
        assert tag_map == {"brazil-pt": "PT", "english": "KS_EN"}

    def test_default_map_filtered_by_default_prefixes(self, db_session: Session) -> None:
        # 默认映射为空（公开代码零项目痕迹）
        assert resolve_market_tag_map(db_session) == {}

    def test_broken_json_falls_back(self, db_session: Session) -> None:
        repo = SettingsRepository(db_session)
        repo.set("market_prefixes", "KS_EN,KS_PT")
        repo.set(MARKET_TAG_MAP_SETTING, "{not json")
        assert resolve_market_tag_map(db_session) == {}
