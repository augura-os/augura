from __future__ import annotations

import pytest

from app.config import Settings
from app.services import graph_sync


def test_real_neo4j_is_blocked_by_default() -> None:
    with pytest.raises(RuntimeError, match="Neo4j is disabled in tests"):
        graph_sync.get_graph_repository(Settings())
