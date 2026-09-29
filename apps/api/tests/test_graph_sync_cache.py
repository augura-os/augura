"""graph_sync.read_similar_pairs_cached 的 TTL 缓存行为。

SIMILAR_TO 读取单次实测 ~2.3s（Neo4j 往返），缓存必须满足：
1. TTL 内重复调用只查一次底层 repo；
2. invalidate 后立即重查；
3. TTL 过期后自动重查。
"""
from __future__ import annotations

from app.config import get_settings
from app.services import graph_sync


class _CountingRepo:
    def __init__(self) -> None:
        self.calls = 0

    def read_similar_pairs(self) -> list[tuple[str, str]]:
        self.calls += 1
        return [("c-a", "c-b")]


def _stub_repo(monkeypatch) -> _CountingRepo:
    repo = _CountingRepo()
    monkeypatch.setattr(graph_sync, "get_graph_repository", lambda _settings: repo)
    graph_sync.invalidate_similar_pairs_cache()
    return repo


def test_repeated_calls_hit_repo_once(monkeypatch) -> None:
    repo = _stub_repo(monkeypatch)
    settings = get_settings()
    assert graph_sync.read_similar_pairs_cached(settings) == [("c-a", "c-b")]
    assert graph_sync.read_similar_pairs_cached(settings) == [("c-a", "c-b")]
    assert repo.calls == 1


def test_invalidate_forces_refetch(monkeypatch) -> None:
    repo = _stub_repo(monkeypatch)
    settings = get_settings()
    graph_sync.read_similar_pairs_cached(settings)
    graph_sync.invalidate_similar_pairs_cache()
    graph_sync.read_similar_pairs_cached(settings)
    assert repo.calls == 2


def test_ttl_expiry_forces_refetch(monkeypatch) -> None:
    repo = _stub_repo(monkeypatch)
    settings = get_settings()
    graph_sync.read_similar_pairs_cached(settings)
    monkeypatch.setattr(graph_sync, "_SIMILAR_PAIRS_TTL_SECONDS", -1.0)
    graph_sync.read_similar_pairs_cached(settings)
    assert repo.calls == 2
