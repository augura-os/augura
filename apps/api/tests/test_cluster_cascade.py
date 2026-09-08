"""_cluster 三级级联：文本召回 → 视频测量裁决 → 文本 margin 决策。

测量层（measure_pair / 候选族视频下载）全部 mock；recall 在需要精确
控制分数时也 mock（Jaccard 精确造分太脆，级联逻辑才是被测对象）。
"""
from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Creative, CreativeAsset, CreativeVariant, EditLog
from app.services import pipeline
from app.services.pipeline import _cluster


class _FakeStorage:
    def __init__(self, _settings: Settings) -> None:
        pass

    def download_to(self, _key: str, dest: str) -> None:
        Path(dest).write_bytes(b"fake-video")


def _settings(tmp_path: Path) -> Settings:
    return Settings(upload_dir=str(tmp_path))


def _new_asset(
    db: Session, *, filename: str = "new-variant.mp4", file_type: str = "video"
) -> CreativeAsset:
    asset = CreativeAsset(
        id=str(uuid.uuid4()), filename=filename, file_type=file_type,
        storage_key=f"test/{uuid.uuid4()}",
    )
    db.add(asset)
    db.flush()
    return asset


def _creative_with_video(
    db: Session, *, name: str, text: str = ""
) -> Creative:
    creative = Creative(
        id=str(uuid.uuid4()), name=name, representative_text=text or name
    )
    asset = CreativeAsset(
        id=str(uuid.uuid4()), filename=f"{name}.mp4", file_type="video",
        storage_key=f"test/{uuid.uuid4()}",
    )
    variant = CreativeVariant(
        id=str(uuid.uuid4()), creative_id=creative.id, asset_id=asset.id,
        name=name,
    )
    db.add_all([creative, asset, variant])
    db.flush()
    return creative


def _local_video(tmp_path: Path) -> str:
    path = tmp_path / "new.mp4"
    path.write_bytes(b"fake")
    return str(path)


def _measure(aligned: float):
    return lambda _a, _b: (None, None, SimpleNamespace(aligned_fraction=aligned))


def _cluster_logs(db: Session, creative_id: str) -> list[EditLog]:
    return list(
        db.scalars(
            select(EditLog).where(
                EditLog.entity_id == creative_id, EditLog.field == "cluster"
            )
        ).all()
    )


def _creative_count(db: Session) -> int:
    return int(db.scalar(select(func.count()).select_from(Creative)) or 0)


class TestTextMarginPath:
    def test_clear_winner_attaches_and_logs(
        self, db_session: Session, tmp_path: Path, monkeypatch
    ) -> None:
        existing = _creative_with_video(db_session, name="fail-retry-island")
        # 测量低对齐（0.3 < 0.90），但文本是唯一候选且过阈值 → 文本关联
        monkeypatch.setattr(pipeline, "measure_pair", _measure(0.30))
        asset = _new_asset(db_session)
        creative, _ = _cluster(
            db_session, asset, "fail-retry-island", None,
            "fail-retry-island fail retry island",
            settings=_settings(tmp_path),
            storage=_FakeStorage(_settings(tmp_path)),
            local_video=_local_video(tmp_path),
        )
        assert creative.id == existing.id
        logs = _cluster_logs(db_session, existing.id)
        assert len(logs) == 1
        assert logs[0].new_value.startswith("auto: cluster")
        assert "文本" in logs[0].new_value

    def test_twins_create_new_without_log(
        self, db_session: Session, tmp_path: Path, monkeypatch
    ) -> None:
        a = _creative_with_video(db_session, name="fail-retry-a")
        b = _creative_with_video(db_session, name="fail-retry-b")
        # 双子并列（margin < 0.05），测量都不足 0.90 → 新建，不自动归
        monkeypatch.setattr(
            pipeline, "top_creative_matches_by_text",
            lambda _n, _t, _cs, limit=2: [(a, 0.40), (b, 0.38)][:limit],
        )
        monkeypatch.setattr(pipeline, "measure_pair", _measure(0.30))
        asset = _new_asset(db_session)
        creative, _ = _cluster(
            db_session, asset, "fail-retry-c", None, "fail retry",
            settings=_settings(tmp_path),
            storage=_FakeStorage(_settings(tmp_path)),
            local_video=_local_video(tmp_path),
        )
        assert creative.id not in (a.id, b.id)
        assert _creative_count(db_session) == 3
        assert _cluster_logs(db_session, a.id) == []
        assert _cluster_logs(db_session, b.id) == []

    def test_no_candidates_creates_new(self, db_session: Session) -> None:
        asset = _new_asset(db_session)
        creative, previous = _cluster(
            db_session, asset, "brand-new", None, "brand new"
        )
        assert _creative_count(db_session) == 1
        assert creative.representative_text == "brand new"
        assert previous is None


class TestMeasuredOverride:
    def test_high_alignment_attaches_despite_low_text(
        self, db_session: Session, tmp_path: Path, monkeypatch
    ) -> None:
        existing = _creative_with_video(db_session, name="measured-target")
        # 文本只有 0.25（低于阈值 0.34），但视频对齐 0.93 → 物证直接关联
        monkeypatch.setattr(
            pipeline, "top_creative_matches_by_text",
            lambda _n, _t, _cs, limit=2: [(existing, 0.25)][:limit],
        )
        monkeypatch.setattr(pipeline, "measure_pair", _measure(0.93))
        asset = _new_asset(db_session)
        creative, _ = _cluster(
            db_session, asset, "whatever", None, "whatever",
            settings=_settings(tmp_path),
            storage=_FakeStorage(_settings(tmp_path)),
            local_video=_local_video(tmp_path),
        )
        assert creative.id == existing.id
        logs = _cluster_logs(db_session, existing.id)
        assert len(logs) == 1
        assert "视频帧对齐 93%" in logs[0].new_value

    def test_twins_second_candidate_wins_by_measurement(
        self, db_session: Session, tmp_path: Path, monkeypatch
    ) -> None:
        a = _creative_with_video(db_session, name="twin-a")
        b = _creative_with_video(db_session, name="twin-b")
        monkeypatch.setattr(
            pipeline, "top_creative_matches_by_text",
            lambda _n, _t, _cs, limit=2: [(a, 0.40), (b, 0.38)][:limit],
        )

        def _measure_by_path(_src: str, dst: str):  # noqa: ANN202
            aligned = 0.95 if b.id in dst else 0.30
            return None, None, SimpleNamespace(aligned_fraction=aligned)

        monkeypatch.setattr(pipeline, "measure_pair", _measure_by_path)
        asset = _new_asset(db_session)
        creative, _ = _cluster(
            db_session, asset, "twin-c", None, "twin",
            settings=_settings(tmp_path),
            storage=_FakeStorage(_settings(tmp_path)),
            local_video=_local_video(tmp_path),
        )
        assert creative.id == b.id

    def test_low_alignment_borderline_text_creates(
        self, db_session: Session, tmp_path: Path, monkeypatch
    ) -> None:
        existing = _creative_with_video(db_session, name="not-same-source")
        # 文本中间带（0.25）+ 对齐率 0.30 < 0.50 → 新建（收件箱机制兜底）
        monkeypatch.setattr(
            pipeline, "top_creative_matches_by_text",
            lambda _n, _t, _cs, limit=2: [(existing, 0.25)][:limit],
        )
        monkeypatch.setattr(pipeline, "measure_pair", _measure(0.30))
        asset = _new_asset(db_session)
        creative, _ = _cluster(
            db_session, asset, "new-thing", None, "new thing",
            settings=_settings(tmp_path),
            storage=_FakeStorage(_settings(tmp_path)),
            local_video=_local_video(tmp_path),
        )
        assert creative.id != existing.id
        assert _cluster_logs(db_session, existing.id) == []


class TestMeasurementFallback:
    def test_measure_failure_falls_back_to_text(
        self, db_session: Session, tmp_path: Path, monkeypatch
    ) -> None:
        existing = _creative_with_video(db_session, name="fallback-target")
        monkeypatch.setattr(
            pipeline, "top_creative_matches_by_text",
            lambda _n, _t, _cs, limit=2: [(existing, 0.90)][:limit],
        )

        def _boom(_a: str, _b: str):  # noqa: ANN202
            raise RuntimeError("ffmpeg 炸了")

        monkeypatch.setattr(pipeline, "measure_pair", _boom)
        asset = _new_asset(db_session)
        creative, _ = _cluster(
            db_session, asset, "fallback-target", None, "fallback",
            settings=_settings(tmp_path),
            storage=_FakeStorage(_settings(tmp_path)),
            local_video=_local_video(tmp_path),
        )
        assert creative.id == existing.id
        assert "文本" in _cluster_logs(db_session, existing.id)[0].new_value

    def test_download_failure_falls_back(
        self, db_session: Session, tmp_path: Path, monkeypatch
    ) -> None:
        existing = _creative_with_video(db_session, name="no-download")

        class _BrokenStorage(_FakeStorage):
            def download_to(self, _key: str, _dest: str) -> None:
                raise RuntimeError("S3 down")

        monkeypatch.setattr(
            pipeline, "top_creative_matches_by_text",
            lambda _n, _t, _cs, limit=2: [(existing, 0.90)][:limit],
        )
        asset = _new_asset(db_session)
        creative, _ = _cluster(
            db_session, asset, "no-download", None, "no download",
            settings=_settings(tmp_path),
            storage=_BrokenStorage(_settings(tmp_path)),
            local_video=_local_video(tmp_path),
        )
        assert creative.id == existing.id

    def test_below_borderline_not_measured(
        self, db_session: Session, tmp_path: Path, monkeypatch
    ) -> None:
        existing = _creative_with_video(db_session, name="too-weak")
        monkeypatch.setattr(
            pipeline, "top_creative_matches_by_text",
            lambda _n, _t, _cs, limit=2: [(existing, 0.10)][:limit],
        )
        calls: list[str] = []

        def _spy(a: str, b: str):  # noqa: ANN202
            calls.append(a)
            return None, None, SimpleNamespace(aligned_fraction=0.99)

        monkeypatch.setattr(pipeline, "measure_pair", _spy)
        asset = _new_asset(db_session)
        creative, _ = _cluster(
            db_session, asset, "too-weak", None, "too weak",
            settings=_settings(tmp_path),
            storage=_FakeStorage(_settings(tmp_path)),
            local_video=_local_video(tmp_path),
        )
        assert calls == []  # 0.10 < BORDERLINE_LOW，不花测量成本
        assert creative.id != existing.id

    def test_non_video_asset_skips_measurement(
        self, db_session: Session, tmp_path: Path, monkeypatch
    ) -> None:
        existing = _creative_with_video(db_session, name="image-target")
        monkeypatch.setattr(
            pipeline, "top_creative_matches_by_text",
            lambda _n, _t, _cs, limit=2: [(existing, 0.90)][:limit],
        )

        def _forbidden(_a: str, _b: str):  # noqa: ANN202
            raise AssertionError("非视频素材不应触发测量")

        monkeypatch.setattr(pipeline, "measure_pair", _forbidden)
        asset = _new_asset(
            db_session, filename="cover.png", file_type="image"
        )
        creative, _ = _cluster(
            db_session, asset, "image-target", None, "image",
            settings=_settings(tmp_path),
            storage=_FakeStorage(_settings(tmp_path)),
            local_video=None,
        )
        assert creative.id == existing.id


class TestRepresentativeDrift:
    def test_embedding_running_mean_on_attach(
        self, db_session: Session, tmp_path: Path
    ) -> None:
        creative = Creative(
            id=str(uuid.uuid4()), name="emb-creative",
            representative_embedding=[1.0, 0.0], representative_text="old text",
        )
        old_asset = CreativeAsset(
            id=str(uuid.uuid4()), filename="old.png", file_type="image",
            storage_key=f"test/{uuid.uuid4()}",
        )
        old_variant = CreativeVariant(
            id=str(uuid.uuid4()), creative_id=creative.id,
            asset_id=old_asset.id, name="old",
        )
        db_session.add_all([creative, old_asset, old_variant])
        db_session.flush()

        asset = _new_asset(db_session, filename="new.png", file_type="image")
        result, _ = _cluster(
            db_session, asset, "emb-creative", [0.9, 0.1], "new text",
            settings=_settings(tmp_path),
            storage=_FakeStorage(_settings(tmp_path)),
        )
        assert result.id == creative.id
        # running mean：([1.0, 0.0] * 1 + [0.9, 0.1]) / 2
        assert creative.representative_embedding == pytest.approx([0.95, 0.05])
        assert creative.representative_text == "new text"
