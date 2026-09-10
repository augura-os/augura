"""Post-upload AI pipeline (contract §4/§5):

status processing → resolve OpenAI key → (video: ffmpeg 3 frames /
image: direct) → vision model with strict JSON schema → persist
AnalysisResult → upsert Tags + TagAssignments → embed(summary + tags)
→ cosine-cluster into a Creative (≥ 0.85 joins, else new Creative with
the AI creative_name) → wrap asset in a CreativeVariant → sync Neo4j
→ rebuild the SQL mirror → status completed / failed.

Runs in a FastAPI background task (upload) or synchronously
(POST /analysis). Always uses its own DB session.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import threading
import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import SessionLocal
from app.models import Creative, CreativeAsset
from app.repositories.analysis import AnalysisRepository
from app.repositories.assets import AssetRepository
from app.repositories.creatives import CreativeRepository, VariantRepository
from app.repositories.edit_logs import EditLogRepository
from app.repositories.graph_mirror import rebuild_mirror
from app.repositories.tags import TagRepository
from app.services import graph_sync
from app.services.analysis import AnalysisService
from app.services.clustering import (
    BORDERLINE_LOW,
    CLUSTER_MARGIN,
    CLUSTER_THRESHOLD,
    TEXT_CLUSTER_THRESHOLD,
    ClusterDecision,
    decide_cluster,
    running_mean,
    top_creative_matches,
    top_creative_matches_by_text,
)
from app.services.media import build_contact_sheet, extract_smart_frames
from app.services.merge_measure import (
    MIN_ALIGNED_FRACTION_FOR_AUTO,
    first_video_asset,
)
from app.services.settings import resolve_ai_config
from app.services.storage import StorageService
from app.services.variant_diff import measure_pair

logger = logging.getLogger(__name__)


def cleanup_creative_if_empty(
    db: Session, settings: Settings, creative_id: str
) -> None:
    """Delete a creative that lost its last variant (SQL + Neo4j)."""
    creative_repo = CreativeRepository(db)
    creative = creative_repo.get(creative_id)
    if creative is None or creative_repo.variant_count(creative_id) > 0:
        return
    creative_repo.delete(creative)
    db.commit()
    graph_sync.delete_creative_node(settings, creative_id)


def _fail(db: Session, asset: CreativeAsset, message: str) -> None:
    AssetRepository(db).set_status(asset, "failed", message)
    db.commit()


def _collect_frames(
    settings: Settings,
    storage: StorageService,
    asset: CreativeAsset,
    tmp_dir: Path,
) -> list[tuple[bytes, str]]:
    """Video: scene-aware frames via cut detection (first frame doubles as
    the cached thumbnail; a tiled contact sheet is stored alongside).
    Image: the original file bytes."""
    if asset.file_type == "video":
        local_video = str(tmp_dir / f"source{Path(asset.filename).suffix.lower()}")
        storage.download_to(asset.storage_key, local_video)
        frame_infos = extract_smart_frames(local_video, str(tmp_dir))
        frames = [(Path(path).read_bytes(), "image/jpeg") for path, _ in frame_infos]
        if frames:
            thumb_key = f"{asset.storage_key}.thumb.jpg"
            storage.put_bytes(thumb_key, frames[0][0], "image/jpeg")
            asset.thumbnail_key = thumb_key
            try:
                contact_path = str(tmp_dir / "contact.jpg")
                build_contact_sheet([path for path, _ in frame_infos], contact_path)
                storage.put_bytes(
                    f"{asset.storage_key}.contact.jpg",
                    Path(contact_path).read_bytes(),
                    "image/jpeg",
                )
            except Exception as exc:  # noqa: BLE001 — contact sheet is a bonus
                logger.warning("contact sheet 生成失败（继续分析）: %s", exc)
        return frames
    return [(storage.get_bytes(asset.storage_key), asset.mime_type)]


def _measure_creative_alignment(
    db: Session,
    storage: StorageService,
    local_video: str,
    creative_id: str,
    tmp_dir: Path,
) -> float | None:
    """新素材本地视频 vs 候选族代表视频的帧对齐率；测不了/失败 → None。

    全程容错：测量失败不阻塞入库（调用方回退文本决策）。
    """
    try:
        candidate = first_video_asset(db, creative_id)
        if candidate is None:
            return None
        storage_key, filename = candidate
        candidate_path = str(
            tmp_dir / f"candidate-{creative_id}{Path(filename).suffix.lower()}"
        )
        storage.download_to(storage_key, candidate_path)
        _meta_new, _meta_cand, diff = measure_pair(local_video, candidate_path)
        return diff.aligned_fraction
    except Exception:  # noqa: BLE001 — 测量失败 = 证据不足，回退文本路径
        logger.exception("聚类对齐测量失败 creative=%s", creative_id)
        return None


def _measure_candidates(
    db: Session,
    storage: StorageService,
    local_video: str,
    candidates: list[tuple[Creative, float]],
    decision: ClusterDecision,
) -> list[tuple[Creative, float]]:
    """级联第 2 级：对文本召回候选跑视频帧对齐测量。

    只测文本分 ≥ BORDERLINE_LOW 的候选（控制测量成本）；margin <
    CLUSTER_MARGIN 的双子候选连 top-2 一起测。返回 [(creative, 对齐率)]，
    测量不可用的候选直接缺席。
    """
    eligible = [(c, s) for c, s in candidates if s >= BORDERLINE_LOW]
    if not eligible:
        return []
    twins = decision.margin is not None and decision.margin < CLUSTER_MARGIN
    targets = eligible[:2] if twins else eligible[:1]
    results: list[tuple[Creative, float]] = []
    # 系统临时目录而非 settings.upload_dir（CI 无权限，同 merge_measure）
    tmp_dir = Path(tempfile.mkdtemp(prefix="cluster-measure-"))
    try:
        for creative, _score in targets:
            aligned = _measure_creative_alignment(
                db, storage, local_video, creative.id, tmp_dir
            )
            if aligned is not None:
                results.append((creative, aligned))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    return results


def _cluster(
    db: Session,
    asset: CreativeAsset,
    creative_name: str,
    embedding: list[float] | None,
    text_signature: str,
    *,
    settings: Settings | None = None,
    storage: StorageService | None = None,
    local_video: str | None = None,
) -> tuple[Creative, str | None]:
    """Attach the asset's variant to the best-matching creative (§5).

    三级级联：文本/向量 top-2 召回 → 视频测量裁决（对齐率 ≥ 0.90 直接
    关联，物证优先于文本）→ 文本 margin 决策（top1 过阈值且 margin
    ≥ CLUSTER_MARGIN 才自动归；双子并列/低于阈值一律新建，候选对由
    收件箱合并机制人工裁）。测量不可用（非视频/下载失败）回退文本路径。

    自动归入会写 edit_logs（field="cluster"，auto: 前缀），供校准回路
    统计改判率（人工移动/拆分 = 改判）。

    Returns ``(creative, previous_creative_id)`` — the latter is set when a
    re-analysis moved the variant away from a different creative.
    """
    variant_repo = VariantRepository(db)
    creative_repo = CreativeRepository(db)

    previous_creative_id: str | None = None
    variant = variant_repo.get_by_asset(asset.id)
    if variant is not None:
        previous_creative_id = variant.creative_id

    creatives = creative_repo.list_all()
    if embedding is not None:
        candidates = top_creative_matches(embedding, creatives, limit=2)
        threshold = CLUSTER_THRESHOLD
    else:
        candidates = top_creative_matches_by_text(
            creative_name, text_signature, creatives, limit=2
        )
        threshold = TEXT_CLUSTER_THRESHOLD
    decision = decide_cluster(candidates, threshold)

    # 级联第 2 级：视频测量裁决（需要新素材本地视频 + 存储句柄）
    attach: Creative | None = None
    evidence = ""
    measured: list[tuple[Creative, float]] = []
    if (
        settings is not None
        and storage is not None
        and asset.file_type == "video"
        and local_video is not None
        and Path(local_video).is_file()
    ):
        measured = _measure_candidates(db, storage, local_video, candidates, decision)
    winners = [
        (c, a) for c, a in measured if a >= MIN_ALIGNED_FRACTION_FOR_AUTO
    ]
    if winners:
        attach, aligned = max(winners, key=lambda item: item[1])
        evidence = (
            f"视频帧对齐 {aligned:.0%} ≥ {MIN_ALIGNED_FRACTION_FOR_AUTO:.0%}"
            f"（文本 {decision.score:.2f}）"
        )
    elif decision.action == "attach" and decision.creative is not None:
        attach = decision.creative
        margin_text = (
            f"，margin {decision.margin:.2f}" if decision.margin is not None else ""
        )
        evidence = f"文本 {decision.score:.2f} ≥ {threshold:.2f}{margin_text}"
        if measured:
            aligned_text = " / ".join(f"{a:.0%}" for _c, a in measured)
            evidence += f"；视频对齐 {aligned_text} 未达直接关联阈值"

    if attach is not None:
        creative = attach
        if embedding is not None:
            creative.representative_embedding = running_mean(
                creative.representative_embedding,
                embedding,
                creative_repo.variant_count(creative.id),
            )
        creative.representative_text = text_signature
        EditLogRepository(db).record(
            entity_type="creative",
            entity_id=creative.id,
            action="update",
            field="cluster",
            new_value=f"auto: cluster {Path(asset.filename).stem}（{evidence}）",
        )
    else:
        name = creative_name.strip() or f"Creative · {Path(asset.filename).stem}"
        creative = creative_repo.create(
            name=name,
            representative_embedding=embedding,
            representative_text=text_signature,
        )

    if variant is None:
        variant = variant_repo.create(
            asset_id=asset.id,
            creative_id=creative.id,
            name=Path(asset.filename).stem,
            embedding=embedding,
        )
    else:
        variant.creative_id = creative.id
        variant.embedding = embedding
    db.flush()
    return creative, previous_creative_id


# 分析并发上限：批量上传 = 每文件一个后台任务，每个 pipeline 在整个 LLM
# 调用期间持有一个 DB session，不封顶会把连接池打爆（QueuePool timeout）。
_PIPELINE_SLOTS = threading.Semaphore(get_settings().analysis_concurrency)


def run_analysis_pipeline(asset_id: str) -> None:
    """并发闸门：超出 analysis_concurrency 的任务在此排队（线程阻塞）。"""
    with _PIPELINE_SLOTS:
        _run_analysis_pipeline(asset_id)


def _run_analysis_pipeline(asset_id: str) -> None:
    settings = get_settings()
    storage = StorageService(settings)
    db = SessionLocal()
    tmp_dir = Path(settings.upload_dir) / f"analysis-{asset_id}-{uuid.uuid4().hex[:8]}"
    try:
        asset_repo = AssetRepository(db)
        asset = asset_repo.get(asset_id)
        if asset is None or asset.file_type not in ("video", "image"):
            return

        asset_repo.set_status(asset, "processing", "")
        db.commit()

        config = resolve_ai_config(db, settings)
        if not config.api_key:
            _fail(
                db,
                asset,
                "AI API Key 未配置：请在 Settings 页面填写"
                "（支持 OpenAI / Kimi 等 OpenAI 兼容接口），"
                "或设置环境变量 OPENAI_API_KEY 后重试",
            )
            return

        tmp_dir.mkdir(parents=True, exist_ok=True)
        frames = _collect_frames(settings, storage, asset, tmp_dir)

        service = AnalysisService(config)
        payload = service.analyze_frames(frames, media_type=asset.file_type)  # type: ignore[arg-type]

        analysis = AnalysisRepository(db).upsert(
            asset.id, payload, engine_version=f"auto:{config.vision_model}"
        )
        tags = TagRepository(db).set_asset_tags(asset.id, payload.tags)

        # Embedding is optional: providers without an embeddings endpoint
        # (e.g. Kimi) fall back to local text-similarity clustering.
        embed_text = f"{payload.summary} {' '.join(payload.tags)}"
        try:
            embedding: list[float] | None = service.embed(embed_text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("embedding 不可用，改用文本相似度聚类: %s", exc)
            embedding = None
        if embedding is not None:
            AnalysisRepository(db).set_embedding(analysis, embedding)

        text_signature = f"{payload.creative_name} {' '.join(payload.tags)}"
        # 级联测量裁决用：_collect_frames 已把视频下载到本地临时目录
        local_video: str | None = None
        if asset.file_type == "video":
            downloaded = tmp_dir / f"source{Path(asset.filename).suffix.lower()}"
            if downloaded.is_file():
                local_video = str(downloaded)
        creative, previous_creative_id = _cluster(
            db,
            asset,
            payload.creative_name,
            embedding,
            text_signature,
            settings=settings,
            storage=storage,
            local_video=local_video,
        )
        variant = VariantRepository(db).get_by_asset(asset.id)
        asset_repo.set_status(asset, "completed", "")
        db.commit()

        if (
            previous_creative_id is not None
            and previous_creative_id != creative.id
        ):
            cleanup_creative_if_empty(db, settings, previous_creative_id)

        if variant is not None:
            graph_sync.sync_asset_subgraph(
                settings,
                creative=creative,
                variant=variant,
                asset=asset,
                tags=tags,
            )
        rebuild_mirror(db)

        # 分析完成后自动判定：归族 + 涉及本 creative 的合并预裁（仅建议/
        # 自动级辅助；内部容错，失败不影响上面的 completed 状态）
        from app.services.judge_pipeline import run_post_analysis

        run_post_analysis(db, config, creative.id, settings=settings)
    except Exception as exc:  # noqa: BLE001 — status must become "failed"
        logger.exception("分析流水线失败 asset=%s", asset_id)
        # 404 url.not_found 几乎都是 Base URL 漏了 /v1，给用户可直接行动的提示
        hint = (
            "（请检查 Settings → Base URL 是否完整，例如 https://api.moonshot.cn/v1）"
            if "url.not_found" in str(exc) or "Error code: 404" in str(exc)
            else ""
        )
        try:
            db.rollback()
            failed_asset = AssetRepository(db).get(asset_id)
            if failed_asset is not None:
                _fail(db, failed_asset, f"分析失败：{exc}{hint}")
        except Exception:  # noqa: BLE001
            logger.exception("记录失败状态时出错 asset=%s", asset_id)
    finally:
        db.close()
        shutil.rmtree(tmp_dir, ignore_errors=True)
