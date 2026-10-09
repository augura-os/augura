"""graph_sync.reconcile_graph 的启动对账行为（Neo4j 幽灵节点/边自愈）。

Neo4j 侧漂移有两个方向：幽灵节点/边（PG 已删但 Neo4j 残留，/graph
优先读 Neo4j 会直接渲染成重复节点）和缺失边（Neo4j 不可达期间的
dna 归属没同步上）。对账必须满足：
1. 五类节点都按 PG 有效 id 全集调用 prune_ghost_nodes；
2. HAS_CREATIVE 严格按 PG creatives.dna_id 归属重建；
3. 孤儿 Tag 修剪被调用；
4. Neo4j 不可用时静默通过（lifespan 不被炸掉）。
"""
from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Creative, CreativeDNA
from app.services import graph_sync


class _StubRepo:
    def __init__(self) -> None:
        self.pruned: list[tuple[str, list[str]]] = []
        self.replaced: list[tuple[str, str]] | None = None
        self.orphan_tags_pruned = 0

    def prune_ghost_nodes(self, node_type: str, valid_ref_ids: list[str]) -> int:
        self.pruned.append((node_type, sorted(valid_ref_ids)))
        return 1

    def replace_has_creative_edges(self, assignments: list[tuple[str, str]]) -> int:
        self.replaced = sorted(assignments)
        return len(self.replaced)

    def prune_orphan_tags(self) -> None:
        self.orphan_tags_pruned += 1


class _BoomRepo:
    def prune_ghost_nodes(self, node_type: str, valid_ref_ids: list[str]) -> int:
        raise RuntimeError("neo4j unreachable")


def _mk_dna(db: Session, code: str, name: str) -> CreativeDNA:
    dna = CreativeDNA(id=str(uuid.uuid4()), code=code, name=name)
    db.add(dna)
    db.flush()
    return dna


def _mk_creative(db: Session, name: str, dna_id: str | None = None) -> Creative:
    creative = Creative(id=str(uuid.uuid4()), name=name, dna_id=dna_id)
    db.add(creative)
    db.flush()
    return creative


def test_reconcile_prunes_with_pg_ids_and_rebuilds_edges(
    db_session: Session, monkeypatch
) -> None:
    repo = _StubRepo()
    monkeypatch.setattr(graph_sync, "get_graph_repository", lambda _settings: repo)
    dna = _mk_dna(db_session, "D91", "对账家族甲")
    assigned = _mk_creative(db_session, "对账素材甲", dna_id=dna.id)
    unassigned = _mk_creative(db_session, "对账素材乙")

    graph_sync.reconcile_graph(db_session, get_settings())

    pruned = dict(repo.pruned)
    assert set(pruned) == {"dna", "creative", "variant", "asset", "tag"}
    assert pruned["dna"] == [dna.id]
    assert pruned["creative"] == sorted([assigned.id, unassigned.id])
    assert pruned["variant"] == []
    assert pruned["asset"] == []
    assert pruned["tag"] == []
    assert repo.replaced == [(dna.id, assigned.id)]
    assert repo.orphan_tags_pruned == 1


def test_reconcile_empty_database(db_session: Session, monkeypatch) -> None:
    repo = _StubRepo()
    monkeypatch.setattr(graph_sync, "get_graph_repository", lambda _settings: repo)

    graph_sync.reconcile_graph(db_session, get_settings())

    assert dict(repo.pruned) == {
        "dna": [],
        "creative": [],
        "variant": [],
        "asset": [],
        "tag": [],
    }
    assert repo.replaced == []
    assert repo.orphan_tags_pruned == 1


def test_reconcile_survives_neo4j_down(db_session: Session, monkeypatch) -> None:
    monkeypatch.setattr(graph_sync, "get_graph_repository", lambda _s: _BoomRepo())
    _mk_creative(db_session, "对账素材丙")

    graph_sync.reconcile_graph(db_session, get_settings())  # must not raise
