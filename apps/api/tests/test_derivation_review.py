"""Tests for single-edge derivation review (services/derivation_review).

measure_pair / judge_factor 全部 mock 掉，不依赖真实视频、ffmpeg 或 LLM；
Neo4j 用一个记录调用的假仓库（验证同步发生且失败不阻断）。
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import (
    Creative,
    CreativeAsset,
    CreativeVariant,
    EditLog,
    JudgeSuggestion,
    VariantDerivation,
)
from app.services import derivation_review
from app.services.derivation_review import review_derivation_edge
from app.services.variant_diff import VariantDiff, VideoMeta


class _FakeStorage:
    """download_to 空实现——measure_pair 已被 mock，文件内容无所谓。"""

    def __init__(self, _settings) -> None:  # noqa: ANN001
        pass

    def download_to(self, _key: str, _dest: str) -> None:
        pass


class _FakeGraphRepo:
    def __init__(self) -> None:
        self.links: list[tuple[str, str, str]] = []

    def link_derivation(self, source: str, target: str, factor: str) -> None:
        self.links.append((source, target, factor))


def _meta(width: int = 1920, height: int = 1080, duration: float = 20.0) -> VideoMeta:
    return VideoMeta(width=width, height=height, duration=duration, has_audio=True)


def _mock_measure(monkeypatch, diff: VariantDiff) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        derivation_review,
        "measure_pair",
        lambda _s, _t: (_meta(), _meta(), diff),
    )


@pytest.fixture()
def edge(db_session: Session) -> VariantDerivation:
    creative = Creative(id=str(uuid.uuid4()), name="auto-review")
    db_session.add(creative)
    db_session.flush()
    variants: list[CreativeVariant] = []
    for tag in ("V1", "V2"):
        asset = CreativeAsset(
            id=str(uuid.uuid4()),
            filename=f"KS_EN-260630-58-制作人甲-测试素材{tag}-竖.mp4",
            file_type="video",
            storage_key=f"test/{uuid.uuid4()}",
        )
        db_session.add(asset)
        db_session.flush()
        variant = CreativeVariant(
            id=str(uuid.uuid4()), creative_id=creative.id,
            asset_id=asset.id, name=tag,
        )
        db_session.add(variant)
        variants.append(variant)
    derivation = VariantDerivation(
        id=str(uuid.uuid4()),
        source_variant_id=variants[0].id,
        target_variant_id=variants[1].id,
        factor="unknown",
    )
    db_session.add(derivation)
    db_session.flush()
    return derivation


@pytest.fixture()
def mocked(monkeypatch) -> _FakeGraphRepo:  # noqa: ANN001
    """Mock 掉 MinIO 存储与 Neo4j；返回假图仓库供断言。"""
    monkeypatch.setattr(derivation_review, "StorageService", _FakeStorage)
    repo = _FakeGraphRepo()
    monkeypatch.setattr(
        derivation_review.graph_sync, "get_graph_repository", lambda _s: repo
    )
    return repo


class TestReviewDerivationEdge:
    def test_deterministic_update(
        self, db_session: Session, edge, mocked, monkeypatch
    ) -> None:
        # resolution_changed → classify 定案 aspect-ratio
        _mock_measure(
            monkeypatch,
            VariantDiff(
                aligned_fraction=1.0, unmatched_prefix_sec=0.0,
                unmatched_suffix_sec=0.0, resolution_changed=True,
            ),
        )
        result = review_derivation_edge(db_session, Settings(), edge.id)
        assert result == "updated:aspect-ratio"
        assert db_session.get(VariantDerivation, edge.id).factor == "aspect-ratio"
        log = db_session.scalars(
            select(EditLog).where(
                EditLog.entity_id == edge.id, EditLog.action == "update"
            )
        ).one()
        assert log.old_value == "unknown"
        assert "aspect-ratio" in log.new_value
        assert mocked.links == [
            (edge.source_variant_id, edge.target_variant_id, "aspect-ratio")
        ]

    def test_unchanged_when_factor_matches(
        self, db_session: Session, edge, mocked, monkeypatch
    ) -> None:
        edge.factor = "aspect-ratio"
        db_session.flush()
        _mock_measure(
            monkeypatch,
            VariantDiff(
                aligned_fraction=1.0, unmatched_prefix_sec=0.0,
                unmatched_suffix_sec=0.0, resolution_changed=True,
            ),
        )
        result = review_derivation_edge(db_session, Settings(), edge.id)
        assert result == "unchanged"
        assert db_session.scalars(
            select(EditLog).where(EditLog.entity_id == edge.id)
        ).all() == []
        assert mocked.links == []

    def test_factor_reviewed_edge_is_skipped(
        self, db_session: Session, edge, mocked, monkeypatch
    ) -> None:
        # 人工已拍板因子（factor_reviewed=True）的边：复核直接跳过，
        # 不测量、不修正、不写建议——批量重跑也不能把人工裁决翻案
        edge.factor = "remake"
        edge.factor_reviewed = True
        db_session.flush()
        monkeypatch.setattr(
            derivation_review,
            "measure_pair",
            lambda _s, _t: pytest.fail("人工确认的边不应触发测量"),
        )
        result = review_derivation_edge(db_session, Settings(), edge.id)
        assert result == "unchanged"
        assert db_session.get(VariantDerivation, edge.id).factor == "remake"
        assert db_session.scalars(
            select(EditLog).where(EditLog.entity_id == edge.id)
        ).all() == []
        assert mocked.links == []

    def test_not_a_derivation_writes_suggestion(
        self, db_session: Session, edge, mocked, monkeypatch
    ) -> None:
        _mock_measure(
            monkeypatch,
            VariantDiff(
                aligned_fraction=0.2, unmatched_prefix_sec=0.0,
                unmatched_suffix_sec=0.0,
            ),
        )
        result = review_derivation_edge(db_session, Settings(), edge.id)
        assert result == "suggested:not-a-derivation"
        suggestion = db_session.scalars(
            select(JudgeSuggestion).where(
                JudgeSuggestion.kind == "derivation-factor",
                JudgeSuggestion.left_id == edge.id,
            )
        ).one()
        assert suggestion.verdict == "not-a-derivation"
        assert suggestion.votes == 3
        # 绝不自动改边
        assert db_session.get(VariantDerivation, edge.id).factor == "unknown"

    def test_llm_suggestion(
        self, db_session: Session, edge, mocked, monkeypatch
    ) -> None:
        # 中段发散 → classify 定不了，走 LLM
        _mock_measure(
            monkeypatch,
            VariantDiff(
                aligned_fraction=0.8, unmatched_prefix_sec=0.0,
                unmatched_suffix_sec=0.0, divergent_segments=[(8.0, 12.0)],
            ),
        )
        from app.services.derivation_judge import DerivationJudgement

        monkeypatch.setattr(
            derivation_review,
            "judge_factor",
            lambda config, **kwargs: DerivationJudgement(
                factor="remake", votes=3, reason="画面整体重拍"
            ),
        )
        result = review_derivation_edge(db_session, Settings(), edge.id)
        assert result == "suggested:remake"
        suggestion = db_session.scalars(
            select(JudgeSuggestion).where(
                JudgeSuggestion.kind == "derivation-factor",
                JudgeSuggestion.left_id == edge.id,
            )
        ).one()
        assert suggestion.verdict == "remake"

    def test_undecided_without_api_key(
        self, db_session: Session, edge, mocked, monkeypatch
    ) -> None:
        _mock_measure(
            monkeypatch,
            VariantDiff(
                aligned_fraction=0.8, unmatched_prefix_sec=0.0,
                unmatched_suffix_sec=0.0, divergent_segments=[(8.0, 12.0)],
            ),
        )
        # 无 API key → judge_factor 返回 None（确定性 mock，防环境变量注入）
        from app.services.settings import AIConfig

        monkeypatch.setattr(
            derivation_review,
            "resolve_ai_config",
            lambda db, s: AIConfig(
                api_key="", base_url="", vision_model="", embedding_model=""
            ),
        )
        result = review_derivation_edge(db_session, Settings(), edge.id)
        assert result == "undecided"
        assert db_session.scalars(select(JudgeSuggestion)).all() == []

    def test_non_video_skipped(self, db_session: Session, edge, mocked) -> None:
        source_variant = db_session.get(CreativeVariant, edge.source_variant_id)
        asset = db_session.get(CreativeAsset, source_variant.asset_id)
        asset.file_type = "image"
        db_session.flush()
        result = review_derivation_edge(db_session, Settings(), edge.id)
        assert result == "skipped:non-video"

    def test_measure_failure_contained(
        self, db_session: Session, edge, mocked, monkeypatch
    ) -> None:
        def _boom(_s: str, _t: str) -> None:
            raise RuntimeError("ffmpeg 炸了")

        monkeypatch.setattr(derivation_review, "measure_pair", _boom)
        result = review_derivation_edge(db_session, Settings(), edge.id)
        assert result == "failed"
        # 边本身不受影响
        assert db_session.get(VariantDerivation, edge.id) is not None

    def test_missing_edge(self, db_session: Session, mocked) -> None:
        result = review_derivation_edge(db_session, Settings(), str(uuid.uuid4()))
        assert result == "missing"


class TestCreateDerivationSchedulesReview:
    """POST /derivations 创建边后应排入后台复核任务。"""

    def test_background_task_added(self, db_session: Session) -> None:
        from fastapi import BackgroundTasks

        from app.api.routes.derivations import create_derivation
        from app.schemas.derivation import DerivationCreatePayload

        creative = Creative(id=str(uuid.uuid4()), name="bg-task")
        db_session.add(creative)
        db_session.flush()
        variants: list[CreativeVariant] = []
        for tag in ("V1", "V2"):
            asset = CreativeAsset(
                id=str(uuid.uuid4()), filename=f"KS_EN-x-{tag}.mp4",
                file_type="video", storage_key=f"test/{uuid.uuid4()}",
            )
            db_session.add(asset)
            db_session.flush()
            variant = CreativeVariant(
                id=str(uuid.uuid4()), creative_id=creative.id,
                asset_id=asset.id, name=tag,
            )
            db_session.add(variant)
            variants.append(variant)
        db_session.flush()

        tasks = BackgroundTasks()
        result = create_derivation(
            DerivationCreatePayload(
                source_variant_id=variants[0].id,
                target_variant_id=variants[1].id,
                factor="aspect-ratio",
            ),
            background_tasks=tasks,
            db=db_session,
            settings=Settings(),
        )
        assert result.success is True
        assert result.data is not None
        assert len(tasks.tasks) == 1
        task = tasks.tasks[0]
        assert task.func is derivation_review.run_derivation_review
        assert task.args == (result.data.id,)
