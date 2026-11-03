from __future__ import annotations

from app import main
from app.services import graph_sync


class _Session:
    def __enter__(self):
        return object()

    def __exit__(self, *_args):
        return None


def _stub_session(monkeypatch) -> None:
    from app import database

    monkeypatch.setattr(database, "SessionLocal", lambda: _Session())


def test_reconcile_retries_until_graph_is_ready(monkeypatch) -> None:
    _stub_session(monkeypatch)
    outcomes = iter([False, True])
    calls = []
    sleeps = []

    def reconcile(_db, _settings) -> bool:
        calls.append(1)
        return next(outcomes)

    monkeypatch.setattr(graph_sync, "reconcile_graph", reconcile)

    result = main._reconcile_graph_bg(retry_delays=(1, 2), sleep=sleeps.append)

    assert result is True
    assert len(calls) == 2
    assert sleeps == [1]


def test_reconcile_stops_after_bounded_failures(monkeypatch) -> None:
    _stub_session(monkeypatch)
    calls = []
    sleeps = []

    def reconcile(_db, _settings) -> bool:
        calls.append(1)
        return False

    monkeypatch.setattr(graph_sync, "reconcile_graph", reconcile)

    result = main._reconcile_graph_bg(retry_delays=(1, 2), sleep=sleeps.append)

    assert result is False
    assert len(calls) == 3
    assert sleeps == [1, 2]
