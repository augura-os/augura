"""Tests for merge_measure.measure_pair_evidence（tier-2 证据带出）。

下载与 measure_pair 全部 mock；classify 走真实实现（被测对象就是
"测量 diff → 白名单因子"的过滤逻辑）。
"""
from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Creative, CreativeAsset, CreativeVariant
from app.services import merge_measure
from app.services.merge_measure import measure_pair_alignment, measure_pair_evidence
from app.services.variant_diff import VariantDiff, VideoMeta


class _FakeStorage:
    def __init__(self, _settings: Settings) -> None:
        pass

    def download_to(self, _key: str, dest: str) -> None:
        Path(dest).write_bytes(b"fake-video")


def _creative_with_video(db: Session, name: str) -> Creative:
    creative = Creative(id=str(uuid.uuid4()), name=name, representative_text=name)
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


def _meta(duration: float = 30.0) -> VideoMeta:
    return VideoMeta(width=1080, height=1920, duration=duration, has_audio=True)


def _measure(diff: VariantDiff, *, duration_b: float = 30.0):
    def _run(_a: str, _b: str):  # noqa: ANN202
        return _meta(), _meta(duration_b), diff

    return _run


def _pair(db: Session) -> tuple[Creative, Creative]:
    return (
        _creative_with_video(db, "lava-escape-a1-b2-c3"),
        _creative_with_video(db, "lava-escape-x1-y2-z3"),
    )


class TestMeasurePairEvidence:
    def test_intro_sticker_factor_carried(
        self, db_session: Session, monkeypatch
    ) -> None:
        # 15s 前贴 + 30s 主体：对齐率 0.50，classify 判 intro-sticker
        a, b = _pair(db_session)
        diff = VariantDiff(
            aligned_fraction=0.50, unmatched_prefix_sec=15.0,
            unmatched_suffix_sec=0.0,
        )
        monkeypatch.setattr(merge_measure, "StorageService", _FakeStorage)
        monkeypatch.setattr(
            merge_measure, "measure_pair", _measure(diff, duration_b=45.0)
        )
        assert measure_pair_evidence(
            db_session, Settings(), a.id, b.id
        ) == (0.50, "intro-sticker")
        # 薄封装保持旧契约：只回对齐率
        assert measure_pair_alignment(db_session, Settings(), a.id, b.id) == 0.50

    def test_brand_endcard_factor_carried(
        self, db_session: Session, monkeypatch
    ) -> None:
        a, b = _pair(db_session)
        diff = VariantDiff(
            aligned_fraction=0.55, unmatched_prefix_sec=0.0,
            unmatched_suffix_sec=14.0,
        )
        monkeypatch.setattr(merge_measure, "StorageService", _FakeStorage)
        monkeypatch.setattr(
            merge_measure, "measure_pair", _measure(diff, duration_b=44.0)
        )
        assert measure_pair_evidence(
            db_session, Settings(), a.id, b.id
        ) == (0.55, "brand-endcard")

    def test_low_alignment_is_not_a_derivation(
        self, db_session: Session, monkeypatch
    ) -> None:
        # 对齐率 0.45 < 0.50 → classify 判 not-a-derivation → factor None
        a, b = _pair(db_session)
        diff = VariantDiff(
            aligned_fraction=0.45, unmatched_prefix_sec=12.0,
            unmatched_suffix_sec=0.0,
        )
        monkeypatch.setattr(merge_measure, "StorageService", _FakeStorage)
        monkeypatch.setattr(merge_measure, "measure_pair", _measure(diff))
        assert measure_pair_evidence(
            db_session, Settings(), a.id, b.id
        ) == (0.45, None)

    def test_mid_divergence_disqualifies_factor(
        self, db_session: Session, monkeypatch
    ) -> None:
        # 实测重剪案例回归：对齐 66% + 前缀 3s + 中段 10 段发散——主体被
        # 重剪过，远超噪点容忍（≤2 段），不能作为不可逆自动执行的物证
        # → factor None
        a, b = _pair(db_session)
        diff = VariantDiff(
            aligned_fraction=0.66, unmatched_prefix_sec=3.0,
            unmatched_suffix_sec=0.0,
            divergent_segments=[(float(i), float(i) + 1.0) for i in range(10)],
        )
        monkeypatch.setattr(merge_measure, "StorageService", _FakeStorage)
        monkeypatch.setattr(
            merge_measure, "measure_pair", _measure(diff, duration_b=33.0)
        )
        assert measure_pair_evidence(
            db_session, Settings(), a.id, b.id
        ) == (0.66, None)

    def test_minor_divergence_within_noise_tolerance(
        self, db_session: Session, monkeypatch
    ) -> None:
        # 1fps 抽帧的转场帧/字幕噪点会造成 1-2 段假发散：容忍阈值内
        # factor 正常带出；超过阈值（3 段）按重剪拦截
        a, b = _pair(db_session)
        monkeypatch.setattr(merge_measure, "StorageService", _FakeStorage)
        for segments, expected in (
            (1, "intro-sticker"),
            (2, "intro-sticker"),
            (3, None),
        ):
            diff = VariantDiff(
                aligned_fraction=0.66, unmatched_prefix_sec=3.0,
                unmatched_suffix_sec=0.0,
                divergent_segments=[
                    (float(i), float(i) + 1.0) for i in range(segments)
                ],
            )
            monkeypatch.setattr(
                merge_measure, "measure_pair", _measure(diff, duration_b=33.0)
            )
            assert measure_pair_evidence(
                db_session, Settings(), a.id, b.id
            ) == (0.66, expected)

    def test_high_alignment_without_factor(
        self, db_session: Session, monkeypatch
    ) -> None:
        # 高对齐但无边缘未匹配段 → classify 定不了案（factor None），
        # tier-1 通道照旧可用
        a, b = _pair(db_session)
        diff = VariantDiff(
            aligned_fraction=0.95, unmatched_prefix_sec=0.0,
            unmatched_suffix_sec=0.0,
        )
        monkeypatch.setattr(merge_measure, "StorageService", _FakeStorage)
        monkeypatch.setattr(merge_measure, "measure_pair", _measure(diff))
        assert measure_pair_evidence(
            db_session, Settings(), a.id, b.id
        ) == (0.95, None)

    def test_aspect_ratio_factor_filtered(
        self, db_session: Session, monkeypatch
    ) -> None:
        # 画幅豁免路径对齐率可任意低，不在 tier-2 白名单 → factor None
        a, b = _pair(db_session)
        diff = VariantDiff(
            aligned_fraction=0.20, unmatched_prefix_sec=0.0,
            unmatched_suffix_sec=0.0, resolution_changed=True,
            duration_diff_sec=0.3,
        )

        def _run(_a: str, _b: str):  # noqa: ANN202
            return _meta(), VideoMeta(
                width=1920, height=1080, duration=30.3, has_audio=True
            ), diff

        monkeypatch.setattr(merge_measure, "StorageService", _FakeStorage)
        monkeypatch.setattr(merge_measure, "measure_pair", _run)
        assert measure_pair_evidence(
            db_session, Settings(), a.id, b.id
        ) == (0.20, None)

    def test_measure_failure_returns_none(
        self, db_session: Session, monkeypatch
    ) -> None:
        a, b = _pair(db_session)
        monkeypatch.setattr(merge_measure, "StorageService", _FakeStorage)

        def _boom(_a: str, _b: str):  # noqa: ANN202
            raise RuntimeError("ffmpeg 炸了")

        monkeypatch.setattr(merge_measure, "measure_pair", _boom)
        assert measure_pair_evidence(db_session, Settings(), a.id, b.id) is None
        assert measure_pair_alignment(db_session, Settings(), a.id, b.id) is None

    def test_missing_video_returns_none(self, db_session: Session) -> None:
        a = _creative_with_video(db_session, "lava-escape-a1-b2-c3")
        image_only = Creative(
            id=str(uuid.uuid4()), name="cover-only", representative_text="cover"
        )
        db_session.add(image_only)
        db_session.flush()
        assert measure_pair_evidence(
            db_session, Settings(), a.id, image_only.id
        ) is None
