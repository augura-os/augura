"""Post-upload AI pipeline (contract §4/§5):

status processing → resolve OpenAI key → (video: ffmpeg 3 frames /
image: direct) → vision model with strict JSON schema → persist
AnalysisResult → upsert Tags + TagAssignments → embed(summary + tags,
shadow 只写入) → text-similarity cluster into a Creative (≥ 0.34 joins,
else new Creative with the AI creative_name) → wrap asset in a
CreativeVariant → sync Neo4j → rebuild the SQL mirror → status
completed / failed.

attach 判定恒走文本通道（设计 §3.2 shadow 默认）；embedding 向量只为
E2 的漏合并召回积累数据。

Runs synchronously (POST /analysis) or in the standalone worker process
(``python -m app.worker``) driven by durable ``analysis_jobs`` rows.
Always uses its own DB session.
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
from app.models import AnalysisResult, Creative, CreativeAsset
from app.repositories.analysis import AnalysisRepository
from app.repositories.assets import AssetRepository
from app.repositories.creatives import CreativeRepository, VariantRepository
from app.repositories.edit_logs import EditLogRepository
from app.repositories.graph_mirror import rebuild_mirror
from app.repositories.jobs import JobRepository
from app.repositories.tags import TagRepository
from app.schemas.analysis import AnalysisPayload
from app.services import graph_sync
from app.services.analysis import AnalysisService
from app.services.clustering import (
    BORDERLINE_LOW,
    CLUSTER_MARGIN,
    TEXT_CLUSTER_THRESHOLD,
    ClusterDecision,
    decide_cluster,
    merge_embedding,
    top_creative_matches_by_text,
)
from app.services.embedding import (
    embed_analysis_text,
    recompute_creative_representative,
    recorded_embedding_model_id,
)
from app.services.judge_calibration import judge_auto_allowed
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
    embedding_model: str | None = None,
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

    ``embedding_model`` 是 ``embedding`` 的行级 provenance 戳（向量写 None
    时戳同步清 None——同生同灭）。

    Returns ``(creative, previous_creative_id)`` — the latter is set when a
    re-analysis moved the variant away from a different creative.
    """
    variant_repo = VariantRepository(db)
    creative_repo = CreativeRepository(db)
    # 戳与向量同生同灭：本次向量缺失（embed 降级/复用到 NULL）时戳也为 None
    stamp = embedding_model if embedding is not None else None

    previous_creative_id: str | None = None
    variant = variant_repo.get_by_asset(asset.id)
    if variant is not None:
        previous_creative_id = variant.creative_id

    creatives = creative_repo.list_all()
    # shadow 默认（设计 §3.2，E0 拍板）：attach 判定永远走文本通道——
    # embedding 只写入存储（representative/variant/analysis 三处），
    # 不接管判定；向量召回通道在 E2 接 missed_merge_scan 候选并集。
    candidates = top_creative_matches_by_text(
        creative_name, text_signature, creatives, limit=2
    )
    threshold = TEXT_CLUSTER_THRESHOLD
    decision = decide_cluster(candidates, threshold)

    # 级联第 2 级：视频测量裁决（需要新素材本地视频 + 存储句柄）
    attach: Creative | None = None
    evidence = ""
    measured: list[tuple[Creative, float]] = []
    # cluster 类别闸（P0-2 两层模型：类别级失控只停本类别自动执行）：
    # 刹车期间不做自动归入——新素材落散点（新建 creative），连测量成本
    # 一起省；与 dna_assign/merge_pair 同一降级哲学
    cluster_auto = judge_auto_allowed(db, "cluster")
    if (
        cluster_auto
        and settings is not None
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
    elif cluster_auto and decision.action == "attach" and decision.creative is not None:
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
            # P0-3：count 用实际参与过均值的向量条数（embedding_count），
            # 不是全部 variant 数（历史无向量 variant 不参与均值）
            creative.representative_embedding, creative.embedding_count = (
                merge_embedding(
                    creative.representative_embedding,
                    creative.embedding_count,
                    embedding,
                )
            )
            # 均值仍同源（一致性机制保证成员向量同模型）→ 戳随向量更新
            creative.embedding_model = stamp
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
            embedding_count=1 if embedding is not None else 0,
            embedding_model=stamp,
        )

    if variant is None:
        variant = variant_repo.create(
            asset_id=asset.id,
            creative_id=creative.id,
            name=Path(asset.filename).stem,
            embedding=embedding,
            embedding_model=stamp,
        )
    else:
        variant.creative_id = creative.id
        variant.embedding = embedding
        variant.embedding_model = stamp
    db.flush()
    return creative, previous_creative_id


class MissingAIKeyError(RuntimeError):
    """AI key 未配置：worker 归类为 waiting_user（提示去 Settings），不重试。"""


# 分析并发上限：POST /analysis 同步入口的进程内闸门，每个 pipeline 在 LLM
# 调用期间持有 DB session，不封顶会把连接池打爆（QueuePool timeout）。
# F3 双槽模型：本槽只覆盖分析核心（_run_analysis_pipeline：抽帧/LLM/嵌入/
# 聚类 + completed 落库 + 紧随的重算/图同步/镜像重建）；判定阶段在槽释放后
# 运行，用 judge_pipeline._JUDGE_SLOTS 独立限流——判定挂起不再堵死分析通道
# （事故时判定共槽，LLM 卡住 → 槽不释放 → 37 个上传任务占满线程池，全站挂起）。
# 批量上传不走本进程：upload 只写 analysis_jobs，worker 进程的有界线程池
# 承担并发闸门，不再经过 _PIPELINE_SLOTS。
_PIPELINE_SLOTS = threading.Semaphore(get_settings().analysis_concurrency)


def run_analysis_pipeline(asset_id: str) -> None:
    """并发闸门：分析核心在 _PIPELINE_SLOTS 下运行；判定阶段槽外独立限流。"""
    with _PIPELINE_SLOTS:
        creative_id = _run_analysis_pipeline(asset_id)
    if creative_id is not None:
        _run_judge_phase(creative_id)


def _run_judge_phase(creative_id: str) -> None:
    """判定阶段（归族 + 合并预裁 + 漏网扫描 + 巩固）：分析槽外、判定槽内。

    独立 session（照 run_analysis_pipeline 模式）；run_post_analysis 内部
    已全程容错，外层 try 只是兜底——判定失败绝不翻转已提交的 completed。
    """
    from app.services.judge_pipeline import _JUDGE_SLOTS, run_post_analysis

    with _JUDGE_SLOTS:
        settings = get_settings()
        db = SessionLocal()
        try:
            config = resolve_ai_config(db, settings)
            run_post_analysis(db, config, creative_id, settings=settings)
        except Exception:  # noqa: BLE001 — 判定失败不拖垮上传后台任务
            logger.exception("判定阶段失败 creative=%s", creative_id)
        finally:
            db.close()


def _update_job_stage(job_id: str | None, stage: str) -> None:
    """Best-effort 进度上报（独立短 session，不掺和 pipeline 自身事务）。"""
    if job_id is None:
        return
    try:
        with SessionLocal() as session:
            job = JobRepository(session).get(job_id)
            if job is not None:
                job.stage = stage
                session.commit()
    except Exception:  # noqa: BLE001 — stage 只是可观测性，不能影响主流程
        logger.warning("更新 job stage 失败 job=%s stage=%s", job_id, stage)


def _payload_from_result(result: AnalysisResult) -> AnalysisPayload:
    """从已存的 AnalysisResult 重组 Vision payload（崩溃重跑时跳过 Vision）。"""
    return AnalysisPayload(
        summary=result.summary,
        hook=result.hook,
        conflict=result.conflict,
        gameplay=result.gameplay,
        reward=result.reward,
        characters=list(result.characters or []),
        environment=list(result.environment or []),
        emotion=list(result.emotion or []),
        tags=list(result.tags or []),
        variant_factors=list(result.variant_factors or []),
        creative_name=result.creative_name,
        confidence=result.confidence,
    )


def _redownload_for_measure(
    storage: StorageService, asset: CreativeAsset, tmp_dir: Path
) -> str | None:
    """重跑（跳过 Vision/抽帧）时视频不在本地，重新下载供级联测量裁决。"""
    local = str(tmp_dir / f"source{Path(asset.filename).suffix.lower()}")
    try:
        storage.download_to(asset.storage_key, local)
    except Exception:  # noqa: BLE001 — 下载失败回退纯文本决策
        logger.warning("重跑视频下载失败，聚类回退文本路径 asset=%s", asset.id)
        return None
    return local


def _run_analysis_pipeline(
    asset_id: str,
    *,
    job_id: str | None = None,
    raise_on_error: bool = False,
) -> str | None:
    """分析核心；成功返回 creative.id（供判定阶段用），失败/跳过返回 None。

    ``job_id``：worker 驱动时在关键节点上报 job.stage。
    ``raise_on_error``：worker 需要原始异常做重试分类；默认 False 保持
    POST /analysis 的旧行为（异常吞掉、asset 置 failed）。
    幂等：已有同 engine_version 的 AnalysisResult 时跳过抽帧/Vision/
    embedding，直接用已存 payload 走聚类段（聚类本身幂等：variant 复用）。
    判定（归族/合并预裁/漏网扫描/巩固）不在这里跑——由调用方
    （run_analysis_pipeline / worker）在分析槽释放后调 _run_judge_phase
    （F3 双槽模型），本函数只负责把 creative.id 交出去。
    """
    settings = get_settings()
    storage = StorageService(settings)
    db = SessionLocal()
    tmp_dir = Path(settings.upload_dir) / f"analysis-{asset_id}-{uuid.uuid4().hex[:8]}"
    try:
        asset_repo = AssetRepository(db)
        asset = asset_repo.get(asset_id)
        if asset is None or asset.file_type not in ("video", "image"):
            return None

        asset_repo.set_status(asset, "processing", "")
        db.commit()

        config = resolve_ai_config(db, settings)
        if not config.api_key:
            message = (
                "AI API Key 未配置：请在 Settings 页面填写"
                "（支持 OpenAI / Kimi 等 OpenAI 兼容接口），"
                "或设置环境变量 OPENAI_API_KEY 后重试"
            )
            _fail(db, asset, message)
            if raise_on_error:
                raise MissingAIKeyError(message)
            return None

        tmp_dir.mkdir(parents=True, exist_ok=True)
        engine_version = f"auto:{config.vision_model}"
        analysis_repo = AnalysisRepository(db)
        existing = analysis_repo.get_by_asset(asset.id)
        resume = existing is not None and existing.engine_version == engine_version
        local_video: str | None = None

        if resume:
            # Vision 已成功过（崩溃重跑/429 重试）：跳过抽帧 + Vision +
            # embedding，用已存 payload 直接走聚类段。
            assert existing is not None  # resume implies existing
            payload = _payload_from_result(existing)
            embedding: list[float] | None = existing.embedding
            # 向量是上次分析产出的——provenance 戳沿用 analysis 行上的原戳
            # （迁移前的存量行为 None = 未知模型，语义正确）
            embedding_model = existing.embedding_model
            tags = TagRepository(db).set_asset_tags(asset.id, payload.tags)
            if asset.file_type == "video":
                local_video = _redownload_for_measure(storage, asset, tmp_dir)
        else:
            _update_job_stage(job_id, "frames")
            frames = _collect_frames(settings, storage, asset, tmp_dir)

            _update_job_stage(job_id, "vision")
            service = AnalysisService(config)
            payload = service.analyze_frames(frames, media_type=asset.file_type)  # type: ignore[arg-type]

            analysis = analysis_repo.upsert(
                asset.id, payload, engine_version=engine_version
            )
            tags = TagRepository(db).set_asset_tags(asset.id, payload.tags)

            # Embedding 只写入（shadow）：抽象层按 embedding_backend 分发
            # （off/provider/local），失败降级 None——attach 判定恒走文本通道。
            # 嵌入文本 = summary + tags（E0 对照实验拍板，见 E0 报告 §5）。
            embed_text = f"{payload.summary} {' '.join(payload.tags)}"
            embedding = embed_analysis_text(db, config, embed_text)
            # 行级 provenance 戳：embed 成功时 settings 已记录 active id
            # （_ensure_model_consistency），直接取记录值；向量 None 戳也 None
            embedding_model = (
                recorded_embedding_model_id(db) if embedding is not None else None
            )
            if embedding is not None:
                analysis_repo.set_embedding(analysis, embedding, model=embedding_model)

            # 级联测量裁决用：_collect_frames 已把视频下载到本地临时目录
            if asset.file_type == "video":
                downloaded = tmp_dir / f"source{Path(asset.filename).suffix.lower()}"
                if downloaded.is_file():
                    local_video = str(downloaded)

        _update_job_stage(job_id, "cluster")
        text_signature = f"{payload.creative_name} {' '.join(payload.tags)}"
        creative, previous_creative_id = _cluster(
            db,
            asset,
            payload.creative_name,
            embedding,
            text_signature,
            embedding_model=embedding_model,
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
            # P0-3 残留：variant 被再分析搬走后，旧族代表向量/计数按剩余
            # 成员重算（不重算会带着搬走成员的向量继续参与召回）
            old_creative = CreativeRepository(db).get(previous_creative_id)
            if old_creative is not None:
                recompute_creative_representative(db, old_creative)
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
        # 判定（归族/合并预裁/漏网扫描/巩固）不在这里跑——由调用方
        # （run_analysis_pipeline / worker）在分析槽释放后调 _run_judge_phase
        # （F3 双槽模型），本函数只负责把 creative.id 交出去
        return creative.id
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
        if raise_on_error:
            raise
    finally:
        db.close()
        shutil.rmtree(tmp_dir, ignore_errors=True)
