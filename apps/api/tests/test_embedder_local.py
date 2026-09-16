"""LocalEmbedder（services/embedding）单元测试：fastembed 全部用假模块注入。

CI 不装 fastembed/onnxruntime/huggingface_hub——通过 sys.modules 注入
假实现覆盖：model_name kwarg、模型 id 断言、hf-mirror 重试、单例与锁。
"""
from __future__ import annotations

import sys
import threading
import types
from pathlib import Path

import pytest

from app.config import Settings
from app.services import embedding as embedding_service
from app.services.embedding import (
    HF_MIRROR_ENDPOINT,
    LOCAL_EMBEDDING_MODEL,
    EmbeddingUnavailableError,
    LocalEmbedder,
    get_local_embedder,
    reset_local_embedder,
)


class _FakeTextEmbedding:
    """记录构造 kwargs 的假 TextEmbedding；embed 返回固定向量。"""

    calls: list[dict] = []
    failures_before_success = 0

    def __init__(self, **kwargs) -> None:
        type(self).calls.append(kwargs)
        if type(self).failures_before_success > 0:
            type(self).failures_before_success -= 1
            raise ConnectionError("HF 不可达")
        # fastembed 0.8.0：TextEmbedding.model 是内部实现，带 model_name
        self.model = types.SimpleNamespace(model_name=kwargs.get("_actual_model", kwargs["model_name"]))

    def embed(self, texts: list[str]):
        return iter([[0.1, 0.2, 0.3] for _ in texts])


def _install_fake_fastembed(
    monkeypatch: pytest.MonkeyPatch, fake_cls: type
) -> types.ModuleType:
    module = types.ModuleType("fastembed")
    module.TextEmbedding = fake_cls  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fastembed", module)
    return module


def _install_fake_hf_hub(monkeypatch: pytest.MonkeyPatch) -> types.SimpleNamespace:
    constants = types.SimpleNamespace(ENDPOINT="https://huggingface.co")
    hub = types.ModuleType("huggingface_hub")
    hub.constants = constants  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    monkeypatch.setitem(sys.modules, "huggingface_hub.constants", constants)
    return constants


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_local_embedder()
    yield
    reset_local_embedder()


@pytest.fixture()
def _cache_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(embedding_service, "_resolve_cache_dir", lambda: str(tmp_path))


class TestLoadModel:
    def test_uses_model_name_kwarg(
        self, monkeypatch: pytest.MonkeyPatch, _cache_dir: None
    ) -> None:
        """E0 坑回归：必须用 model_name=（model= 会被 **kwargs 静默吞掉）。"""
        _FakeTextEmbedding.calls = []
        _FakeTextEmbedding.failures_before_success = 0
        _install_fake_fastembed(monkeypatch, _FakeTextEmbedding)
        embedding_service._load_model("/tmp/cache")
        assert _FakeTextEmbedding.calls[0]["model_name"] == LOCAL_EMBEDDING_MODEL
        assert "model" not in _FakeTextEmbedding.calls[0]

    def test_mirror_retry_on_failure(
        self, monkeypatch: pytest.MonkeyPatch, _cache_dir: None
    ) -> None:
        """首次失败 → patch huggingface_hub.constants.ENDPOINT 切镜像重试。"""
        _FakeTextEmbedding.calls = []
        _FakeTextEmbedding.failures_before_success = 1
        _install_fake_fastembed(monkeypatch, _FakeTextEmbedding)
        constants = _install_fake_hf_hub(monkeypatch)
        embedding_service._load_model("/tmp/cache")
        assert constants.ENDPOINT == HF_MIRROR_ENDPOINT
        assert len(_FakeTextEmbedding.calls) == 2

    def test_both_attempts_fail_raises(
        self, monkeypatch: pytest.MonkeyPatch, _cache_dir: None
    ) -> None:
        _FakeTextEmbedding.calls = []
        _FakeTextEmbedding.failures_before_success = 99
        _install_fake_fastembed(monkeypatch, _FakeTextEmbedding)
        _install_fake_hf_hub(monkeypatch)
        with pytest.raises(EmbeddingUnavailableError, match="加载失败"):
            embedding_service._load_model("/tmp/cache")


class TestLocalEmbedder:
    def test_model_id_assertion(
        self, monkeypatch: pytest.MonkeyPatch, _cache_dir: None
    ) -> None:
        """加载到错误模型（如静默回退 en）必须显式失败，不能带病上岗。"""

        class _WrongModel(_FakeTextEmbedding):
            def __init__(self, **kwargs) -> None:
                super().__init__(**kwargs)
                self.model = types.SimpleNamespace(
                    model_name="BAAI/bge-small-en-v1.5"
                )

        _FakeTextEmbedding.failures_before_success = 0
        _install_fake_fastembed(monkeypatch, _WrongModel)
        with pytest.raises(EmbeddingUnavailableError, match="模型 id 不符"):
            LocalEmbedder()

    def test_embed_returns_float_list(
        self, monkeypatch: pytest.MonkeyPatch, _cache_dir: None
    ) -> None:
        _FakeTextEmbedding.failures_before_success = 0
        _install_fake_fastembed(monkeypatch, _FakeTextEmbedding)
        embedder = LocalEmbedder()
        vector = embedder.embed("岛屿生存建造")
        assert vector == [0.1, 0.2, 0.3]
        assert all(isinstance(v, float) for v in vector)


class TestSingleton:
    def test_lazy_singleton(
        self, monkeypatch: pytest.MonkeyPatch, _cache_dir: None
    ) -> None:
        load_count = 0

        class _Counting(_FakeTextEmbedding):
            def __init__(self, **kwargs) -> None:
                nonlocal load_count
                load_count += 1
                super().__init__(**kwargs)

        _FakeTextEmbedding.failures_before_success = 0
        _install_fake_fastembed(monkeypatch, _Counting)
        first = get_local_embedder()
        second = get_local_embedder()
        assert first is second
        assert load_count == 1

    def test_concurrent_init_runs_once(
        self, monkeypatch: pytest.MonkeyPatch, _cache_dir: None
    ) -> None:
        """初始化锁：pipeline 多线程并发下模型只加载一次。"""
        load_count = 0

        class _Counting(_FakeTextEmbedding):
            def __init__(self, **kwargs) -> None:
                nonlocal load_count
                load_count += 1
                super().__init__(**kwargs)

        _FakeTextEmbedding.failures_before_success = 0
        _install_fake_fastembed(monkeypatch, _Counting)
        threads = [threading.Thread(target=get_local_embedder) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        assert load_count == 1

    def test_reset_forces_reload(
        self, monkeypatch: pytest.MonkeyPatch, _cache_dir: None
    ) -> None:
        _FakeTextEmbedding.failures_before_success = 0
        _install_fake_fastembed(monkeypatch, _FakeTextEmbedding)
        first = get_local_embedder()
        reset_local_embedder()
        assert get_local_embedder() is not first


class TestCacheDirResolution:
    def test_env_var_wins(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(tmp_path / "env-cache"))
        assert embedding_service._resolve_cache_dir() == str(tmp_path / "env-cache")

    def test_existing_upload_dir(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("FASTEMBED_CACHE_PATH", raising=False)
        monkeypatch.setattr(
            embedding_service,
            "get_settings",
            lambda: Settings(upload_dir=str(tmp_path)),
        )
        resolved = embedding_service._resolve_cache_dir()
        assert resolved == str(tmp_path / ".cache" / "fastembed")
        assert Path(resolved).is_dir()

    def test_fallback_to_local_uploads(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("FASTEMBED_CACHE_PATH", raising=False)
        monkeypatch.setattr(
            embedding_service,
            "get_settings",
            lambda: Settings(upload_dir="/nonexistent/path uploads"),
        )
        monkeypatch.chdir(tmp_path)
        resolved = embedding_service._resolve_cache_dir()
        assert resolved == str(Path("uploads") / ".cache" / "fastembed")
        assert (tmp_path / "uploads" / ".cache" / "fastembed").is_dir()
