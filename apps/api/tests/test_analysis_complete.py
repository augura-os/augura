"""Tests for AnalysisService._complete 的 strict/JSON-Mode 分支与温度兼容记忆。"""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from app.services import analysis
from app.services.analysis import AnalysisService
from app.services.settings import AIConfig


def _bad_request(message: str) -> analysis.BadRequestError:
    return analysis.BadRequestError(
        message,
        response=httpx.Response(400, request=httpx.Request("POST", "http://x")),
        body=None,
    )


@pytest.fixture(autouse=True)
def _clear_temperature_memory() -> None:
    """端点温度兼容记忆是进程级状态，用例间必须隔离。"""
    analysis._STRICT_TEMPERATURE_REJECTED.clear()


def _patch_openai(monkeypatch, failures: list[Exception]) -> list[dict]:  # noqa: ANN001
    calls: list[dict] = []

    class Completions:
        def create(self, **kwargs):  # noqa: ANN002, ANN003, ANN201
            calls.append(kwargs)
            if failures:
                raise failures.pop(0)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))]
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    monkeypatch.setattr(analysis, "OpenAI", lambda **_kw: client)
    return calls


def _service(model: str = "gpt-4o") -> AnalysisService:
    return AnalysisService(
        AIConfig(
            api_key="k", base_url="https://api.openai.com/v1",
            vision_model=model, embedding_model="",
        )
    )


class TestStrictTemperatureMemory:
    def test_temperature_rejected_retries_strict_without_and_remembers(
        self, monkeypatch
    ) -> None:
        """端点支持 strict 但只允许固定温度：不带温度重试 strict（保住
        json_schema），并记住该端点后续不再传。"""
        calls = _patch_openai(
            monkeypatch,
            [_bad_request("invalid temperature: only 1 is allowed for this model")],
        )
        svc = _service()
        assert svc._complete([]) == '{"ok": true}'
        assert len(calls) == 2
        assert calls[0]["temperature"] == 0.2
        assert "temperature" not in calls[1]
        # 重试仍是 strict schema，没有降级 JSON Mode
        assert calls[1]["response_format"]["type"] == "json_schema"
        assert ("https://api.openai.com/v1", "gpt-4o") in (
            analysis._STRICT_TEMPERATURE_REJECTED
        )
        # 第二次调用：只发 1 次请求，直接不带温度
        assert svc._complete([]) == '{"ok": true}'
        assert len(calls) == 3
        assert "temperature" not in calls[2]
        assert calls[2]["response_format"]["type"] == "json_schema"

    def test_model_switch_reprobes(self, monkeypatch) -> None:  # noqa: ANN001
        """记忆按 (base_url, model) 做 key：换模型后重新探测（仍带温度）。"""
        calls = _patch_openai(
            monkeypatch,
            [_bad_request("invalid temperature: only 1 is allowed")],
        )
        _service()._complete([])
        _service(model="other-model")._complete([])
        assert len(calls) == 3
        assert calls[2]["model"] == "other-model"
        assert "temperature" in calls[2]

    def test_non_temperature_400_falls_back_to_json_mode(
        self, monkeypatch
    ) -> None:
        """非温度 400（不支持 strict）保持原行为：落 JSON Mode，不记忆。"""
        calls = _patch_openai(
            monkeypatch, [_bad_request("response_format json_schema not supported")]
        )
        assert _service()._complete([]) == '{"ok": true}'
        assert len(calls) == 2
        assert calls[1]["response_format"] == {"type": "json_object"}
        assert analysis._STRICT_TEMPERATURE_REJECTED == set()
