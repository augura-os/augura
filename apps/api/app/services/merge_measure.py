"""合并自动执行的测量证据：候选对双方代表视频的 pHash 帧对齐率。

LLM 预裁只看文本分析（hook/conflict/gameplay），换皮/误配对在文本上
可能高度相似；合并是不可逆操作，所以自动执行前加一道视频内容证据——
复用裂变侧的测量层（services/variant_diff.measure_pair），双方各取
第一个视频变体做帧对齐，aligned_fraction 作为"同一素材源"的物证。

全程容错：无视频变体 / 下载失败 / 测量失败一律返回 None（调用方按
"证据不足"处理——只写建议，绝不自动合并）。

证据分两档：tier-1 是高对齐率（≥ MIN_ALIGNED_FRACTION_FOR_AUTO）；
tier-2 是 classify 的确定性裂变因子（AUTO_MERGE_DERIVATION_FACTORS）——
A-B 结构素材（前贴/尾帧不同 + 主体一致）对齐率有数学上限，够不到
tier-1，但边缘未匹配段 + 中段无发散的测量特征本身就是同源物证。
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
from app.services.variant_diff import classify, measure_pair

logger = logging.getLogger(__name__)

# 自动合并的对齐率门槛：只有近乎完全对齐才算"同一素材源"铁证
# （换皮/重剪都会显著拉低对齐率；裂变侧 MIN_ALIGNED_FRACTION=0.5 只是
# "有同源关系"的下限，不足以支撑不可逆的自动合并）
MIN_ALIGNED_FRACTION_FOR_AUTO = 0.90

# tier-2 证据通道：variant_diff.classify 的确定性判定里，只有这两个因子
# 可以作为与"aligned ≥ 0.90"并列的自动执行物证。
# 背景：A-B 结构素材（前贴/尾帧不同 + 主体完全相同）的 aligned_fraction
# 数学上限 = 主体帧数 / 总帧数（如 15s 前贴 + 15s 主体 → 上限 0.5），
# 永远够不到 0.90，但 classify 能确定性判定其为 intro-sticker /
# brand-endcard 裂变。
# 安全边界：classify 只在 aligned ≥ MIN_ALIGNED_FRACTION(0.5) 且边缘
# 未匹配段 ≥ MIN_UNMATCHED_EDGE_SEC(2s) 且中段无发散支配判定时才给出
# 这两个因子；aspect-ratio（画幅豁免路径对齐率可任意低）、None（测量
# 定不了案）、not-a-derivation（疑似误链）一律不进本白名单。
AUTO_MERGE_DERIVATION_FACTORS = ("intro-sticker", "brand-endcard")

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


def measure_pair_evidence(
    db: Session,
    settings: Settings,
    creative_a_id: str,
    creative_b_id: str,
) -> tuple[float, str | None] | None:
    """候选对双方代表视频的测量证据：(aligned_fraction, factor)；失败 → None。

    factor 来自 variant_diff.classify 的确定性归因，仅当判定为
    intro-sticker / brand-endcard（AUTO_MERGE_DERIVATION_FACTORS 白名单，
    tier-2 证据通道）时带出；None / not-a-derivation / aspect-ratio 等
    一律返回 None 作为 factor。
    """
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
            meta_a, meta_b, diff = measure_pair(path_a, path_b)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        factor, _evidence = classify(diff, meta_a, meta_b)
        if factor not in AUTO_MERGE_DERIVATION_FACTORS:
            factor = None
        return diff.aligned_fraction, factor
    except Exception:  # noqa: BLE001 — 测量失败 = 证据不足，不抛给判定主流程
        logger.exception(
            "合并对齐测量失败 creatives=%s,%s", creative_a_id, creative_b_id
        )
        return None


def measure_pair_alignment(
    db: Session,
    settings: Settings,
    creative_a_id: str,
    creative_b_id: str,
) -> float | None:
    """候选对双方代表视频的帧对齐率（0-1）；测不了/失败返回 None。"""
    evidence = measure_pair_evidence(db, settings, creative_a_id, creative_b_id)
    return None if evidence is None else evidence[0]
