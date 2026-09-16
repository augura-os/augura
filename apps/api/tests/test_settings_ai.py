"""Settings 模型自动发现与连接测试（services/settings + routes/settings）。

OpenAI SDK 的 models.list() 全部 mock——不打真实端点。
"""
from __future__ import annotations

import httpx
import pytest
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
)
from sqlalchemy.orm import Session

from app.config import get_settings
from app.exceptions import ApiError
from app.repositories.settings import SettingsRepository
from app.services import settings as settings_service
from app.services.settings import (
    API_KEY_SETTING,
    BASE_URL_SETTING,
    VISION_MODEL_SETTING,
    AIConfig,
    list_provider_models,
)

_CONFIG = AIConfig(
    api_key="sk-test",
    base_url="https://example.com/v1",
    vision_model="model-a",
    embedding_model="",
)


class _FakeModel:
    def __init__(self, model_id: str) -> None:
        self.id = model_id


class _FakePage:
    def __init__(self, model_ids: list[str]) -> None:
        self.data = [_FakeModel(model_id) for model_id in model_ids]


class _FakeModels:
    def __init__(self, model_ids: list[str] | None = None, error: Exception | None = None):
        self._model_ids = model_ids
        self._error = error

    def list(self) -> _FakePage:
        if self._error is not None:
            raise self._error
        return _FakePage(self._model_ids or [])


class _FakeClient:
    def __init__(self, model_ids: list[str] | None = None, error: Exception | None = None):
        self.models = _FakeModels(model_ids, error)


def _patch_client(monkeypatch: pytest.MonkeyPatch, **kwargs) -> None:
    monkeypatch.setattr(
        settings_service, "OpenAI", lambda **_kw: _FakeClient(**kwargs)
    )


def _http_error(exc_class, status: int) -> Exception:
    request = httpx.Request("GET", "https://example.com/v1/models")
    if exc_class is APITimeoutError:
        return APITimeoutError(request=request)
    if exc_class is APIConnectionError:
        return APIConnectionError(request=request)
    response = httpx.Response(status, request=request)
    return exc_class("error", response=response, body=None)


class TestListProviderModels:
    def test_returns_sorted_ids(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_client(monkeypatch, model_ids=["model-b", "model-a"])
        result = list_provider_models(_CONFIG)
        assert result.ok is True
        assert result.models == ["model-a", "model-b"]

    def test_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_client(monkeypatch, error=_http_error(AuthenticationError, 401))
        result = list_provider_models(_CONFIG)
        assert result.ok is False
        assert "401" in result.message

    def test_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_client(monkeypatch, error=_http_error(APITimeoutError, 0))
        result = list_provider_models(_CONFIG)
        assert result.ok is False
        assert "超时" in result.message

    def test_connection_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_client(monkeypatch, error=_http_error(APIConnectionError, 0))
        result = list_provider_models(_CONFIG)
        assert result.ok is False
        assert "连接失败" in result.message

    def test_models_endpoint_not_supported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_client(monkeypatch, error=_http_error(APIStatusError, 404))
        result = list_provider_models(_CONFIG)
        assert result.ok is False
        assert "不支持模型列表" in result.message
        # 404 最常见的原因是 Base URL 漏了 /v1，提示里必须带这个排查方向
        assert "/v1" in result.message

    def test_missing_api_key(self) -> None:
        result = list_provider_models(
            AIConfig(api_key="", base_url="https://example.com/v1",
                     vision_model="", embedding_model="")
        )
        assert result.ok is False
        assert "API key" in result.message


class TestProviderConnection:
    def test_ok_reports_model_count(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_client(monkeypatch, model_ids=["model-a", "model-b"])
        ok, message = settings_service.test_provider_connection(_CONFIG)
        assert ok is True
        assert "2 个模型可用" in message

    def test_vision_model_checked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_client(monkeypatch, model_ids=["model-a"])
        ok, message = settings_service.test_provider_connection(_CONFIG, vision_model="model-a")
        assert ok is True
        ok, message = settings_service.test_provider_connection(_CONFIG, vision_model="model-zzz")
        assert ok is False
        assert "model-zzz" in message

    def test_failure_propagates_reason(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_client(monkeypatch, error=_http_error(AuthenticationError, 401))
        ok, message = settings_service.test_provider_connection(_CONFIG)
        assert ok is False
        assert "401" in message


class TestRoutes:
    """路由层：mock service 层的 OpenAI 客户端，走真实 DB 配置解析。"""

    def _configure(self, db: Session) -> None:
        repo = SettingsRepository(db)
        repo.set(API_KEY_SETTING, "sk-test")
        repo.set(BASE_URL_SETTING, "https://example.com/v1")
        repo.set(VISION_MODEL_SETTING, "model-a")
        db.flush()

    def test_models_endpoint_ok(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.api.routes.settings import list_ai_models

        self._configure(db_session)
        _patch_client(monkeypatch, model_ids=["model-a", "model-b"])
        result = list_ai_models(db_session, get_settings())
        assert result.success is True
        assert result.data.models == ["model-a", "model-b"]

    def test_models_endpoint_error_message(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.api.routes.settings import list_ai_models

        self._configure(db_session)
        _patch_client(monkeypatch, error=_http_error(AuthenticationError, 401))
        with pytest.raises(ApiError) as exc_info:
            list_ai_models(db_session, get_settings())
        assert "401" in exc_info.value.message

    def test_test_endpoint_ok_and_model_mismatch(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.api.routes.settings import test_ai_connection
        from app.schemas.settings import AiTestRequest

        self._configure(db_session)
        _patch_client(monkeypatch, model_ids=["model-a"])
        result = test_ai_connection(
            AiTestRequest(vision_model="model-a"), db_session, get_settings()
        )
        assert result.data.ok is True
        assert "2" not in result.data.message  # 1 个模型
        result = test_ai_connection(
            AiTestRequest(vision_model="model-zzz"), db_session, get_settings()
        )
        assert result.data.ok is False
        assert "model-zzz" in result.data.message


class TestJudgeTemperature:
    """judge_temperature：settings 表 → AIConfig 的解析/回落 + PUT 范围校验。"""

    def test_default_when_unset(self, db_session: Session) -> None:
        config = settings_service.resolve_ai_config(db_session, get_settings())
        assert config.judge_temperature == 0.7

    def test_valid_value(self, db_session: Session) -> None:
        SettingsRepository(db_session).set("judge_temperature", "0.3")
        config = settings_service.resolve_ai_config(db_session, get_settings())
        assert config.judge_temperature == 0.3

    def test_zero_allowed(self, db_session: Session) -> None:
        # 0 是合法值（用户明确要确定性输出），只有超范围/非法才回落
        SettingsRepository(db_session).set("judge_temperature", "0")
        config = settings_service.resolve_ai_config(db_session, get_settings())
        assert config.judge_temperature == 0.0

    def test_invalid_falls_back(self, db_session: Session) -> None:
        SettingsRepository(db_session).set("judge_temperature", "abc")
        config = settings_service.resolve_ai_config(db_session, get_settings())
        assert config.judge_temperature == 0.7

    def test_out_of_range_falls_back(self, db_session: Session) -> None:
        SettingsRepository(db_session).set("judge_temperature", "1.5")
        config = settings_service.resolve_ai_config(db_session, get_settings())
        assert config.judge_temperature == 0.7

    def test_put_validates_range_and_persists(self, db_session: Session) -> None:
        from app.api.routes.settings import update_settings
        from app.schemas.settings import SettingsUpdate

        with pytest.raises(ApiError) as exc_info:
            update_settings(
                SettingsUpdate(judge_temperature=1.5), db_session, get_settings()
            )
        assert exc_info.value.status_code == 400

        result = update_settings(
            SettingsUpdate(judge_temperature=0.4), db_session, get_settings()
        )
        assert result.data is not None
        assert result.data.judge_temperature == 0.4
