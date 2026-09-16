"""Embedding 抽象层：三态后端分发（off / provider / local）+ shadow 写入。

设计依据 docs/embedding-recall-design.md §3.2–3.4（E0 实测数据见
docs/embedding-recall-e0-report.md）：

- ``off``：不用 embedding，纯文本兜底（v0.7.0 行为）
- ``provider``：走服务商 OpenAI 兼容端点（原 AnalysisService.embed 路径）
- ``local``：fastembed 本地模型 ``BAAI/bge-small-zh-v1.5``（默认）

shadow 语义：本层只负责"产出向量 + 写入存储"，attach 判定永远走文本通道
（pipeline 不再按 embedding 是否存在切换判定通道）。

本地模型的运维细节（全部收口在本模块，调用方无感）：

- 懒加载单例 + 初始化锁：onnxruntime 推理线程安全，初始化（下载/加载）
  不是；pipeline 是多线程环境（_PIPELINE_SLOTS 并发分析）。
- ``model_name=`` 陷阱：fastembed 构造参数是 ``model_name``，误传
  ``model=`` 会被 **kwargs 静默吞掉并回退默认 en 模型（E0 实测）——
  加载后必须断言实际模型 id。
- 模型缓存：显式 cache_dir 指向 uploads 持久卷（容器重建不丢模型）；
  ``FASTEMBED_CACHE_PATH`` 环境变量优先（等效替代）。
- HF 不可达时：monkeypatch ``huggingface_hub.constants.ENDPOINT`` 切
  hf-mirror.com 重试一次（HF_ENDPOINT 环境变量在 import 时绑定，
  运行时设置无效）。
- 极端离线场景：宿主机预下载模型目录后，用 fastembed 原生
  ``TextEmbedding(model_name=..., specific_model_path=<目录>)``
  手动指定（本模块不暴露该参数，需要时改 _load_model 一处即可）。
- 模型一致性：settings 记 ``embedding_model_active``；切换模型/后端时
  存量向量全部失效清空（不同模型维度/语义空间不互通，混存即毒）。
  失效后的批量回填见 ``backfill_embeddings``（POST /admin/backfill-embeddings）。
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AnalysisResult, Creative, CreativeVariant
from app.repositories.creatives import VariantRepository
from app.repositories.settings import SettingsRepository
from app.services.clustering import mean_embeddings
from app.services.settings import AIConfig

logger = logging.getLogger(__name__)

# Settings 表键
EMBEDDING_BACKEND_SETTING = "embedding_backend"
EMBEDDING_MODEL_ACTIVE_SETTING = "embedding_model_active"

# 三态后端（§3.2）；local 为默认（shadow 下默认开不会让任何人变糟）
EMBEDDING_BACKENDS = ("off", "provider", "local")
DEFAULT_EMBEDDING_BACKEND = "local"

# 本地模型（E0 实测：直连 HF ~22s，512 维已归一化，CPU 单句 ~10ms）
LOCAL_EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"

# 漏合并召回的 top-k 预算（E0 拍板：k=5，上限 10）。E1 只定义常量，
# 消费在 E2（missed_merge_scan 的 embedding 召回通道）。
EMBEDDING_RECALL_TOP_K = 5

# HF 国内镜像（P0-2：只能 patch 常量，运行时设 HF_ENDPOINT 无效）
HF_MIRROR_ENDPOINT = "https://hf-mirror.com"


class EmbeddingUnavailableError(RuntimeError):
    """本地 embedding 不可用（依赖未安装 / 模型下载失败 / 模型 id 不符）。"""


def resolve_embedding_backend(db: Session) -> str:
    """读 settings 表的 embedding_backend；缺失/非法值一律回落默认 local。"""
    value = SettingsRepository(db).get(EMBEDDING_BACKEND_SETTING)
    if value in EMBEDDING_BACKENDS:
        return value
    return DEFAULT_EMBEDDING_BACKEND


def active_embedding_model_id(backend: str, config: AIConfig) -> str | None:
    """当前配置下实际产出向量的模型 id（一致性检查的比对基准）。

    off 或 provider 未配置模型时返回 None（不产出向量，无需一致性检查）。
    """
    if backend == "local":
        return f"local:{LOCAL_EMBEDDING_MODEL}"
    if backend == "provider" and config.embedding_model:
        return f"provider:{config.embedding_model}"
    return None


def invalidate_embeddings(db: Session) -> None:
    """清空全部存量向量（模型切换时调用）：三处存储列 + 计数归零。"""
    db.execute(update(AnalysisResult).values(embedding=None))
    db.execute(update(CreativeVariant).values(embedding=None))
    db.execute(
        update(Creative).values(representative_embedding=None, embedding_count=0)
    )
    db.flush()


def _ensure_model_consistency(db: Session, active_id: str) -> None:
    """发现生效模型与记录不一致 → 存量向量失效清空 + 更新记录。

    第一次写入时只记录 active id（库内无向量可清）。清空后由新分析的
    shadow 写入自然重建；存量由 ``backfill_embeddings`` 回填。
    """
    repo = SettingsRepository(db)
    stored = repo.get(EMBEDDING_MODEL_ACTIVE_SETTING)
    if stored == active_id:
        return
    if stored is not None:
        logger.info(
            "embedding 模型切换 %s → %s：存量向量失效清空（shadow 重建 + E2 回填）",
            stored,
            active_id,
        )
        invalidate_embeddings(db)
    repo.set(EMBEDDING_MODEL_ACTIVE_SETTING, active_id)
    db.flush()


def _resolve_cache_dir() -> str:
    """模型缓存目录：FASTEMBED_CACHE_PATH > uploads 持久卷 > ./uploads。

    容器内 upload_dir=/app/uploads（compose 挂载的持久卷）；本地开发
    该路径不存在时回退到工作目录下的 ./uploads/.cache/fastembed。
    """
    env_dir = os.environ.get("FASTEMBED_CACHE_PATH", "").strip()
    if env_dir:
        return env_dir
    upload_dir = Path(get_settings().upload_dir)
    base = upload_dir if upload_dir.is_dir() else Path("uploads")
    cache_dir = base / ".cache" / "fastembed"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return str(cache_dir)


def _load_model(cache_dir: str):
    """加载 fastembed 模型；首次失败时切 hf-mirror 重试一次（P0-2）。"""
    from fastembed import TextEmbedding

    try:
        return TextEmbedding(model_name=LOCAL_EMBEDDING_MODEL, cache_dir=cache_dir)
    except Exception as first:  # noqa: BLE001 — 下载/加载失败统一走镜像重试
        logger.warning("HF 直连加载模型失败（%s），切 %s 重试", first, HF_MIRROR_ENDPOINT)
        import huggingface_hub.constants

        huggingface_hub.constants.ENDPOINT = HF_MIRROR_ENDPOINT
        try:
            return TextEmbedding(model_name=LOCAL_EMBEDDING_MODEL, cache_dir=cache_dir)
        except Exception as second:  # noqa: BLE001
            raise EmbeddingUnavailableError(
                f"本地 embedding 模型加载失败（HF 直连与镜像均不可用）: {second}"
            ) from second


class LocalEmbedder:
    """fastembed 本地模型的懒加载封装（线程安全单例，见 get_local_embedder）。"""

    def __init__(self) -> None:
        self._model = _load_model(_resolve_cache_dir())
        # E0 坑：model= kwarg 被静默吞掉会回退默认 en 模型——加载后断言
        # 实际模型 id，把静默回退变成显式失败
        inner = getattr(self._model, "model", self._model)
        actual = getattr(inner, "model_name", "")
        if actual != LOCAL_EMBEDDING_MODEL:
            raise EmbeddingUnavailableError(
                f"加载的模型 id 不符：期望 {LOCAL_EMBEDDING_MODEL}，实际 {actual!r}"
            )

    def embed(self, text: str) -> list[float]:
        vector = list(self._model.embed([text.strip() or " "]))[0]
        return [float(value) for value in vector]


_LOCAL_EMBEDDER: LocalEmbedder | None = None
_LOCAL_INIT_LOCK = threading.Lock()


def get_local_embedder() -> LocalEmbedder:
    """懒加载单例：首次调用才下载/加载模型（初始化非线程安全，加锁）。"""
    global _LOCAL_EMBEDDER
    if _LOCAL_EMBEDDER is None:
        with _LOCAL_INIT_LOCK:
            if _LOCAL_EMBEDDER is None:
                _LOCAL_EMBEDDER = LocalEmbedder()
    return _LOCAL_EMBEDDER


def reset_local_embedder() -> None:
    """测试用：清掉单例（下一次 get 重新初始化）。"""
    global _LOCAL_EMBEDDER
    with _LOCAL_INIT_LOCK:
        _LOCAL_EMBEDDER = None


def embed_analysis_text(
    db: Session, config: AIConfig, text: str
) -> list[float] | None:
    """按 backend 分发产出 embedding；任何失败降级返回 None（文本兜底）。

    shadow 语义：本函数只产出向量供写入，不影响任何判定通道。
    副作用：首次产出或模型切换时维护 embedding_model_active（含存量失效）。
    """
    backend = resolve_embedding_backend(db)
    active_id = active_embedding_model_id(backend, config)
    if active_id is None:
        return None
    _ensure_model_consistency(db, active_id)
    try:
        if backend == "local":
            return get_local_embedder().embed(text)
        # provider：延迟 import 避免本地路径依赖 OpenAI 客户端构造
        from app.services.analysis import AnalysisService

        return AnalysisService(config).embed(text)
    except Exception as exc:  # noqa: BLE001 — 分析主流程不被 embedding 拖死
        logger.warning("embedding 不可用（%s），本次走向量缺失降级", exc)
        return None


def recompute_creative_representative(db: Session, creative: Creative) -> None:
    """按成员 variant 的向量重算 representative_embedding（均值与顺序无关）。

    维护 embedding_count = 实际参与均值的向量条数（P0-3 口径）。
    用于人工编辑重嵌后同步族代表向量；E2 回填也走这里。
    """
    variants = VariantRepository(db).list_by_creative(creative.id)
    vectors = [v.embedding for v in variants if v.embedding]
    creative.representative_embedding = mean_embeddings(vectors)
    creative.embedding_count = len(vectors) if vectors else 0
    db.flush()


# 随行回填每轮上限（设计 §4.4：周期巩固顺带补一批，几天内自然补完）
BACKFILL_BATCH_SIZE = 50


@dataclass
class BackfillStats:
    """一轮回填的结果（随行分批与手动全量共用同一统计口径）。"""

    analyses: int = 0  # 补了向量的 analysis_results 条数
    variants: int = 0  # 同步写入 variant.embedding 的条数
    creatives: int = 0  # 按成员重算代表向量的 creative 数
    failed: int = 0  # 单条失败/空文本跳过（不阻塞整轮）
    remaining: int = 0  # 仍缺向量的 analysis 条数（回填进度信号）
    skipped: list[str] = field(default_factory=list)  # 跳过条的 analysis id（排障用）


def backfill_embeddings(
    db: Session, config: AIConfig | None, *, limit: int | None = None
) -> BackfillStats:
    """存量向量回填（设计 §4.4）：覆盖三处存储且按成员重算代表向量。

    对每条缺向量的 analysis 按 ``summary + tags``（与 pipeline 嵌入文本同
    口径，E0 拍板）补向量，同步写到对应 variant.embedding；受影响的
    creative 按成员向量**重算均值**（非增量——模型切换后不叠在旧模型的
    毒向量上）。单条失败跳过、不阻塞整轮。

    ``limit`` 给随行回填分批用（≤ BACKFILL_BATCH_SIZE）；不给则全量（手动
    端点）。``config`` 为 None（AI 未配置）或后端不产出向量（off / provider
    未配模型）时只统计 remaining，不做任何写入。
    """
    stats = BackfillStats()
    stmt = (
        select(AnalysisResult)
        .where(AnalysisResult.embedding.is_(None))
        .order_by(AnalysisResult.created_at)
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    rows = list(db.scalars(stmt).all())

    active_id = (
        active_embedding_model_id(resolve_embedding_backend(db), config)
        if config is not None
        else None
    )
    if active_id is not None:
        variant_repo = VariantRepository(db)
        affected: dict[str, Creative] = {}
        for analysis in rows:
            embed_text = f"{analysis.summary} {' '.join(analysis.tags)}"
            vector = (
                embed_analysis_text(db, config, embed_text)
                if embed_text.strip()
                else None  # 空文本嵌入无意义，保持 NULL 跳过
            )
            if vector is None:
                stats.failed += 1
                stats.skipped.append(analysis.id)
                continue
            analysis.embedding = vector
            stats.analyses += 1
            variant = variant_repo.get_by_asset(analysis.asset_id)
            if variant is None:
                continue
            variant.embedding = vector
            stats.variants += 1
            creative = db.get(Creative, variant.creative_id)
            if creative is not None:
                affected[creative.id] = creative
        for creative in affected.values():
            recompute_creative_representative(db, creative)
            stats.creatives += 1
        db.flush()

    stats.remaining = int(
        db.scalar(
            select(func.count(AnalysisResult.id)).where(
                AnalysisResult.embedding.is_(None)
            )
        )
        or 0
    )
    return stats
