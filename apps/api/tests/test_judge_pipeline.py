"""Tests for judge pipeline (services/judge_pipeline) + 上传管线自动触发。

LLM 调用（suggest_dna / judge_pair）与分析服务全部 mock，不依赖真实
API key 或视频文件。
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import (
    AnalysisResult,
    Creative,
    CreativeAsset,
    CreativeDNA,
    CreativeVariant,
    EditLog,
    JudgeSuggestion,
    SplitRuling,
)
from app.repositories.settings import SettingsRepository
from app.schemas.analysis import AnalysisPayload
from app.services import graph_sync, judge_pipeline, merge_ops
from app.services.dna_classifier import DnaSuggestion
from app.services.merge_guard import GuardHit
from app.services.merge_judge import MergeJudgement
from app.services.settings import AIConfig


class _FakeGraphRepository:
    def merge_creatives(self, *_args) -> None:  # noqa: ANN002
        pass


def _patch_graph_sync(monkeypatch) -> None:  # noqa: ANN001
    """Neo4j 同步 patch 成 no-op（测试机的 Neo4j 不能写测试节点）。"""
    monkeypatch.setattr(
        graph_sync, "get_graph_repository", lambda _settings: _FakeGraphRepository()
    )


def _creative_with_analysis(
    db: Session, *, name: str, filename: str, dna_id: str | None = None
) -> Creative:
    creative = Creative(id=str(uuid.uuid4()), name=name, dna_id=dna_id)
    asset = CreativeAsset(
        id=str(uuid.uuid4()), filename=filename, file_type="video",
        storage_key=f"test/{uuid.uuid4()}",
    )
    db.add_all([creative, asset])
    db.flush()
    db.add(
        CreativeVariant(
            id=str(uuid.uuid4()), creative_id=creative.id, asset_id=asset.id,
            name=Path(filename).stem,
        )
    )
    db.add(
        AnalysisResult(
            id=str(uuid.uuid4()), asset_id=asset.id,
            hook="钩子", conflict="冲突", gameplay="玩法",
        )
    )
    db.flush()
    return creative


def _fake_config() -> AIConfig:
    return AIConfig(api_key="sk-fake", base_url="", vision_model="", embedding_model="")


class TestScopedDnaAssignments:
    def test_only_scoped_creative_processed(
        self, db_session: Session, monkeypatch
    ) -> None:
        dna = CreativeDNA(id=str(uuid.uuid4()), code="D1", name="测试家族")
        db_session.add(dna)
        target = _creative_with_analysis(
            db_session, name="c-target", filename="KS_EN-a.mp4"
        )
        other = _creative_with_analysis(
            db_session, name="c-other", filename="KS_EN-b.mp4"
        )
        db_session.flush()
        monkeypatch.setattr(
            judge_pipeline, "suggest_dna",
            lambda creative, hook, gameplay, db, config: DnaSuggestion(
                dna=dna, votes=3, reason="规则命中"
            ),
        )
        judge_pipeline.run_dna_assignments(
            db_session, _fake_config(),
            dry_run=False, auto_enabled=True, creative_id=target.id,
        )
        db_session.flush()
        db_session.expire_all()
        assert db_session.get(Creative, target.id).dna_id == dna.id
        assert db_session.get(Creative, other.id).dna_id is None
        logs = db_session.scalars(
            select(EditLog).where(EditLog.field == "dna_id")
        ).all()
        assert len(logs) == 1
        assert logs[0].entity_id == target.id
        assert logs[0].new_value.startswith("auto:")


class TestScopedMergeJudgements:
    def test_only_pairs_involving_scoped_creative(
        self, db_session: Session, monkeypatch
    ) -> None:
        tail_a = "260611-58-制作人甲-屡次失败重开Ai片头V1-竖.mp4"
        tail_b = "260612-58-制作人乙-黑心老板扣工资V2-竖.mp4"
        # 同对成员同名（名称相似度 1.0 不进 borderline 候选），
        # 两对之间名字零交集（防 heuristic 2 交叉出对）
        a_en = _creative_with_analysis(
            db_session, name="alpha-beta-gamma-delta-epsilon",
            filename=f"KS_EN-{tail_a}",
        )
        _creative_with_analysis(
            db_session, name="alpha-beta-gamma-delta-epsilon",
            filename=f"KS_KR-{tail_a}",
        )
        b_en = _creative_with_analysis(
            db_session, name="omega-x1-x2-x3-zeta", filename=f"KS_EN-{tail_b}"
        )
        _creative_with_analysis(
            db_session, name="omega-x1-x2-x3-zeta", filename=f"KS_KR-{tail_b}"
        )
        db_session.flush()
        monkeypatch.setattr(
            judge_pipeline, "judge_pair",
            lambda config, *, analysis_a, analysis_b: MergeJudgement(
                same_creative=False, votes=3, reason="不同创意",
                swapped_consistent=True,
            ),
        )
        judge_pipeline.run_merge_judgements(
            db_session, _fake_config(),
            dry_run=False, auto_enabled=True, creative_id=a_en.id,
        )
        db_session.flush()
        suggestions = db_session.scalars(
            select(JudgeSuggestion).where(JudgeSuggestion.kind == "merge_pair")
        ).all()
        assert len(suggestions) == 1
        assert a_en.id in (suggestions[0].left_id, suggestions[0].right_id)
        assert b_en.id not in (suggestions[0].left_id, suggestions[0].right_id)


class TestAutoMergeExecute:
    """合并自动执行的决策矩阵：LLM 3/3 + pHash 对齐 ≥0.90 + 开关开 →
    自动合并（edit_logs 标 auto:、无建议残留）；任一不满足只写建议。

    LLM（judge_pair）与测量（measure_pair_alignment）全部 mock；
    Neo4j 同步 patch 成 no-op（测试机的 Neo4j 不能写测试节点）。
    """

    def _setup_pair(self, db: Session) -> tuple[Creative, Creative]:
        tail = "260611-58-制作人甲-同一主题自动合并-竖.mp4"
        first = _creative_with_analysis(
            db, name="auto-merge-gamma-delta-epsilon",
            filename=f"KS_EN-{tail}",
        )
        second = _creative_with_analysis(
            db, name="auto-merge-gamma-delta-epsilon",
            filename=f"KS_KR-{tail}",
        )
        db.flush()
        return first, second

    def _harness(
        self,
        db: Session,
        monkeypatch,  # noqa: ANN001
        *,
        votes: int = 3,
        alignment: float | None = 0.95,
        merge_auto: str | None = "true",
    ) -> tuple[Creative, Creative]:
        first, second = self._setup_pair(db)
        monkeypatch.setattr(
            judge_pipeline, "judge_pair",
            lambda config, *, analysis_a, analysis_b: MergeJudgement(
                same_creative=True, votes=votes, reason="同一创意换皮",
                swapped_consistent=True,
            ),
        )
        monkeypatch.setattr(
            judge_pipeline, "measure_pair_alignment",
            lambda db, settings, a, b: alignment,
        )
        _patch_graph_sync(monkeypatch)
        if merge_auto is not None:
            SettingsRepository(db).set("merge_auto_enabled", merge_auto)
        db.flush()
        return first, second

    def _run(self, db: Session) -> None:
        judge_pipeline.run_merge_judgements(
            db, _fake_config(), dry_run=False, auto_enabled=True,
            settings=Settings(),
        )
        db.flush()
        db.expire_all()

    def _merge_pair_suggestions(self, db: Session) -> list[JudgeSuggestion]:
        return list(db.scalars(
            select(JudgeSuggestion).where(JudgeSuggestion.kind == "merge_pair")
        ).all())

    def test_full_evidence_auto_merges(self, db_session: Session, monkeypatch) -> None:
        first, second = self._harness(db_session, monkeypatch)
        self._run(db_session)
        # 一方消失，另一方收下全部变体（方向：related → first 成员）
        survivors = [c for c in (first, second) if db_session.get(Creative, c.id)]
        assert len(survivors) == 1
        variants = db_session.scalars(
            select(CreativeVariant).where(
                CreativeVariant.creative_id == survivors[0].id
            )
        ).all()
        assert len(variants) == 2
        logs = db_session.scalars(
            select(EditLog).where(EditLog.action == "merge")
        ).all()
        assert len(logs) == 1
        assert logs[0].new_value.startswith("auto:")
        assert self._merge_pair_suggestions(db_session) == []

    def test_low_alignment_only_suggests(self, db_session: Session, monkeypatch) -> None:
        first, second = self._harness(db_session, monkeypatch, alignment=0.6)
        self._run(db_session)
        assert db_session.get(Creative, first.id) is not None
        assert db_session.get(Creative, second.id) is not None
        suggestions = self._merge_pair_suggestions(db_session)
        assert len(suggestions) == 1
        assert suggestions[0].verdict == "merge"

    def test_no_alignment_evidence_only_suggests(
        self, db_session: Session, monkeypatch
    ) -> None:
        first, second = self._harness(db_session, monkeypatch, alignment=None)
        self._run(db_session)
        assert db_session.get(Creative, first.id) is not None
        assert db_session.get(Creative, second.id) is not None
        assert len(self._merge_pair_suggestions(db_session)) == 1

    def test_split_vote_only_suggests(self, db_session: Session, monkeypatch) -> None:
        first, second = self._harness(db_session, monkeypatch, votes=2)
        self._run(db_session)
        assert db_session.get(Creative, first.id) is not None
        assert db_session.get(Creative, second.id) is not None
        assert len(self._merge_pair_suggestions(db_session)) == 1

    def test_switch_off_only_suggests(self, db_session: Session, monkeypatch) -> None:
        first, second = self._harness(db_session, monkeypatch, merge_auto="false")
        self._run(db_session)
        assert db_session.get(Creative, first.id) is not None
        assert db_session.get(Creative, second.id) is not None
        assert len(self._merge_pair_suggestions(db_session)) == 1

    def test_switch_default_on(self, db_session: Session, monkeypatch) -> None:
        # merge_auto_enabled 未设置 = 默认开：证据齐全即自动合并
        first, second = self._harness(db_session, monkeypatch, merge_auto=None)
        self._run(db_session)
        survivors = [c for c in (first, second) if db_session.get(Creative, c.id)]
        assert len(survivors) == 1
        assert self._merge_pair_suggestions(db_session) == []

    def test_prior_ruling_skips_pair(self, db_session: Session, monkeypatch) -> None:
        first, second = self._harness(db_session, monkeypatch)
        low, high = sorted((first.name, second.name))
        db_session.add(
            SplitRuling(
                id=str(uuid.uuid4()), name_a=low, name_b=high,
                reason="人工裁决维持拆分", source="inbox_close",
            )
        )
        db_session.flush()
        self._run(db_session)
        assert db_session.get(Creative, first.id) is not None
        assert db_session.get(Creative, second.id) is not None
        assert self._merge_pair_suggestions(db_session) == []
        assert db_session.scalars(
            select(EditLog).where(EditLog.action == "merge")
        ).all() == []

    def test_guard_block_never_auto(self, db_session: Session, monkeypatch) -> None:
        first, second = self._harness(db_session, monkeypatch)
        monkeypatch.setattr(
            merge_ops, "check_merge",
            lambda source, target, db=None: [
                GuardHit(level="block", check="prior_ruling", message="既定裁决")
            ],
        )
        self._run(db_session)
        assert db_session.get(Creative, first.id) is not None
        assert db_session.get(Creative, second.id) is not None
        assert len(self._merge_pair_suggestions(db_session)) == 1
        assert db_session.scalars(
            select(EditLog).where(EditLog.action == "merge")
        ).all() == []


class _FakeStorage:
    def __init__(self, _settings) -> None:  # noqa: ANN001
        pass

    def download_to(self, _key: str, _dest: str) -> None:
        pass

    def put_bytes(self, _key: str, _data: bytes, _content_type: str) -> None:
        pass


class _FakeAnalysisService:
    def __init__(self, _config) -> None:  # noqa: ANN001
        pass

    def analyze_frames(self, frames, media_type):  # noqa: ANN001, ANN201
        return AnalysisPayload(creative_name="auto-judge-creative", tags=["t"])

    def embed(self, _text: str) -> list[float]:
        raise RuntimeError("no embeddings in test")


@pytest.fixture()
def pipeline_mocks(monkeypatch, tmp_path: Path, db_session: Session) -> None:
    """把 run_analysis_pipeline 的外部依赖全部 mock 掉。

    SessionLocal 绑到测试连接上（同一事务内，管线自建 session 才能看到
    夹具数据、改动随测试回滚）；Neo4j 同步 patch 成 no-op（测试机的
    Neo4j 可能开着，不能往里写测试节点）。
    """
    from sqlalchemy.orm import sessionmaker

    from app.services import pipeline

    monkeypatch.setattr(
        pipeline, "get_settings",
        lambda: Settings(upload_dir=str(tmp_path)),
    )
    monkeypatch.setattr(
        pipeline, "SessionLocal",
        sessionmaker(bind=db_session.get_bind(), autoflush=False,
                     expire_on_commit=False),
    )
    monkeypatch.setattr(
        pipeline.graph_sync, "sync_asset_subgraph",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(pipeline, "StorageService", _FakeStorage)
    monkeypatch.setattr(pipeline, "AnalysisService", _FakeAnalysisService)
    frame = tmp_path / "frame-00.jpg"
    frame.write_bytes(b"\xff\xd8\xff")  # 假 JPEG，只为 read_bytes 不炸
    monkeypatch.setattr(
        pipeline, "extract_smart_frames",
        lambda _video, _dir: [(str(frame), 0.0)],
    )
    monkeypatch.setattr(
        pipeline, "build_contact_sheet",
        lambda _paths, _out: None,  # contact sheet 是 bonus，直接跳过
    )
    monkeypatch.setattr(
        pipeline, "resolve_ai_config", lambda _db, _s: _fake_config()
    )


def _seed_pending_asset(db: Session) -> CreativeAsset:
    asset = CreativeAsset(
        id=str(uuid.uuid4()), filename="KS_EN-pipe.mp4", file_type="video",
        storage_key=f"test/{uuid.uuid4()}", analysis_status="pending",
    )
    db.add(asset)
    db.commit()  # 管线自建 session，必须真实落库才能读到
    return asset


class TestPipelineTrigger:
    def test_judge_runs_after_analysis(
        self, db_session: Session, pipeline_mocks: None, monkeypatch
    ) -> None:
        from app.repositories.assets import AssetRepository
        from app.services.pipeline import run_analysis_pipeline

        asset = _seed_pending_asset(db_session)
        statuses: list[tuple[str, str]] = []

        def _spy(db, config, creative_id, settings=None) -> None:  # noqa: ANN001, ANN001
            # 在管线自己的 session 里读：judge 被触发时素材必须已 completed
            statuses.append(
                (AssetRepository(db).get(asset.id).analysis_status, creative_id)
            )

        monkeypatch.setattr(judge_pipeline, "run_post_analysis", _spy)
        run_analysis_pipeline(asset.id)
        assert len(statuses) == 1
        assert statuses[0][0] == "completed"
        assert statuses[0][1]  # creative_id 非空

    def test_judge_failure_contained(
        self, db_session: Session, monkeypatch
    ) -> None:
        """判定内部异常不传播、不破坏既有数据（run_post_analysis 自行容错，
        所以管线的 completed 状态天然不受 judge 失败影响）。"""
        creative = _creative_with_analysis(
            db_session, name="c-boom", filename="KS_EN-boom.mp4"
        )

        def _boom(*args, **kwargs) -> None:  # noqa: ANN002, ANN003
            raise RuntimeError("judge 炸了")

        monkeypatch.setattr(judge_pipeline, "run_dna_assignments", _boom)
        monkeypatch.setattr(judge_pipeline, "run_merge_judgements", _boom)
        # 不抛异常，既有数据完好
        judge_pipeline.run_post_analysis(db_session, _fake_config(), creative.id)
        assert db_session.get(Creative, creative.id) is not None
