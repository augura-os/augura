"""ffmpeg helpers: video frame extraction + on-demand video thumbnails."""

from __future__ import annotations

import re
import shutil
import subprocess
import uuid
from math import ceil
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import Settings
from app.models import CreativeAsset
from app.services.storage import StorageService

# Scene-change extraction (adopted from den-creative-framework):
# detect real cut points instead of fixed first/middle/last sampling.
SCENE_THRESHOLD = 0.23
SMART_FRAME_COUNT = 8
CONTACT_SHEET_COLS = 4


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False)


def probe_duration_seconds(video_path: str) -> float:
    result = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            video_path,
        ]
    )
    try:
        return max(float(result.stdout.strip()), 0.0)
    except ValueError:
        return 0.0


def extract_frame_at(video_path: str, seconds: float, out_path: str) -> None:
    """Extract a single JPEG frame; falls back to decoding from the start
    when fast-seek fails on the container."""
    base = ["ffmpeg", "-y", "-v", "error"]
    result = _run(
        base
        + ["-ss", f"{max(seconds, 0.0):.3f}", "-i", video_path]
        + ["-frames:v", "1", "-q:v", "3", out_path]
    )
    if result.returncode != 0 or not Path(out_path).exists():
        result = _run(
            base + ["-i", video_path, "-frames:v", "1", "-q:v", "3", out_path]
        )
    if result.returncode != 0 or not Path(out_path).exists():
        raise RuntimeError(f"ffmpeg 抽帧失败: {result.stderr.strip()[-300:]}")


def extract_first_frame(video_path: str, out_path: str) -> None:
    extract_frame_at(video_path, 0.0, out_path)


def extract_frames(video_path: str, out_dir: str, count: int = 3) -> list[str]:
    """Extract ``count`` frames (first / middle / last) as JPEG paths."""
    duration = probe_duration_seconds(video_path)
    if count <= 1 or duration <= 0.0:
        times = [0.0]
    else:
        times = [0.0, duration / 2.0, max(duration - 0.2, 0.0)][:count]
    paths: list[str] = []
    for index, seconds in enumerate(times):
        out_path = str(Path(out_dir) / f"frame-{index}.jpg")
        extract_frame_at(video_path, seconds, out_path)
        paths.append(out_path)
    return paths


def ensure_video_thumbnail(
    db: Session,
    settings: Settings,
    storage: StorageService,
    asset: CreativeAsset,
) -> str:
    """Return the MinIO key of the video's cached first-frame thumbnail,
    extracting and storing it as ``<storage_key>.thumb.jpg`` on first use."""
    if asset.thumbnail_key and storage.stat(asset.thumbnail_key) is not None:
        return asset.thumbnail_key
    thumb_key = f"{asset.storage_key}.thumb.jpg"
    if storage.stat(thumb_key) is None:
        tmp_dir = Path(settings.upload_dir) / f"thumb-{asset.id}-{uuid.uuid4().hex[:8]}"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        try:
            local_video = str(tmp_dir / "source")
            storage.download_to(asset.storage_key, local_video)
            local_thumb = str(tmp_dir / "thumb.jpg")
            extract_first_frame(local_video, local_thumb)
            storage.put_bytes(thumb_key, Path(local_thumb).read_bytes(), "image/jpeg")
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
    asset.thumbnail_key = thumb_key
    db.commit()
    return thumb_key


_SCENE_TIME_RE = re.compile(r"pts_time:([0-9.]+)")


def detect_scene_times(video_path: str, threshold: float = SCENE_THRESHOLD) -> list[float]:
    """Cut-point timestamps via ffmpeg scene-score filter (den-framework §4.1
    ``-SceneThreshold``)."""
    result = _run(
        [
            "ffmpeg",
            "-v",
            "info",
            "-i",
            video_path,
            "-vf",
            f"select='gt(scene,{threshold})',showinfo",
            "-f",
            "null",
            "-",
        ]
    )
    return sorted({float(hit) for hit in _SCENE_TIME_RE.findall(result.stderr)})


def _dedupe_times(times: list[float], min_gap: float) -> list[float]:
    kept: list[float] = []
    for moment in sorted(times):
        if not kept or moment - kept[-1] >= min_gap:
            kept.append(moment)
    return kept


def extract_smart_frames(
    video_path: str,
    out_dir: str,
    count: int = SMART_FRAME_COUNT,
    threshold: float = SCENE_THRESHOLD,
) -> list[tuple[str, float]]:
    """Scene-aware frame extraction (den-framework): first frame + real cut
    points + final frame; uniform sampling fills the remainder when a video
    has few cuts. Returns ``(path, seconds)`` pairs in time order."""
    duration = probe_duration_seconds(video_path)
    end = max(duration - 0.2, 0.0)
    candidates = [0.0] + detect_scene_times(video_path, threshold)
    if duration > 0.0:
        candidates.append(end)
    min_gap = duration / (count * 2) if duration > 0.0 else 1.0
    times = _dedupe_times([t for t in candidates if 0.0 <= t <= end + 0.21], min_gap)
    if len(times) > count:
        head, middle, tail = times[0], times[1:-1], times[-1]
        step = len(middle) / max(count - 2, 1)
        picked = [middle[int(i * step)] for i in range(count - 2)] if middle else []
        times = ([head] + picked + [tail]) if duration > 0.0 else [head]
    if len(times) < count and duration > 0.0:
        uniform = [duration * i / max(count - 1, 1) for i in range(count)]
        times = _dedupe_times(sorted(times + uniform), 0.05)[:count]

    frames: list[tuple[str, float]] = []
    for index, seconds in enumerate(times):
        out_path = str(Path(out_dir) / f"frame-{index:02d}.jpg")
        extract_frame_at(video_path, seconds, out_path)
        frames.append((out_path, seconds))
    return frames


def build_contact_sheet(
    frame_paths: list[str], out_path: str, cols: int = CONTACT_SHEET_COLS
) -> None:
    """Tile frames into one contact-sheet image (den-framework §1)."""
    if not frame_paths:
        raise RuntimeError("没有可用于拼图的帧")
    count = len(frame_paths)
    rows = max(1, ceil(count / cols))
    command = ["ffmpeg", "-y", "-v", "error"]
    for path in frame_paths:
        command += ["-i", path]
    scales = "".join(f"[{i}:v]scale=320:-2[s{i}];" for i in range(count))
    concat = "".join(f"[s{i}]" for i in range(count))
    filter_graph = (
        f"{scales}{concat}concat=n={count}:v=1:a=0[seq];"
        f"[seq]tile={cols}x{rows}:padding=6:margin=6:color=white[out]"
    )
    command += ["-filter_complex", filter_graph, "-map", "[out]", "-frames:v", "1", out_path]
    result = _run(command)
    if result.returncode != 0 or not Path(out_path).exists():
        raise RuntimeError(f"contact sheet 生成失败: {result.stderr.strip()[-300:]}")


def ensure_video_contact_sheet(
    db: Session,
    settings: Settings,
    storage: StorageService,
    asset: CreativeAsset,
) -> str:
    """MinIO key of the video's contact sheet, generated on first use."""
    contact_key = f"{asset.storage_key}.contact.jpg"
    if storage.stat(contact_key) is not None:
        return contact_key
    tmp_dir = Path(settings.upload_dir) / f"contact-{asset.id}-{uuid.uuid4().hex[:8]}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    try:
        local_video = str(tmp_dir / f"source{Path(asset.filename).suffix.lower()}")
        storage.download_to(asset.storage_key, local_video)
        frames = extract_smart_frames(local_video, str(tmp_dir))
        build_contact_sheet([path for path, _ in frames], str(tmp_dir / "contact.jpg"))
        storage.put_bytes(
            contact_key,
            (tmp_dir / "contact.jpg").read_bytes(),
            "image/jpeg",
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    return contact_key
