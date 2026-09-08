"""裂变测量层：基于视频内容的 DERIVED_FROM 因子判定（Layer 0-2）。

原则：能测量的不猜测。画幅/时长这种 ffprobe 一秒出真相的事不靠
文件名或 LLM；前贴/尾帧用帧级 pHash 时序对齐测量发散位置；中段
大面积发散或整体对齐率过低留给 derivation_judge / 人工仲裁。

对齐算法是视频拷贝检测（VCD/NDVR）标准管线「帧特征 + 帧间相似度
矩阵 + 对角线检测」的简化版——场景是同源素材小改动，不需要完整
DTW：枚举 source/target 的帧偏移 k，取匹配对数最多的对角线为对齐
路径，未匹配的头尾段与中段发散区间即改动点。
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import imagehash
from PIL import Image

from app.services.media import _run

# 帧间 Hamming 距离 ≤ 此值视为同帧（pHash 64 位，同源小改动容差）
DEFAULT_MAX_HAMMING = 10
# target 多出 ≥ 此时长的未匹配头/尾段 → 前贴 / 尾帧裂变
MIN_UNMATCHED_EDGE_SEC = 2.0
# 可对齐比例低于此值 → 疑似误链（not-a-derivation 候选）
MIN_ALIGNED_FRACTION = 0.5
# 画幅比变化 + 时长差 ≤ 此值时，即使对齐率低也按画幅裂变量案
# （横竖裁剪本身会破坏 pHash 对齐，时长是独立的同源佐证）；
# 同比例降分辨率不适用——pHash 对缩放不敏感，对齐率低即内容不同
MAX_DURATION_DIFF_SEC = 1.5


@dataclass
class VideoMeta:
    width: int
    height: int
    duration: float
    has_audio: bool


@dataclass
class VariantDiff:
    """两条变体视频的测量差异证据（秒数按抽帧 fps=1 折算）。"""

    aligned_fraction: float  # 可对齐帧数 / 较长序列帧数
    unmatched_prefix_sec: float  # target 开头未被匹配的时长
    unmatched_suffix_sec: float  # target 结尾未被匹配的时长
    divergent_segments: list[tuple[float, float]] = field(default_factory=list)
    resolution_changed: bool = False
    duration_diff_sec: float = 0.0  # target - source（有符号）
    audio_same: bool | None = None  # None = 未测（无 fpcalc）


def probe_metadata(video_path: str) -> VideoMeta:
    """ffprobe 读 width/height/duration/有无音轨（ground truth，零猜测）。"""
    result = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,width,height:format=duration",
            "-of",
            "json",
            video_path,
        ]
    )
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        payload = {}
    width = height = 0
    has_audio = False
    for stream in payload.get("streams") or []:
        codec = stream.get("codec_type")
        if codec == "video" and not width:
            width = int(stream.get("width") or 0)
            height = int(stream.get("height") or 0)
        elif codec == "audio":
            has_audio = True
    try:
        duration = max(float((payload.get("format") or {}).get("duration") or 0.0), 0.0)
    except ValueError:
        duration = 0.0
    return VideoMeta(width=width, height=height, duration=duration, has_audio=has_audio)


def frame_phash_sequence(video_path: str, fps: float = 1.0) -> list[int]:
    """ffmpeg 低帧率抽帧到临时目录 + imagehash.phash，返回 64 位整数序列。"""
    tmp_dir = Path(tempfile.mkdtemp(prefix="phash-"))
    try:
        pattern = str(tmp_dir / "f%05d.jpg")
        result = _run(
            ["ffmpeg", "-y", "-v", "error", "-i", video_path,
             "-vf", f"fps={fps}", "-q:v", "3", pattern]
        )
        frames = sorted(tmp_dir.glob("f*.jpg"))
        if result.returncode != 0 or not frames:
            raise RuntimeError(f"ffmpeg 低帧率抽帧失败: {result.stderr.strip()[-300:]}")
        return [int(str(imagehash.phash(Image.open(path))), 16) for path in frames]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def align_sequences(
    source_hashes: list[int],
    target_hashes: list[int],
    max_hamming: int = DEFAULT_MAX_HAMMING,
) -> VariantDiff:
    """对角线对齐：枚举帧偏移 k = target_idx - source_idx，取匹配对数最多
    （平局取 |k| 最小）的对角线为对齐路径。假设抽帧 fps=1，帧号即秒数。"""
    n, m = len(source_hashes), len(target_hashes)
    if n == 0 or m == 0:
        return VariantDiff(
            aligned_fraction=0.0,
            unmatched_prefix_sec=0.0,
            unmatched_suffix_sec=0.0,
        )

    def _matches(k: int) -> list[tuple[int, int]]:
        lo, hi = max(0, -k), min(n, m - k)
        return [
            (i, i + k)
            for i in range(lo, hi)
            if _hamming(source_hashes[i], target_hashes[i + k]) <= max_hamming
        ]

    best_k, best_pairs = 0, _matches(0)
    for k in range(-(n - 1), m):
        if k == 0:
            continue
        pairs = _matches(k)
        if len(pairs) > len(best_pairs) or (
            len(pairs) == len(best_pairs) and abs(k) < abs(best_k)
        ):
            best_k, best_pairs = k, pairs

    aligned_fraction = len(best_pairs) / max(n, m)
    if not best_pairs:
        return VariantDiff(
            aligned_fraction=0.0,
            unmatched_prefix_sec=0.0,
            unmatched_suffix_sec=0.0,
        )

    matched_source = {i for i, _j in best_pairs}
    first_target, last_target = best_pairs[0][1], best_pairs[-1][1]
    # 中段发散：首个与末个匹配 source 帧之间的连续未匹配区间（source 时间轴）
    divergent: list[tuple[float, float]] = []
    run_start: int | None = None
    for i in range(best_pairs[0][0], best_pairs[-1][0] + 1):
        if i in matched_source:
            if run_start is not None:
                divergent.append((float(run_start), float(i)))
                run_start = None
        elif run_start is None:
            run_start = i
    if run_start is not None:
        divergent.append((float(run_start), float(best_pairs[-1][0] + 1)))

    return VariantDiff(
        aligned_fraction=aligned_fraction,
        unmatched_prefix_sec=float(first_target),
        unmatched_suffix_sec=float(m - 1 - last_target),
        divergent_segments=divergent,
    )


def audio_fingerprint_same(source_path: str, target_path: str) -> bool | None:
    """fpcalc（Chromaprint）音轨指纹对比；系统无 fpcalc 或提取失败返回 None。"""
    if shutil.which("fpcalc") is None:
        return None
    fingerprints: list[str] = []
    for path in (source_path, target_path):
        result = _run(["fpcalc", path])
        if result.returncode != 0:
            return None
        match = re.search(r"^FINGERPRINT=(.+)$", result.stdout, re.MULTILINE)
        if not match:
            return None
        fingerprints.append(match.group(1).strip())
    return fingerprints[0] == fingerprints[1]


def measure_pair(
    source_path: str, target_path: str, fps: float = 1.0
) -> tuple[VideoMeta, VideoMeta, VariantDiff]:
    """Layer 0-2 一把梭：元数据 + 帧对齐 + 可选音频指纹。"""
    source_meta = probe_metadata(source_path)
    target_meta = probe_metadata(target_path)
    diff = align_sequences(
        frame_phash_sequence(source_path, fps),
        frame_phash_sequence(target_path, fps),
    )
    if source_meta.width and target_meta.width:
        diff.resolution_changed = (source_meta.width, source_meta.height) != (
            target_meta.width,
            target_meta.height,
        )
    diff.duration_diff_sec = target_meta.duration - source_meta.duration
    diff.audio_same = audio_fingerprint_same(source_path, target_path)
    return source_meta, target_meta, diff


def describe(diff: VariantDiff, source_meta: VideoMeta, target_meta: VideoMeta) -> str:
    """测量证据的一句话文本（打印 + 喂给 LLM 归因）。"""
    if diff.audio_same is None:
        audio = "音轨未测"
    else:
        audio = "音轨一致" if diff.audio_same else "音轨不同"
    divergent = "、".join(f"{a:.0f}-{b:.0f}s" for a, b in diff.divergent_segments) or "无"
    return (
        f"source {source_meta.width}x{source_meta.height} {source_meta.duration:.1f}s"
        f" → target {target_meta.width}x{target_meta.height} "
        f"{target_meta.duration:.1f}s；对齐比例 {diff.aligned_fraction:.0%}；"
        f"未匹配前缀 {diff.unmatched_prefix_sec:.1f}s；"
        f"未匹配后缀 {diff.unmatched_suffix_sec:.1f}s；"
        f"中段发散 {divergent}；{audio}"
    )


def _ratio(meta: VideoMeta) -> float:
    return meta.width / meta.height if meta.width and meta.height else 0.0


def classify(
    diff: VariantDiff, source_meta: VideoMeta, target_meta: VideoMeta
) -> tuple[str | None, str]:
    """确定性归因：返回 (factor | "not-a-derivation" | None, 证据文本)。

    None = 测量定不了案（中段发散的换皮类），留给 derivation_judge。
    """
    well_aligned = diff.aligned_fraction >= MIN_ALIGNED_FRACTION
    # 豁免只给"真·画幅比变化"（横竖裁剪会破坏 pHash 对齐，用时长佐证）；
    # 同比例降分辨率不破坏对齐——对齐率低就是内容真的不同，不能豁免
    ratio_changed = abs(_ratio(source_meta) - _ratio(target_meta)) > 0.02
    if diff.resolution_changed and (
        well_aligned
        or (ratio_changed and abs(diff.duration_diff_sec) <= MAX_DURATION_DIFF_SEC)
    ):
        return "aspect-ratio", (
            f"分辨率 {source_meta.width}x{source_meta.height} → "
            f"{target_meta.width}x{target_meta.height}"
        )
    if diff.unmatched_prefix_sec >= MIN_UNMATCHED_EDGE_SEC and well_aligned:
        return "intro-sticker", (
            f"target 多出 {diff.unmatched_prefix_sec:.1f}s 未匹配片头"
            f"（对齐比例 {diff.aligned_fraction:.0%}）"
        )
    if diff.unmatched_suffix_sec >= MIN_UNMATCHED_EDGE_SEC and well_aligned:
        return "brand-endcard", (
            f"target 多出 {diff.unmatched_suffix_sec:.1f}s 未匹配结尾"
            f"（对齐比例 {diff.aligned_fraction:.0%}）"
        )
    if not well_aligned:
        return "not-a-derivation", (
            f"可对齐比例仅 {diff.aligned_fraction:.0%}，两条素材疑似无同源关系"
        )
    return None, (
        f"对齐比例 {diff.aligned_fraction:.0%}，"
        f"中段发散 {len(diff.divergent_segments)} 段，测量无法定案"
    )
