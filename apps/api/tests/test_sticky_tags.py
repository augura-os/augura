"""sticky 来源标签（competitor-reference）防覆盖测试。

三层覆盖：
1. 纯函数 preserve_sticky_tags：sticky 并入 / 去重 / 无 sticky 原样。
2. pipeline 集成点（_set_tags_preserving_sources 的两行模式）：replace
   语义的自动打标签不抹 sticky 标记。
3. 端到端：全 mock 的 pipeline 重分析（fresh + resume 两路径）后
   competitor-reference 仍在。
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models import CreativeAsset
from app.repositories.tags import (
    STICKY_SOURCE_TAGS,
    TagRepository,
    preserve_sticky_tags,
)
from app.schemas.analysis import AnalysisPayload
from app.services import graph_sync, judge_pipeline, pipeline
from app.services.settings import AIConfig


class TestPreserveStickyTags:
    def test_sticky_merged_into_incoming(self) -> None:
        result = preserve_sticky_tags(
            ["competitor-reference", "old-ai-tag"], ["new-tag-a", "new-tag-b"]
        )
        assert result == ["new-tag-a", "new-tag-b", "competitor-reference"]

    def test_sticky_dedup_when_already_incoming(self) -> None:
        result = preserve_sticky_tags(
            ["competitor-reference"], ["competitor-reference", "new-tag-a"]
        )
        assert result == ["competitor-reference", "new-tag-a"]

    def test_no_sticky_returns_incoming_as_is(self) -> None:
        assert preserve_sticky_tags(["old-ai-tag"], ["new-tag-a"]) == ["new-tag-a"]
        assert preserve_sticky_tags([], []) == []

    def test_non_sticky_existing_tags_are_dropped(self) -> None:
        # 普通旧标签不保留——replace 语义只给 sticky 开口子
        result = preserve_sticky_tags(["old-ai-tag", "another-old"], ["new-tag-a"])
        assert result == ["new-tag-a"]

    def test_sticky_set_contents(self) -> None:
        assert "competitor-reference" in STICKY_SOURCE_TAGS


def _seed_asset(db: Session, filename: str, status: str = "pending") -> CreativeAsset:
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


class TestIntegrationPoint:
    """_set_tags_preserving_sources 的两行模式（与 pipeline 调用点逐字一致）。"""

    def test_replace_keeps_sticky_source_tag(self, db_session: Session) -> None:
        asset = _seed_asset(db_session, "FAKE-m1b-sticky-repo.mp4")
        tag_repo = TagRepository(db_session)
        tag_repo.set_asset_tags(asset.id, ["competitor-reference", "old-ai-tag"])

        # 与 pipeline._set_tags_preserving_sources 相同的调用序列
        existing_names = [tag.name for tag in tag_repo.get_asset_tags(asset.id)]
        tag_repo.set_asset_tags(
            asset.id, preserve_sticky_tags(existing_names, ["new-ai-tag"])
        )

        names = [tag.name for tag in tag_repo.get_asset_tags(asset.id)]
        assert "competitor-reference" in names
        assert "new-ai-tag" in names
        assert "old-ai-tag" not in names  # 非 sticky 旧标签被正常替换

    def test_full_replace_removes_sticky_when_escape_hatch(
        self, db_session: Session
    ) -> None:
        """人工 PUT tags 走 set_asset_tags 全替换：deliberate 移除是合法逃生舱。"""
        asset = _seed_asset(db_session, "FAKE-m1b-sticky-hatch.mp4")
        tag_repo = TagRepository(db_session)
        tag_repo.set_asset_tags(asset.id, ["competitor-reference"])

        tag_repo.set_asset_tags(asset.id, ["only-this-tag"])

        names = [tag.name for tag in tag_repo.get_asset_tags(asset.id)]
        assert names == ["only-this-tag"]


# ----------------------------------------------------------------------
# 端到端：pipeline 重分析（fresh + resume 两条 set_asset_tags 路径）后
# competitor-reference 仍在。mock 套路同 test_jobs.TestPipelineIdempotency。
# ----------------------------------------------------------------------
class _FakeAnalysisService:
    def __init__(self, _config) -> None:  # noqa: ANN001
        pass

    def analyze_frames(self, frames, media_type) -> AnalysisPayload:  # noqa: ANN001
        return AnalysisPayload(
            summary="fake summary for sticky test",
            creative_name="fake-sticky-creative",
            tags=["fake-ai-tag"],
        )


class _FakeStorage:
    def __init__(self, *_args) -> None:  # noqa: ANN002
        pass

    def put_bytes(self, *_args) -> None:  # noqa: ANN002
        pass

    def download_to(self, *_args) -> None:  # noqa: ANN002
        pass

    def get_bytes(self, *_args) -> bytes:  # noqa: ANN002
        return b"\xff\xd8\xff"


@pytest.fixture()
def pipeline_mocks(monkeypatch, tmp_path: Path, db_session: Session) -> None:
    monkeypatch.setattr(
        pipeline, "get_settings", lambda: Settings(upload_dir=str(tmp_path))
    )
    monkeypatch.setattr(
        pipeline,
        "SessionLocal",
        sessionmaker(
            bind=db_session.get_bind(), autoflush=False, expire_on_commit=False
        ),
    )
    monkeypatch.setattr(graph_sync, "sync_asset_subgraph", lambda *a, **k: None)
    monkeypatch.setattr(judge_pipeline, "run_post_analysis", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "StorageService", _FakeStorage)
    monkeypatch.setattr(pipeline, "AnalysisService", _FakeAnalysisService)
    frame = tmp_path / "frame-00.jpg"
    frame.write_bytes(b"\xff\xd8\xff")
    monkeypatch.setattr(
        pipeline, "extract_smart_frames", lambda _v, _d: [(str(frame), 0.0)]
    )
    monkeypatch.setattr(pipeline, "build_contact_sheet", lambda *_a: None)
    monkeypatch.setattr(
        pipeline,
        "resolve_ai_config",
        lambda _db, _s: AIConfig(
            api_key="sk-fake",
            base_url="",
            vision_model="fake-vision-v1",
            embedding_model="",
        ),
    )


class TestPipelineReanalysisKeepsSticky:
    def test_fresh_and_resume_paths_keep_competitor_reference(
        self, db_session: Session, pipeline_mocks: None
    ) -> None:
        asset = _seed_asset(db_session, "FAKE-m1b-sticky-pipeline.mp4")
        tag_repo = TagRepository(db_session)
        # /lab/references 入口打的来源标记
        tag_repo.set_asset_tags(asset.id, ["competitor-reference"])

        # fresh 路径：Vision → set_asset_tags(payload.tags)
        pipeline.run_analysis_pipeline(asset.id)
        db_session.expire_all()
        names = [tag.name for tag in tag_repo.get_asset_tags(asset.id)]
        assert "competitor-reference" in names
        assert "fake-ai-tag" in names

        # resume 路径：重分析跳过 Vision，payload 来自已存 analysis（tags 依旧
        # 不含来源标记）——replace 也不许抹掉 sticky
        pipeline.run_analysis_pipeline(asset.id)
        db_session.expire_all()
        names = [tag.name for tag in tag_repo.get_asset_tags(asset.id)]
        assert "competitor-reference" in names
        assert "fake-ai-tag" in names
