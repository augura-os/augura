"""CreativeDiagnosisRepository：定性诊断行的幂等 upsert 与读取（test DB）。"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import CreativeAsset, CreativeDiagnosis
from app.repositories.creative_diagnoses import CreativeDiagnosisRepository


def _make_asset(db: Session) -> CreativeAsset:
    asset = CreativeAsset(
        id=str(uuid.uuid4()),
        filename="FAKE-m1a-diagnosis.mp4",
        file_type="video",
        storage_key=f"test/{uuid.uuid4()}",
    )
    db.add(asset)
    db.flush()
    return asset


def _observation(summary: str = "虚构观察层：机甲合成素材") -> dict[str, object]:
    return {"summary": summary, "tags": ["机甲", "合成"]}


def _output(verdict: str) -> dict[str, object]:
    return {"verdict": verdict, "classification": {"motif": "资源被阻断，用合成解决"}}


class TestUpsert:
    def test_insert_then_conflict_overwrites(self, db_session: Session) -> None:
        asset = _make_asset(db_session)
        repo = CreativeDiagnosisRepository(db_session)
        first = repo.upsert(
            asset.id,
            prompt_version="lab-v0.1.0",
            lang="zh",
            observation_snapshot=_observation(),
            output=_output("初版结论"),
            evidence_label="reference_only",
            engine_version="auto:fake-engine-1",
            product_facts_snapshot={"core_loop": "合成部件组装机甲"},
        )
        assert first.id
        second = repo.upsert(
            asset.id,
            prompt_version="lab-v0.1.0",
            lang="zh",
            observation_snapshot=_observation("更新后的观察层"),
            output=_output("重诊断结论"),
            evidence_label="graph_supported",
            engine_version="auto:fake-engine-2",
            product_facts_snapshot=None,
            audience_context={"market": "FAKE"},
        )
        # 同素材同 prompt 版本同语言：同一行，字段整体覆盖
        assert second.id == first.id
        assert second.output["verdict"] == "重诊断结论"
        assert second.observation_snapshot["summary"] == "更新后的观察层"
        assert second.product_facts_snapshot is None
        assert second.audience_context == {"market": "FAKE"}
        assert second.evidence_label == "graph_supported"
        assert second.engine_version == "auto:fake-engine-2"
        count = db_session.scalar(
            select(func.count())
            .select_from(CreativeDiagnosis)
            .where(CreativeDiagnosis.asset_id == asset.id)
        )
        assert count == 1

    def test_new_prompt_version_creates_new_row(self, db_session: Session) -> None:
        asset = _make_asset(db_session)
        repo = CreativeDiagnosisRepository(db_session)
        repo.upsert(
            asset.id,
            prompt_version="lab-v0.1.0",
            lang="zh",
            observation_snapshot=_observation(),
            output=_output("旧版本结论"),
            evidence_label="reference_only",
            engine_version="auto:fake-engine-1",
        )
        repo.upsert(
            asset.id,
            prompt_version="lab-v0.2.0",
            lang="zh",
            observation_snapshot=_observation(),
            output=_output("新版本结论"),
            evidence_label="reference_only",
            engine_version="auto:fake-engine-1",
        )
        old = repo.get_current(asset.id, "lab-v0.1.0", "zh")
        new = repo.get_current(asset.id, "lab-v0.2.0", "zh")
        assert old is not None and old.output["verdict"] == "旧版本结论"
        assert new is not None and new.output["verdict"] == "新版本结论"
        assert old.id != new.id


class TestReads:
    def test_get_current_missing_returns_none(self, db_session: Session) -> None:
        asset = _make_asset(db_session)
        repo = CreativeDiagnosisRepository(db_session)
        assert repo.get_current(asset.id, "lab-v0.1.0", "zh") is None
        assert repo.get_latest(asset.id, "zh") is None

    def test_get_latest_returns_newest_across_versions(
        self, db_session: Session
    ) -> None:
        asset = _make_asset(db_session)
        repo = CreativeDiagnosisRepository(db_session)
        # 旧版本行：created_at 显式拨回一天前（同事务 now() 相同，无法靠
        # server_default 分出先后）
        old = CreativeDiagnosis(
            asset_id=asset.id,
            prompt_version="lab-v0.0.9",
            lang="zh",
            observation_snapshot=_observation(),
            output=_output("更早的结论"),
            evidence_label="reference_only",
            engine_version="auto:fake-engine-1",
            created_at=datetime.now(timezone.utc) - timedelta(days=1),
            updated_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        db_session.add(old)
        db_session.flush()
        repo.upsert(
            asset.id,
            prompt_version="lab-v0.1.0",
            lang="zh",
            observation_snapshot=_observation(),
            output=_output("最新的结论"),
            evidence_label="reference_only",
            engine_version="auto:fake-engine-1",
        )
        latest = repo.get_latest(asset.id, "zh")
        assert latest is not None
        assert latest.prompt_version == "lab-v0.1.0"
        assert latest.output["verdict"] == "最新的结论"


class TestLangDimension:
    def test_same_asset_version_zh_en_coexist(self, db_session: Session) -> None:
        """同 asset 同 prompt 版本下 zh/en 两行共存，重诊断其一不覆盖另一个。"""
        asset = _make_asset(db_session)
        repo = CreativeDiagnosisRepository(db_session)
        zh = repo.upsert(
            asset.id,
            prompt_version="lab-v0.1.0",
            lang="zh",
            observation_snapshot=_observation(),
            output=_output("中文结论"),
            evidence_label="reference_only",
            engine_version="auto:fake-engine-1",
        )
        en = repo.upsert(
            asset.id,
            prompt_version="lab-v0.1.0",
            lang="en",
            observation_snapshot=_observation(),
            output=_output("English verdict"),
            evidence_label="reference_only",
            engine_version="auto:fake-engine-1",
        )
        assert zh.id != en.id

        # 重诊断 zh：只覆盖 zh 行，en 行原样保留
        zh2 = repo.upsert(
            asset.id,
            prompt_version="lab-v0.1.0",
            lang="zh",
            observation_snapshot=_observation(),
            output=_output("中文结论v2"),
            evidence_label="reference_only",
            engine_version="auto:fake-engine-1",
        )
        assert zh2.id == zh.id
        en_stored = repo.get_current(asset.id, "lab-v0.1.0", "en")
        assert en_stored is not None
        assert en_stored.id == en.id
        assert en_stored.output["verdict"] == "English verdict"
        count = db_session.scalar(
            select(func.count())
            .select_from(CreativeDiagnosis)
            .where(CreativeDiagnosis.asset_id == asset.id)
        )
        assert count == 2

    def test_get_latest_scoped_to_lang(self, db_session: Session) -> None:
        """get_latest 按语言取：其它语言更新的行不影响本语言的 latest。"""
        asset = _make_asset(db_session)
        repo = CreativeDiagnosisRepository(db_session)
        en_old = CreativeDiagnosis(
            asset_id=asset.id,
            prompt_version="lab-v0.0.9",
            lang="en",
            observation_snapshot=_observation(),
            output=_output("older English verdict"),
            evidence_label="reference_only",
            engine_version="auto:fake-engine-1",
            created_at=datetime.now(timezone.utc) - timedelta(days=1),
            updated_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        db_session.add(en_old)
        db_session.flush()
        repo.upsert(
            asset.id,
            prompt_version="lab-v0.1.0",
            lang="zh",
            observation_snapshot=_observation(),
            output=_output("最新的中文结论"),
            evidence_label="reference_only",
            engine_version="auto:fake-engine-1",
        )
        latest_en = repo.get_latest(asset.id, "en")
        assert latest_en is not None
        assert latest_en.id == en_old.id
        assert latest_en.prompt_version == "lab-v0.0.9"
