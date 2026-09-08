"""合并自动执行的测量证据：候选对双方代表视频的 pHash 帧对齐率。

LLM 预裁只看文本分析（hook/conflict/gameplay），换皮/误配对在文本上
可能高度相似；合并是不可逆操作，所以自动执行前加一道视频内容证据——
复用裂变侧的测量层（services/variant_diff.measure_pair），双方各取
第一个视频变体做帧对齐，aligned_fraction 作为"同一素材源"的物证。

全程容错：无视频变体 / 下载失败 / 测量失败一律返回 None（调用方按
"证据不足"处理——只写建议，绝不自动合并）。
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import Settings
from app.services.storage import StorageService
from app.services.variant_diff import measure_pair

logger = logging.getLogger(__name__)

# 自动合并的对齐率门槛：只有近乎完全对齐才算"同一素材源"铁证
# （换皮/重剪都会显著拉低对齐率；裂变侧 MIN_ALIGNED_FRACTION=0.5 只是
# "有同源关系"的下限，不足以支撑不可逆的自动合并）
MIN_ALIGNED_FRACTION_FOR_AUTO = 0.90

_VIDEO_ASSET_SQL = text(
    "select a.id, a.storage_key, a.filename from creative_variants v "
    "join creative_assets a on a.id = v.asset_id "
    "where v.creative_id = :cid and a.file_type = 'video' "
    "order by v.created_at, v.id limit 1"
)


def first_video_asset(db: Session, creative_id: str) -> tuple[str, str] | None:
    """creative 第一个视频变体资产的 (storage_key, filename)；无视频 → None。

    公共函数：聚类级联（pipeline._cluster）也用它取候选族代表视频。
    """
    row = db.execute(_VIDEO_ASSET_SQL, {"cid": creative_id}).first()
    if row is None:
        return None
    return row[1], row[2]


def measure_pair_alignment(
    db: Session,
    settings: Settings,
    creative_a_id: str,
    creative_b_id: str,
) -> float | None:
    """候选对双方代表视频的帧对齐率（0-1）；测不了/失败返回 None。"""
    try:
        asset_a = first_video_asset(db, creative_a_id)
        asset_b = first_video_asset(db, creative_b_id)
        if asset_a is None or asset_b is None:
            return None
        storage = StorageService(settings)
        # 系统临时目录而非 settings.upload_dir：容器默认 /app/uploads 在
        # CI runner 等无权限路径上 mkdir 会 PermissionError
        tmp_dir = Path(tempfile.mkdtemp(prefix="merge-measure-"))
        try:
            path_a = str(tmp_dir / f"a{Path(asset_a[1]).suffix.lower()}")
            path_b = str(tmp_dir / f"b{Path(asset_b[1]).suffix.lower()}")
            storage.download_to(asset_a[0], path_a)
            storage.download_to(asset_b[0], path_b)
            _meta_a, _meta_b, diff = measure_pair(path_a, path_b)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        return diff.aligned_fraction
    except Exception:  # noqa: BLE001 — 测量失败 = 证据不足，不抛给判定主流程
        logger.exception(
            "合并对齐测量失败 creatives=%s,%s", creative_a_id, creative_b_id
        )
        return None
