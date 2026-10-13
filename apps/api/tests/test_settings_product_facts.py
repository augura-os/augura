"""product_facts（Creative Lab 产品事实）：service 解析纪律 + 路由 PUT/GET。

解析纪律（services/settings.resolve_product_facts）：非法（非 dict /
已知键值类型错）整体丢弃返回 {}；未知键单独丢弃。路由层形状错误一律
400（非 dict / 未知键 / 非字符串数组），不进 pydantic 422。
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy.orm import Session

from app.config import get_settings
from app.exceptions import ApiError
from app.repositories.settings import SettingsRepository
from app.services.settings import PRODUCT_FACTS_KEY, resolve_product_facts

# 虚构产品事实（不与任何真实品类/产品对应）
_VALID_FACTS = {
    "core_loop": "合成部件组装机甲，放置推图收菜",
    "selling_points": ["单手竖屏", "离线也有收益"],
    "must_show": ["真实合成动画"],
    "forbidden_claims": ["必出传说机甲"],
    "compliance_limits": ["不出现血液与骷髅"],
    "success_metric_defs": ["CTR≥3%", "CPA≤40"],
}


class TestResolveProductFacts:
    def test_empty_when_unset(self, db_session: Session) -> None:
        assert resolve_product_facts(db_session) == {}

    def test_roundtrip(self, db_session: Session) -> None:
        SettingsRepository(db_session).set(
            PRODUCT_FACTS_KEY, json.dumps(_VALID_FACTS, ensure_ascii=False)
        )
        assert resolve_product_facts(db_session) == _VALID_FACTS

    def test_broken_json_returns_empty(self, db_session: Session) -> None:
        SettingsRepository(db_session).set(PRODUCT_FACTS_KEY, "{not json")
        assert resolve_product_facts(db_session) == {}

    def test_non_dict_returns_empty(self, db_session: Session) -> None:
        SettingsRepository(db_session).set(PRODUCT_FACTS_KEY, json.dumps(["core_loop"]))
        assert resolve_product_facts(db_session) == {}

    def test_wrong_value_type_discards_all(self, db_session: Session) -> None:
        # selling_points 应为 list[str]；已知键类型错 → 整体丢弃
        payload = {**_VALID_FACTS, "selling_points": "单手竖屏"}
        SettingsRepository(db_session).set(PRODUCT_FACTS_KEY, json.dumps(payload))
        assert resolve_product_facts(db_session) == {}

    def test_non_str_list_item_discards_all(self, db_session: Session) -> None:
        payload = {**_VALID_FACTS, "must_show": ["真实合成动画", 42]}
        SettingsRepository(db_session).set(PRODUCT_FACTS_KEY, json.dumps(payload))
        assert resolve_product_facts(db_session) == {}

    def test_unknown_keys_dropped_known_keys_kept(self, db_session: Session) -> None:
        # 品类不复存于 product_facts（走 project_category 键）——混入也丢弃
        payload = {**_VALID_FACTS, "category": "match9", "extra_note": "x"}
        SettingsRepository(db_session).set(PRODUCT_FACTS_KEY, json.dumps(payload))
        assert resolve_product_facts(db_session) == _VALID_FACTS


class TestProductFactsRoutes:
    def test_put_get_roundtrip(self, db_session: Session) -> None:
        from app.api.routes.settings import get_settings_info, update_settings
        from app.schemas.settings import SettingsUpdate

        result = update_settings(
            SettingsUpdate(product_facts=_VALID_FACTS), db_session, get_settings()
        )
        assert result.data is not None
        assert result.data.product_facts == _VALID_FACTS
        # GET 聚合同样读回
        info = get_settings_info(db_session, get_settings())
        assert info.data is not None
        assert info.data.product_facts == _VALID_FACTS

    def test_get_defaults_empty(self, db_session: Session) -> None:
        from app.api.routes.settings import get_settings_info

        info = get_settings_info(db_session, get_settings())
        assert info.data is not None
        assert info.data.product_facts == {}

    def test_put_rejects_non_dict(self, db_session: Session) -> None:
        from app.api.routes.settings import update_settings
        from app.schemas.settings import SettingsUpdate

        with pytest.raises(ApiError) as exc_info:
            update_settings(
                SettingsUpdate(product_facts=["not", "a", "dict"]),
                db_session,
                get_settings(),
            )
        assert exc_info.value.status_code == 400

    def test_put_rejects_unknown_key(self, db_session: Session) -> None:
        from app.api.routes.settings import update_settings
        from app.schemas.settings import SettingsUpdate

        with pytest.raises(ApiError) as exc_info:
            update_settings(
                SettingsUpdate(product_facts={"unknown_key": "x"}),
                db_session,
                get_settings(),
            )
        assert exc_info.value.status_code == 400
        assert "unknown_key" in exc_info.value.message

    def test_put_rejects_non_str_list(self, db_session: Session) -> None:
        from app.api.routes.settings import update_settings
        from app.schemas.settings import SettingsUpdate

        with pytest.raises(ApiError) as exc_info:
            update_settings(
                SettingsUpdate(product_facts={"selling_points": ["单手竖屏", 42]}),
                db_session,
                get_settings(),
            )
        assert exc_info.value.status_code == 400

    def test_put_rejects_non_str_core_loop(self, db_session: Session) -> None:
        from app.api.routes.settings import update_settings
        from app.schemas.settings import SettingsUpdate

        with pytest.raises(ApiError) as exc_info:
            update_settings(
                SettingsUpdate(product_facts={"core_loop": ["不是字符串"]}),
                db_session,
                get_settings(),
            )
        assert exc_info.value.status_code == 400

    def test_put_partial_keys_persisted(self, db_session: Session) -> None:
        from app.api.routes.settings import update_settings
        from app.schemas.settings import SettingsUpdate

        result = update_settings(
            SettingsUpdate(product_facts={"core_loop": "合成部件组装机甲"}),
            db_session,
            get_settings(),
        )
        assert result.data is not None
        assert result.data.product_facts == {"core_loop": "合成部件组装机甲"}
