"""lab_diagnosis.run_diagnosis 编排测试（LLM 一律 mock，fixture 虚构名）。"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import AnalysisResult, CreativeAsset
from app.repositories.creative_diagnoses import CreativeDiagnosisRepository
from app.services import lab_diagnosis
from app.services.lab_prompts import LAB_PROMPT_VERSION
from app.services.settings import AIConfig


def _valid_output(evidence_label: str = "reference_only") -> dict:
    return {
        "verdict": "虚构素材：舰队被海盗阻断，用升级炮火解决；可迁移核心是损失重建。",
        "classification": {
            "motif": "舰队被海盗摧毁，用升级重建复仇",
            "hook_mechanism": "H5",
            "form_tags": ["engine_render"],
            "timeliness": "evergreen",
            "opening_3s_solution": "event_in_progress",
            "brand_implant_level": "endcard_only",
        },
        "structure": {
            "segments": [
                {"kind": "A", "start_s": 0.0, "end_s": 9.0, "summary": "舰队遇袭"},
                {"kind": "B", "start_s": 9.0, "end_s": 33.0, "summary": "重建反击"},
            ],
            "ab_bridge_mode": "theme_direct_link",
            "conflict_trigger": "海盗夜袭",
        },
        "content": {"setting": "虚构星海港口", "key_visuals": ["舰桥", "炮火"]},
        "emotion": {"primary_entry": "loss_rebuild", "emotion_curve": "先虐后爽"},
        "transferable_structure": {
            "fixed": ["损失重建循环"],
            "reskinnable": ["星海场景"],
            "do_not_copy": ["写实血腥"],
        },
        "selling_point_candidates": {
            "mappable_points": [],
            "spectrum_layer": "strong_direct",
        },
        "evidence_label": evidence_label,
        "uncertainties": [],
    }


def _seed_analyzed_asset(
    db: Session, filename: str, *, embedding: list[float] | None = None
) -> CreativeAsset:
    asset = CreativeAsset(
        id=str(uuid.uuid4()),
        filename=filename,
        file_type="video",
        storage_key=f"test/{uuid.uuid4()}",
        analysis_status="completed",
    )
    db.add(asset)
    db.flush()
    db.add(
        AnalysisResult(
            asset_id=asset.id,
            summary="虚构观察层：舰队重建素材",
            hook="舰队被袭",
            conflict="资源不足",
            gameplay="建造升级",
            reward="反击成功",
            characters=["指挥官"],
            environment=["星海"],
            emotion=["紧张"],
            tags=["星海", "建造"],
            variant_factors=["竖版"],
            creative_name="fake-fleet-rebuild",
            confidence=0.9,
            embedding=embedding,
        )
    )
    db.flush()
    return asset


@pytest.fixture()
def llm_mocks(monkeypatch):
    """complete_json / resolve_ai_config 全部 mock；calls 记录 LLM 调用次数。"""
    calls: list[dict] = []

    def _fake_complete_json(config, *, system, user, max_tokens, **_kw):  # noqa: ANN001, ANN202
        calls.append({"system": system, "user": user, "max_tokens": max_tokens})
        return _valid_output()

    monkeypatch.setattr(lab_diagnosis, "complete_json", _fake_complete_json)
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
    return calls


class TestSuccess:
    def test_persists_row_with_versions_and_snapshots(
        self, db_session: Session, llm_mocks: list
    ) -> None:
        asset = _seed_analyzed_asset(db_session, "FAKE-m1b-diag-ok.mp4")

        row = lab_diagnosis.run_diagnosis(
            db_session, Settings(), asset_id=asset.id, audience={"region": "FAKE"}
        )

        assert row.prompt_version == LAB_PROMPT_VERSION
        assert row.engine_version == "auto:fake-vision-v1"
        assert row.evidence_label == "reference_only"
        assert row.output["verdict"].startswith("虚构素材")
        assert row.observation_snapshot["summary"] == "虚构观察层：舰队重建素材"
        assert row.observation_snapshot["tags"] == ["星海", "建造"]
        assert row.audience_context == {"region": "FAKE", "lang": "zh"}
        assert row.product_facts_snapshot is None  # 未配置 product_facts
        assert len(llm_mocks) == 1
        assert llm_mocks[0]["max_tokens"] == lab_diagnosis.DIAGNOSIS_MAX_TOKENS
        # 落库可读回
        stored = CreativeDiagnosisRepository(db_session).get_current(
            asset.id, LAB_PROMPT_VERSION
        )
        assert stored is not None and stored.id == row.id


class TestRetry:
    def test_invalid_then_valid_retries_once(
        self, db_session: Session, monkeypatch, llm_mocks: list
    ) -> None:
        asset = _seed_analyzed_asset(db_session, "FAKE-m1b-diag-retry.mp4")
        responses = iter([{"verdict": "结构残缺"}, _valid_output()])
        calls: list[None] = []

        def _fake(*_a, **_k) -> dict:  # noqa: ANN202
            calls.append(None)
            return next(responses)

        monkeypatch.setattr(lab_diagnosis, "complete_json", _fake)

        row = lab_diagnosis.run_diagnosis(
            db_session, Settings(), asset_id=asset.id, audience=None
        )

        assert len(calls) == 2  # 首次非法 + 重试一次成功
        assert row.output["verdict"].startswith("虚构素材")

    def test_invalid_twice_raises_and_writes_nothing(
        self, db_session: Session, monkeypatch
    ) -> None:
        asset = _seed_analyzed_asset(db_session, "FAKE-m1b-diag-fail.mp4")
        calls: list[None] = []

        def _always_invalid(*_a, **_k) -> dict:  # noqa: ANN202
            calls.append(None)
            return {"verdict": "还是残缺"}

        monkeypatch.setattr(lab_diagnosis, "complete_json", _always_invalid)
        monkeypatch.setattr(
            lab_diagnosis,
            "resolve_ai_config",
            lambda _db, _s: AIConfig(
                api_key="sk-fake", base_url="", vision_model="fake-vision-v1",
                embedding_model="",
            ),
        )

        with pytest.raises(lab_diagnosis.LabDiagnosisFailedError):
            lab_diagnosis.run_diagnosis(
                db_session, Settings(), asset_id=asset.id, audience=None
            )

        assert len(calls) == 2  # 首次 + 重试一次
        assert (
            CreativeDiagnosisRepository(db_session).get_current(
                asset.id, LAB_PROMPT_VERSION
            )
            is None
        )

    def test_none_return_counts_as_failure_and_retries(
        self, db_session: Session, monkeypatch, llm_mocks: list
    ) -> None:
        asset = _seed_analyzed_asset(db_session, "FAKE-m1b-diag-none.mp4")
        responses = iter([None, _valid_output()])
        monkeypatch.setattr(
            lab_diagnosis, "complete_json", lambda *a, **k: next(responses)
        )

        row = lab_diagnosis.run_diagnosis(
            db_session, Settings(), asset_id=asset.id, audience=None
        )
        assert row.output["verdict"].startswith("虚构素材")


class TestEvidenceLabelClamp:
    def test_no_graph_evidence_forces_reference_only(
        self, db_session: Session, monkeypatch, llm_mocks: list
    ) -> None:
        # 无 embedding → graph_evidence None；模型自报 performance_supported 也钳掉
        asset = _seed_analyzed_asset(db_session, "FAKE-m1b-diag-clamp.mp4")
        monkeypatch.setattr(
            lab_diagnosis,
            "complete_json",
            lambda *a, **k: _valid_output("performance_supported"),
        )

        row = lab_diagnosis.run_diagnosis(
            db_session, Settings(), asset_id=asset.id, audience=None
        )

        assert row.output["evidence_label"] == "reference_only"
        assert row.evidence_label == "reference_only"

    def test_graph_evidence_present_keeps_model_label(
        self, db_session: Session, monkeypatch, llm_mocks: list
    ) -> None:
        # 本条与另一条素材 embedding 完全相同（余弦 1.0 ≥ 0.85）→ 有图证据
        asset = _seed_analyzed_asset(
            db_session, "FAKE-m1b-diag-graph-a.mp4", embedding=[1.0, 0.0, 0.0]
        )
        _seed_analyzed_asset(
            db_session, "FAKE-m1b-diag-graph-b.mp4", embedding=[1.0, 0.0, 0.0]
        )
        monkeypatch.setattr(
            lab_diagnosis,
            "complete_json",
            lambda *a, **k: _valid_output("graph_supported"),
        )

        row = lab_diagnosis.run_diagnosis(
            db_session, Settings(), asset_id=asset.id, audience=None
        )

        assert row.evidence_label == "graph_supported"


class TestGraphEvidence:
    def test_counts_only_similar_and_excludes_self(
        self, db_session: Session
    ) -> None:
        asset = _seed_analyzed_asset(
            db_session, "FAKE-m1b-ge-a.mp4", embedding=[1.0, 0.0]
        )
        _seed_analyzed_asset(db_session, "FAKE-m1b-ge-b.mp4", embedding=[1.0, 0.0])
        _seed_analyzed_asset(db_session, "FAKE-m1b-ge-c.mp4", embedding=[0.0, 1.0])
        from app.repositories.analysis import AnalysisRepository

        analysis = AnalysisRepository(db_session).get_by_asset(asset.id)
        assert analysis is not None
        evidence = lab_diagnosis.graph_evidence_for(db_session, analysis)
        assert evidence == {"similar_count": 1, "threshold": 0.85}

    def test_no_embedding_returns_none(self, db_session: Session) -> None:
        from app.repositories.analysis import AnalysisRepository

        plain = _seed_analyzed_asset(db_session, "FAKE-m1b-ge-none.mp4")
        analysis = AnalysisRepository(db_session).get_by_asset(plain.id)
        assert analysis is not None
        assert lab_diagnosis.graph_evidence_for(db_session, analysis) is None

    def test_no_candidates_returns_none(self) -> None:
        """库内无其它带 embedding 候选 → None（stub db，不依赖共享库为空）。"""

        class _EmptyScalars:
            def all(self) -> list:
                return []

        class _NoCandidateDb:
            def scalars(self, _stmt):  # noqa: ANN001, ANN202
                return _EmptyScalars()

        analysis = AnalysisResult(asset_id="fake-solo", embedding=[1.0, 0.0])
        assert (
            lab_diagnosis.graph_evidence_for(_NoCandidateDb(), analysis) is None  # type: ignore[arg-type]
        )


class TestDiagnosisLang:
    """诊断输出语言参数：lang=en 换英文第 8 条铁律，其余逐字不变。"""

    def test_lang_en_switches_rule8_to_english(
        self, db_session: Session, llm_mocks: list
    ) -> None:
        asset = _seed_analyzed_asset(db_session, "FAKE-m1b-lang-en.mp4")

        row = lab_diagnosis.run_diagnosis(
            db_session, Settings(), asset_id=asset.id, audience=None, lang="en"
        )

        assert len(llm_mocks) == 1
        system = llm_mocks[0]["system"]
        # 英文第 8 条在、中文第 8 条不在
        assert "8. Output language: except enum ids, write every field value in English" in system
        assert "8. 输出语言" not in system
        # 无受众时快照也至少留 lang
        assert row.audience_context == {"lang": "en"}

    def test_default_lang_keeps_chinese_rule8(
        self, db_session: Session, llm_mocks: list
    ) -> None:
        asset = _seed_analyzed_asset(db_session, "FAKE-m1b-lang-zh.mp4")

        row = lab_diagnosis.run_diagnosis(
            db_session, Settings(), asset_id=asset.id, audience={"region": "FAKE"}
        )

        system = llm_mocks[0]["system"]
        assert "8. 输出语言：除枚举 id 外，全部字段一律使用简体中文" in system
        assert "8. Output language" not in system
        # lang 并入受众快照，不覆盖既有受众字段
        assert row.audience_context == {"region": "FAKE", "lang": "zh"}


class TestPreconditions:
    def test_missing_asset_raises_not_found(self, db_session: Session) -> None:
        with pytest.raises(lab_diagnosis.LabAssetNotFoundError):
            lab_diagnosis.run_diagnosis(
                db_session, Settings(), asset_id=str(uuid.uuid4()), audience=None
            )

    def test_pending_asset_raises_not_ready(self, db_session: Session) -> None:
        asset = CreativeAsset(
            id=str(uuid.uuid4()),
            filename="FAKE-m1b-diag-pending.mp4",
            file_type="video",
            storage_key=f"test/{uuid.uuid4()}",
            analysis_status="pending",
        )
        db_session.add(asset)
        db_session.flush()
        with pytest.raises(lab_diagnosis.LabObservationNotReadyError):
            lab_diagnosis.run_diagnosis(
                db_session, Settings(), asset_id=asset.id, audience=None
            )

    def test_completed_status_without_analysis_row_raises_not_ready(
        self, db_session: Session
    ) -> None:
        asset = CreativeAsset(
            id=str(uuid.uuid4()),
            filename="FAKE-m1b-diag-orphan.mp4",
            file_type="video",
            storage_key=f"test/{uuid.uuid4()}",
            analysis_status="completed",
        )
        db_session.add(asset)
        db_session.flush()
        with pytest.raises(lab_diagnosis.LabObservationNotReadyError):
            lab_diagnosis.run_diagnosis(
                db_session, Settings(), asset_id=asset.id, audience=None
            )

    def test_missing_ai_key_raises_human_message(
        self, db_session: Session, monkeypatch
    ) -> None:
        asset = _seed_analyzed_asset(db_session, "FAKE-m1b-diag-nokey.mp4")
        monkeypatch.setattr(
            lab_diagnosis,
            "resolve_ai_config",
            lambda _db, _s: AIConfig(
                api_key="", base_url="", vision_model="fake-vision-v1",
                embedding_model="",
            ),
        )
        with pytest.raises(lab_diagnosis.LabAIKeyMissingError) as excinfo:
            lab_diagnosis.run_diagnosis(
                db_session, Settings(), asset_id=asset.id, audience=None
            )
        assert "Settings" in str(excinfo.value)
