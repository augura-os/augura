"""Tests for variant content measurement (services/variant_diff)."""
from __future__ import annotations

import json
import random
from types import SimpleNamespace

from app.services import variant_diff
from app.services.variant_diff import (
    VideoMeta,
    align_sequences,
    classify,
    probe_metadata,
)


def _hashes(seed: int, count: int) -> list[int]:
    """合成帧哈希序列：随机 64 位整数，两两 Hamming 距离远超阈值。"""
    rng = random.Random(seed)
    return [rng.getrandbits(64) for _ in range(count)]


def _meta(
    width: int = 1920, height: int = 1080, duration: float = 20.0
) -> VideoMeta:
    return VideoMeta(width=width, height=height, duration=duration, has_audio=True)


class TestAlignSequences:
    def test_identical_sequences_align_fully(self) -> None:
        hashes = _hashes(1, 20)
        diff = align_sequences(hashes, list(hashes))
        assert diff.aligned_fraction == 1.0
        assert diff.unmatched_prefix_sec == 0.0
        assert diff.unmatched_suffix_sec == 0.0
        assert diff.divergent_segments == []

    def test_prefix_insert(self) -> None:
        source = _hashes(1, 20)
        target = _hashes(2, 3) + source
        diff = align_sequences(source, target)
        assert diff.unmatched_prefix_sec == 3.0
        assert diff.unmatched_suffix_sec == 0.0
        assert diff.divergent_segments == []
        assert diff.aligned_fraction == 20 / 23

    def test_suffix_insert(self) -> None:
        source = _hashes(1, 20)
        target = source + _hashes(3, 3)
        diff = align_sequences(source, target)
        assert diff.unmatched_prefix_sec == 0.0
        assert diff.unmatched_suffix_sec == 3.0
        assert diff.aligned_fraction == 20 / 23

    def test_middle_replacement(self) -> None:
        source = _hashes(1, 20)
        target = source[:8] + _hashes(4, 4) + source[12:]
        diff = align_sequences(source, target)
        assert diff.divergent_segments == [(8.0, 12.0)]
        assert diff.unmatched_prefix_sec == 0.0
        assert diff.unmatched_suffix_sec == 0.0
        assert diff.aligned_fraction == 0.8

    def test_unrelated_sequences(self) -> None:
        diff = align_sequences(_hashes(1, 20), _hashes(5, 20))
        assert diff.aligned_fraction < 0.5

    def test_empty_sequence(self) -> None:
        diff = align_sequences([], _hashes(1, 5))
        assert diff.aligned_fraction == 0.0


class TestClassify:
    def test_resolution_change_is_aspect_ratio(self) -> None:
        hashes = _hashes(1, 20)
        diff = align_sequences(hashes, list(hashes))
        diff.resolution_changed = True
        factor, evidence = classify(diff, _meta(), _meta(1080, 1920))
        assert factor == "aspect-ratio"
        assert "1920x1080" in evidence and "1080x1920" in evidence

    def test_resolution_change_with_low_alignment_but_same_duration(self) -> None:
        # 横竖裁剪会破坏 pHash 对齐；时长一致时仍按画幅裂变量案
        diff = align_sequences(_hashes(1, 20), _hashes(5, 20))
        diff.resolution_changed = True
        diff.duration_diff_sec = 0.3
        factor, _ = classify(diff, _meta(), _meta(1080, 1920))
        assert factor == "aspect-ratio"

    def test_same_ratio_downscale_with_low_alignment_is_not_exempt(self) -> None:
        # 1080x1920 → 720x1280 同为 9:16，pHash 对缩放不敏感——
        # 对齐率低说明内容真的不同，不能按画幅裂变豁免（dry-run 实测回归）
        diff = align_sequences(_hashes(1, 20), _hashes(5, 20))
        diff.resolution_changed = True
        diff.duration_diff_sec = 0.3
        factor, _ = classify(diff, _meta(1080, 1920), _meta(720, 1280))
        assert factor == "not-a-derivation"

    def test_prefix_insert_is_intro_sticker(self) -> None:
        source = _hashes(1, 20)
        diff = align_sequences(source, _hashes(2, 3) + source)
        factor, evidence = classify(diff, _meta(), _meta(duration=23.0))
        assert factor == "intro-sticker"
        assert "3.0s" in evidence

    def test_suffix_insert_is_brand_endcard(self) -> None:
        source = _hashes(1, 20)
        diff = align_sequences(source, source + _hashes(3, 3))
        factor, _ = classify(diff, _meta(), _meta(duration=23.0))
        assert factor == "brand-endcard"

    def test_unrelated_is_not_a_derivation(self) -> None:
        diff = align_sequences(_hashes(1, 20), _hashes(5, 20))
        factor, _ = classify(diff, _meta(), _meta())
        assert factor == "not-a-derivation"

    def test_middle_replacement_left_for_llm(self) -> None:
        source = _hashes(1, 20)
        diff = align_sequences(source, source[:8] + _hashes(4, 4) + source[12:])
        factor, evidence = classify(diff, _meta(), _meta())
        assert factor is None
        assert evidence


class TestProbeMetadata:
    def _fake_run(self, payload: dict):
        return lambda cmd: SimpleNamespace(
            stdout=json.dumps(payload), stderr="", returncode=0
        )

    def test_parses_ffprobe_json(self, monkeypatch) -> None:  # noqa: ANN001
        payload = {
            "streams": [
                {"codec_type": "video", "width": 1920, "height": 1080},
                {"codec_type": "audio"},
            ],
            "format": {"duration": "12.5"},
        }
        monkeypatch.setattr(variant_diff, "_run", self._fake_run(payload))
        assert probe_metadata("fake.mp4") == VideoMeta(
            width=1920, height=1080, duration=12.5, has_audio=True
        )

    def test_no_audio_and_bad_duration(self, monkeypatch) -> None:  # noqa: ANN001
        payload = {
            "streams": [{"codec_type": "video", "width": 1080, "height": 1920}],
            "format": {"duration": "N/A"},
        }
        monkeypatch.setattr(variant_diff, "_run", self._fake_run(payload))
        assert probe_metadata("fake.mp4") == VideoMeta(
            width=1080, height=1920, duration=0.0, has_audio=False
        )

    def test_garbage_output_returns_zero_meta(self, monkeypatch) -> None:  # noqa: ANN001
        monkeypatch.setattr(
            variant_diff, "_run",
            lambda cmd: SimpleNamespace(stdout="not json", stderr="", returncode=1),
        )
        assert probe_metadata("fake.mp4") == VideoMeta(
            width=0, height=0, duration=0.0, has_audio=False
        )
