"""GET /review/queue 合并候选分页（P0b）：merge_limit 生效、上限 100、
merge_candidates_total 为三通道合计去重后的总对数（带内全部，不截断）、
不传参默认 20 条不变。GET 纯净：只读计算，无任何写操作。"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.routes.review import review_queue
from app.models import Creative, CreativeAsset, CreativeVariant
from app.services.review import merge_candidates_page


def _seed(db: Session, *, name: str, filename: str) -> Creative:
    creative = Creative(id=str(uuid.uuid4()), name=name)
    asset = CreativeAsset(
        id=str(uuid.uuid4()), filename=filename, file_type="video",
        storage_key=f"test/{uuid.uuid4()}",
    )
    db.add_all([creative, asset])
    db.flush()
    db.add(
        CreativeVariant(
            id=str(uuid.uuid4()), creative_id=creative.id,
            asset_id=asset.id, name="v1",
        )
    )
    db.flush()
    return creative


def _seed_borderline_pairs(db: Session, count: int) -> None:
    """造 count 对边界带候选：对内 Jaccard = 3/11 ≈ 0.27（带内），
    跨对共享仅 {red, blue} = 2/12 ≈ 0.17（带外），互不污染。"""
    for i in range(count):
        _seed(
            db, name=f"pair{i}-red-blue-k1-k2-k3-k4",
            filename=f"KS_EN-p{i}-a.mp4",
        )
        _seed(
            db, name=f"pair{i}-red-blue-m1-m2-m3-m4",
            filename=f"KS_EN-p{i}-b.mp4",
        )


class TestMergeCandidatesPage:
    def test_total_counts_all_in_band(self, db_session: Session) -> None:
        _seed_borderline_pairs(db_session, 3)
        items, total = merge_candidates_page(db_session, limit=20)
        assert total == 3
        assert len(items) == 3
        assert all(item.reason_code == "merge_similarity" for item in items)

    def test_limit_slices_total_unchanged(self, db_session: Session) -> None:
        _seed_borderline_pairs(db_session, 5)
        items, total = merge_candidates_page(db_session, limit=2)
        assert len(items) == 2
        assert total == 5

    def test_default_limit_is_20(self, db_session: Session) -> None:
        _seed_borderline_pairs(db_session, 25)
        items, total = merge_candidates_page(db_session, limit=20)
        assert len(items) == 20
        assert total == 25

    def test_route_default_unchanged(self, db_session: Session) -> None:
        # 不传参 = 原 20 对
        _seed_borderline_pairs(db_session, 25)
        result = review_queue(db=db_session)
        assert result.data is not None
        assert len(result.data.merge_candidates) == 20
        assert result.data.merge_candidates_total == 25

    def test_route_merge_limit_and_total(self, db_session: Session) -> None:
        _seed_borderline_pairs(db_session, 3)
        result = review_queue(db=db_session, merge_limit=2)
        assert result.data is not None
        assert result.data.merge_candidates_total == 3
        assert len(result.data.merge_candidates) == 2
        assert result.data.merge_candidates_total >= len(
            result.data.merge_candidates
        )


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


class TestMergeLimitHttp:
    def test_query_param_slicing(
        self, api_client: TestClient, db_session: Session
    ) -> None:
        _seed_borderline_pairs(db_session, 3)
        response = api_client.get("/review/queue", params={"merge_limit": 2})
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["merge_candidates_total"] == 3
        assert len(data["merge_candidates"]) == 2

    def test_over_max_limit_rejected(self, api_client: TestClient) -> None:
        # 上限 100：超出走统一参数错误封装（400 validation_error）
        response = api_client.get("/review/queue", params={"merge_limit": 101})
        assert response.status_code == 400
