"""Creative Lab 三端点路由测试（TestClient；LLM mock；fixture 虚构名）。"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import AnalysisResult, CreativeAsset
from app.repositories.creative_diagnoses import CreativeDiagnosisRepository
from app.repositories.tags import TagRepository
from app.services import lab_diagnosis
from app.services.lab_prompts import LAB_PROMPT_VERSION
from app.services.settings import AIConfig


@pytest.fixture()
def api_client(db_session: Session, tmp_path: Path) -> TestClient:
    from app.api.deps import get_db, get_storage
    from app.config import get_settings
    from app.main import app

    class _FakeStorage:
        def put_bytes(self, *_args) -> None:  # noqa: ANN002
            pass

    def _db():  # noqa: ANN202
        yield db_session

    settings = get_settings().model_copy(update={"upload_dir": str(tmp_path)})
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_storage] = lambda: _FakeStorage()
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def _valid_output() -> dict:
    return {
        "verdict": "虚构素材：灯塔被风暴阻断，用加固重建解决。",
        "classification": {
            "motif": "灯塔被风暴摧毁，用加固重建复仇",
            "hook_mechanism": "H5",
            "form_tags": ["engine_render"],
            "timeliness": "evergreen",
            "opening_3s_solution": "event_in_progress",
            "brand_implant_level": "endcard_only",
        },
        "structure": {
            "segments": [
                {"kind": "A", "start_s": 0.0, "end_s": 9.0, "summary": "风暴来袭"},
                {"kind": "B", "start_s": 9.0, "end_s": 33.0, "summary": "重建点灯"},
            ],
            "ab_bridge_mode": "theme_direct_link",
            "conflict_trigger": "风暴夜袭",
        },
        "content": {"setting": "虚构海岸", "key_visuals": ["灯塔", "海浪"]},
        "emotion": {"primary_entry": "loss_rebuild", "emotion_curve": "先虐后爽"},
        "transferable_structure": {
            "fixed": ["损失重建循环"],
            "reskinnable": ["海岸场景"],
            "do_not_copy": ["写实血腥"],
        },
        "selling_point_candidates": {
            "mappable_points": [],
            "spectrum_layer": "strong_direct",
        },
        "evidence_label": "reference_only",
        "uncertainties": [],
    }


def _seed_asset(
    db: Session, filename: str, status: str = "pending"
) -> CreativeAsset:
    asset = CreativeAsset(
        id=str(uuid.uuid4()),
        filename=filename,
        file_type="video",
        storage_key=f"test/{uuid.uuid4()}",
        analysis_status=status,
    )
    db.add(asset)
    db.flush()
    return asset


def _seed_completed_with_analysis(db: Session, filename: str) -> CreativeAsset:
    asset = _seed_asset(db, filename, "completed")
    db.add(
        AnalysisResult(
            asset_id=asset.id,
            summary="虚构观察层：灯塔重建素材",
            hook="灯塔被毁",
            conflict="风暴再临",
            gameplay="加固建造",
            reward="重新点灯",
            characters=["守塔人"],
            environment=["海岸"],
            emotion=["紧张"],
            tags=["海岸", "建造"],
            variant_factors=["竖版"],
            creative_name="fake-lighthouse-rebuild",
            confidence=0.9,
        )
    )
    db.flush()
    return asset


class TestUploadReferences:
    def test_upload_marks_competitor_reference_tag(
        self, api_client: TestClient, db_session: Session
    ) -> None:
        response = api_client.post(
            "/lab/references",
            files=[("files", ("FAKE-m1b-ref-a.mp4", b"\x00fake", "video/mp4"))],
        )
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert len(body["data"]["uploaded"]) == 1
        asset_id = body["data"]["uploaded"][0]["id"]
        # 响应形状照 /upload
        assert body["data"]["uploaded"][0]["analysis_status"] == "pending"
        assert body["data"]["skipped"] == []
        assert body["data"]["warnings"] == []

        names = [tag.name for tag in TagRepository(db_session).get_asset_tags(asset_id)]
        assert "competitor-reference" in names

    def test_upload_duplicate_skipped(
        self, api_client: TestClient, db_session: Session
    ) -> None:
        _seed_asset(db_session, "FAKE-m1b-ref-dup.mp4")
        response = api_client.post(
            "/lab/references",
            files=[("files", ("FAKE-m1b-ref-dup.mp4", b"\x00fake", "video/mp4"))],
        )
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["data"]["uploaded"] == []
        assert len(body["data"]["skipped"]) == 1
        assert body["data"]["skipped"][0]["code"] == "upload_duplicate"


class TestGetLabAnalysis:
    def test_missing_asset_404(self, api_client: TestClient) -> None:
        response = api_client.get(f"/lab/analysis/{uuid.uuid4()}")
        assert response.status_code == 404
        body = response.json()
        assert body["success"] is False
        assert body["code"] == "asset_not_found"

    def test_view_shape_without_diagnosis(
        self, api_client: TestClient, db_session: Session
    ) -> None:
        asset = _seed_completed_with_analysis(db_session, "FAKE-m1b-view.mp4")
        response = api_client.get(f"/lab/analysis/{asset.id}")
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        data = body["data"]
        assert data["asset_id"] == asset.id
        assert data["analysis_status"] == "completed"
        assert data["observation"]["summary"] == "虚构观察层：灯塔重建素材"
        assert data["observation"]["tags"] == ["海岸", "建造"]
        assert data["diagnosis"] is None
        assert data["diagnosis_stale"] is False
        assert data["graph_evidence"] is None
        assert data["product_facts"] == {}

    def test_pending_asset_observation_null(
        self, api_client: TestClient, db_session: Session
    ) -> None:
        asset = _seed_asset(db_session, "FAKE-m1b-view-pending.mp4", "pending")
        response = api_client.get(f"/lab/analysis/{asset.id}")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["analysis_status"] == "pending"
        assert data["observation"] is None


@pytest.fixture()
def llm_mocks(monkeypatch) -> None:
    monkeypatch.setattr(
        lab_diagnosis, "complete_json", lambda *a, **k: _valid_output()
    )
    monkeypatch.setattr(
        lab_diagnosis,
        "resolve_ai_config",
        lambda _db, _s: AIConfig(
            api_key="sk-fake",
            base_url="",
            vision_model="fake-vision-v1",
            embedding_model="",
        ),
    )


class TestCreateDiagnosis:
    def test_observation_not_ready_409(
        self, api_client: TestClient, db_session: Session
    ) -> None:
        asset = _seed_asset(db_session, "FAKE-m1b-diag-pending.mp4", "pending")
        response = api_client.post("/lab/diagnoses", json={"asset_id": asset.id})
        assert response.status_code == 409
        body = response.json()
        assert body["success"] is False
        assert body["code"] == "lab_observation_not_ready"

    def test_missing_asset_404(self, api_client: TestClient) -> None:
        response = api_client.post("/lab/diagnoses", json={"asset_id": str(uuid.uuid4())})
        assert response.status_code == 404
        assert response.json()["code"] == "asset_not_found"

    def test_success_returns_persisted_diagnosis(
        self, api_client: TestClient, db_session: Session, llm_mocks: None
    ) -> None:
        asset = _seed_completed_with_analysis(db_session, "FAKE-m1b-diag-ok.mp4")
        response = api_client.post(
            "/lab/diagnoses",
            json={"asset_id": asset.id, "audience": {"region": "FAKE", "age": "18-24"}},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        data = body["data"]
        assert data["asset_id"] == asset.id
        assert data["prompt_version"] == LAB_PROMPT_VERSION
        assert data["engine_version"] == "auto:fake-vision-v1"
        assert data["evidence_label"] == "reference_only"
        assert data["output"]["verdict"].startswith("虚构素材")
        assert data["created_at"]

        stored = CreativeDiagnosisRepository(db_session).get_current(
            asset.id, LAB_PROMPT_VERSION
        )
        assert stored is not None
        assert stored.audience_context == {"region": "FAKE", "age": "18-24", "lang": "zh"}


def _capture_llm(monkeypatch) -> list[str]:  # noqa: ANN001, ANN202
    """mock LLM 并捕获每次调用的 system prompt（语言断言用）。"""
    systems: list[str] = []

    def _fake(_config, *, system, **_kw):  # noqa: ANN001, ANN202
        systems.append(system)
        return _valid_output()

    monkeypatch.setattr(lab_diagnosis, "complete_json", _fake)
    monkeypatch.setattr(
        lab_diagnosis,
        "resolve_ai_config",
        lambda _db, _s: AIConfig(
            api_key="sk-fake",
            base_url="",
            vision_model="fake-vision-v1",
            embedding_model="",
        ),
    )
    return systems


class TestDiagnosisLang:
    """POST /lab/diagnoses 的 lang 参数全链路：schema → service → prompt。"""

    def test_lang_en_sends_english_rule8(
        self, api_client: TestClient, db_session: Session, monkeypatch
    ) -> None:
        asset = _seed_completed_with_analysis(db_session, "FAKE-m1b-lang-en.mp4")
        systems = _capture_llm(monkeypatch)

        response = api_client.post(
            "/lab/diagnoses", json={"asset_id": asset.id, "lang": "en"}
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["lang"] == "en"
        assert len(systems) == 1
        # mock LLM 收到的 system prompt：英文第 8 条在、中文第 8 条不在
        assert "8. Output language: except enum ids" in systems[0]
        assert "8. 输出语言" not in systems[0]
        stored = CreativeDiagnosisRepository(db_session).get_current(
            asset.id, LAB_PROMPT_VERSION
        )
        assert stored is not None
        assert stored.audience_context == {"lang": "en"}

    def test_default_lang_keeps_chinese_rule8(
        self, api_client: TestClient, db_session: Session, monkeypatch
    ) -> None:
        asset = _seed_completed_with_analysis(db_session, "FAKE-m1b-lang-zh.mp4")
        systems = _capture_llm(monkeypatch)

        response = api_client.post("/lab/diagnoses", json={"asset_id": asset.id})

        assert response.status_code == 200
        assert response.json()["data"]["lang"] == "zh"
        assert len(systems) == 1
        assert "8. 输出语言：除枚举 id 外，全部字段一律使用简体中文" in systems[0]
        assert "8. Output language" not in systems[0]

    def test_invalid_lang_rejected_400(
        self, api_client: TestClient, db_session: Session
    ) -> None:
        # 词表外语言：Literal 校验失败，全局 RequestValidationError 映 400
        asset = _seed_completed_with_analysis(db_session, "FAKE-m1b-lang-bad.mp4")
        response = api_client.post(
            "/lab/diagnoses", json={"asset_id": asset.id, "lang": "fr"}
        )
        assert response.status_code == 400
        assert response.json()["code"] == "validation_error"


class TestAnalysisViewDiagnosisLang:
    """GET /lab/analysis 的 diagnosis.lang：从 audience_context 快照回填。"""

    def _seed_diagnosis(
        self, db: Session, asset_id: str, audience_context: dict | None
    ) -> None:
        CreativeDiagnosisRepository(db).upsert(
            asset_id,
            prompt_version=LAB_PROMPT_VERSION,
            observation_snapshot={"summary": "虚构观察层"},
            output=_valid_output(),
            evidence_label="reference_only",
            engine_version="auto:fake-vision-v1",
            audience_context=audience_context,
        )

    def test_en_snapshot_returns_en(
        self, api_client: TestClient, db_session: Session
    ) -> None:
        asset = _seed_completed_with_analysis(db_session, "FAKE-m1b-view-lang-en.mp4")
        self._seed_diagnosis(db_session, asset.id, {"lang": "en"})

        response = api_client.get(f"/lab/analysis/{asset.id}")

        assert response.status_code == 200
        assert response.json()["data"]["diagnosis"]["lang"] == "en"

    def test_legacy_row_without_lang_defaults_zh(
        self, api_client: TestClient, db_session: Session
    ) -> None:
        # 旧行：audience_context 有受众但无 lang 键
        asset = _seed_completed_with_analysis(db_session, "FAKE-m1b-view-lang-old.mp4")
        self._seed_diagnosis(db_session, asset.id, {"region": "FAKE"})

        response = api_client.get(f"/lab/analysis/{asset.id}")

        assert response.status_code == 200
        assert response.json()["data"]["diagnosis"]["lang"] == "zh"

    def test_legacy_row_without_audience_defaults_zh(
        self, api_client: TestClient, db_session: Session
    ) -> None:
        # 旧行：audience_context 整列为 NULL
        asset = _seed_completed_with_analysis(db_session, "FAKE-m1b-view-lang-null.mp4")
        self._seed_diagnosis(db_session, asset.id, None)

        response = api_client.get(f"/lab/analysis/{asset.id}")

        assert response.status_code == 200
        assert response.json()["data"]["diagnosis"]["lang"] == "zh"
