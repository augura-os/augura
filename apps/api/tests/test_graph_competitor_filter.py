"""GET /graph 的竞品参考子图过滤（include_competitor 开关）。

Lab 竞品参考素材（sticky 标签 competitor-reference）与自有素材同走
ingest 管线进图，但只服务诊断对照，默认视图不渲染：剔除竞品
asset/variant 节点与相连边、variant 被删光的空壳 creative、孤儿
competitor-reference Tag 节点；?include_competitor=true 全量返回。
测试环境无 Neo4j：stub 掉 read_graph 走 SQL 镜像回退路径（两条读取
路径在 _current_graph 出口汇聚，过滤逻辑共用）。
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Creative, CreativeAsset, CreativeVariant
from app.repositories.graph_mirror import rebuild_mirror
from app.repositories.tags import TagRepository
from app.services import graph_sync


@pytest.fixture()
def api_client(db_session: Session, tmp_path: Path) -> TestClient:
    from app.api.deps import get_db
    from app.config import get_settings
    from app.main import app

    def _db():  # noqa: ANN202
        yield db_session

    settings = get_settings().model_copy(update={"upload_dir": str(tmp_path)})
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


class _NoNeo4jRepo:
    """read_graph 直接失败 → 路由回退 SQL 镜像（测试环境无 Neo4j）。"""

    def read_graph(self) -> dict:  # noqa: ANN202
        raise RuntimeError("neo4j unreachable")


@pytest.fixture()
def mirror_fallback(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        graph_sync, "get_graph_repository", lambda _settings: _NoNeo4jRepo()
    )


def _new_asset(db: Session, filename: str) -> CreativeAsset:
    asset = CreativeAsset(
        id=str(uuid.uuid4()),
        filename=filename,
        file_type="video",
        storage_key=f"test/{uuid.uuid4()}",
    )
    db.add(asset)
    db.flush()
    return asset


def _add_variant(
    db: Session, creative: Creative, name: str, *, competitor: bool = False
) -> tuple[CreativeVariant, CreativeAsset]:
    asset = _new_asset(db, f"{name}.mp4")
    variant = CreativeVariant(
        id=str(uuid.uuid4()), creative_id=creative.id, asset_id=asset.id, name=name
    )
    db.add(variant)
    db.flush()
    if competitor:
        TagRepository(db).set_asset_tags(asset.id, ["competitor-reference"])
    return variant, asset


def _seed_family(
    db: Session, name: str, *, competitor: bool = False
) -> tuple[Creative, CreativeVariant, CreativeAsset]:
    creative = Creative(id=str(uuid.uuid4()), name=name)
    db.add(creative)
    db.flush()
    variant, asset = _add_variant(db, creative, name, competitor=competitor)
    return creative, variant, asset


def _graph_node_ids(data: dict) -> set[str]:
    return {node["id"] for node in data["nodes"]}


class TestCompetitorFilter:
    def test_default_hides_competitor_subgraph(
        self, api_client: TestClient, db_session: Session, mirror_fallback: None
    ) -> None:
        own_creative, own_variant, own_asset = _seed_family(
            db_session, "FAKE-graph-own"
        )
        comp_creative, comp_variant, comp_asset = _seed_family(
            db_session, "FAKE-graph-comp", competitor=True
        )
        rebuild_mirror(db_session)

        response = api_client.get("/graph")

        assert response.status_code == 200
        data = response.json()["data"]
        node_ids = _graph_node_ids(data)
        # 自有全家在
        assert f"creative:{own_creative.id}" in node_ids
        assert f"variant:{own_variant.id}" in node_ids
        assert f"asset:{own_asset.id}" in node_ids
        # 竞品 creative/variant/asset 节点都不在
        assert f"creative:{comp_creative.id}" not in node_ids
        assert f"variant:{comp_variant.id}" not in node_ids
        assert f"asset:{comp_asset.id}" not in node_ids
        # 孤儿 competitor-reference Tag 节点也被剔除
        assert all(
            node["label"] != "competitor-reference" for node in data["nodes"]
        )
        # 没有指向已删节点的悬挂边
        assert all(
            edge["source"] in node_ids and edge["target"] in node_ids
            for edge in data["edges"]
        )

    def test_include_competitor_returns_full_graph(
        self, api_client: TestClient, db_session: Session, mirror_fallback: None
    ) -> None:
        own_creative, _, _ = _seed_family(db_session, "FAKE-graph-own-full")
        comp_creative, comp_variant, comp_asset = _seed_family(
            db_session, "FAKE-graph-comp-full", competitor=True
        )
        rebuild_mirror(db_session)

        response = api_client.get("/graph?include_competitor=true")

        assert response.status_code == 200
        data = response.json()["data"]
        node_ids = _graph_node_ids(data)
        assert f"creative:{own_creative.id}" in node_ids
        assert f"creative:{comp_creative.id}" in node_ids
        assert f"variant:{comp_variant.id}" in node_ids
        assert f"asset:{comp_asset.id}" in node_ids
        # 竞品 Tag 节点与 HAS_TAG 边都在
        tag_nodes = [
            node
            for node in data["nodes"]
            if node["type"] == "tag" and node["label"] == "competitor-reference"
        ]
        assert len(tag_nodes) == 1
        assert any(
            edge["type"] == "HAS_TAG"
            and edge["source"] == f"asset:{comp_asset.id}"
            and edge["target"] == tag_nodes[0]["id"]
            for edge in data["edges"]
        )

    def test_mixed_family_keeps_creative_and_own_variant(
        self, api_client: TestClient, db_session: Session, mirror_fallback: None
    ) -> None:
        # 混族（历史数据）：同一 creative 下自有 + 竞品 variant 并存，
        # 过滤后 creative 与自有 variant 保留，竞品 variant/asset 剔除
        creative = Creative(id=str(uuid.uuid4()), name="FAKE-graph-mixed")
        db_session.add(creative)
        db_session.flush()
        own_variant, own_asset = _add_variant(db_session, creative, "own-part")
        comp_variant, comp_asset = _add_variant(
            db_session, creative, "comp-part", competitor=True
        )
        rebuild_mirror(db_session)

        response = api_client.get("/graph")

        assert response.status_code == 200
        data = response.json()["data"]
        node_ids = _graph_node_ids(data)
        assert f"creative:{creative.id}" in node_ids
        assert f"variant:{own_variant.id}" in node_ids
        assert f"asset:{own_asset.id}" in node_ids
        assert f"variant:{comp_variant.id}" not in node_ids
        assert f"asset:{comp_asset.id}" not in node_ids

    def test_no_competitor_data_is_noop(
        self, api_client: TestClient, db_session: Session, mirror_fallback: None
    ) -> None:
        # 库内无竞品参考：默认视图原样返回（过滤整体跳过）
        own_creative, own_variant, own_asset = _seed_family(
            db_session, "FAKE-graph-plain"
        )
        rebuild_mirror(db_session)

        response = api_client.get("/graph")

        assert response.status_code == 200
        node_ids = _graph_node_ids(response.json()["data"])
        assert f"creative:{own_creative.id}" in node_ids
        assert f"variant:{own_variant.id}" in node_ids
        assert f"asset:{own_asset.id}" in node_ids
