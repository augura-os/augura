"""市场前缀自动检测（新用户引导：上传即发现命名约定）。

市场前缀（KS_EN 之类）是项目级命名约定，靠 Settings 手动配置——新用户
不配置就上传，市场维度全失效。本模块从文件名反推约定：扫最近上传的
素材文件名，提取 `^([A-Z]{2,8})[-_]` 模式前缀，频次达标（≥3）且未在
market_prefixes 配置里的 → 写收件箱建议（只建议，配置永远人工确认，
Human > AI）。
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CreativeAsset, JudgeSuggestion
from app.services.judge_suggestions import delete_suggestion, upsert_suggestion
from app.services.markets import resolve_market_prefixes

logger = logging.getLogger(__name__)

# 候选前缀模式：2-8 个大写字母 + 连字符/下划线（KS_EN-、BR- 等）
PREFIX_RE = re.compile(r"^([A-Z]{2,8})[-_]")
# 扫描窗口与频次阈值：最近 50 条素材里出现 ≥3 次才算"命名约定"
SCAN_WINDOW = 50
MIN_OCCURRENCES = 3

DETECT_KIND = "market_detect"


def detect_prefixes(db: Session) -> list[tuple[str, int]]:
    """最近上传素材里的未配置市场前缀候选：返回 [(前缀, 出现次数)]。"""
    filenames = db.scalars(
        select(CreativeAsset.filename)
        .order_by(CreativeAsset.created_at.desc())
        .limit(SCAN_WINDOW)
    ).all()
    configured = {p.upper() for p in resolve_market_prefixes(db)}
    counts: dict[str, int] = {}
    for filename in filenames:
        match = PREFIX_RE.match(filename.rsplit("/", 1)[-1])
        if match is None:
            continue
        prefix = match.group(1).upper()
        if prefix not in configured:
            counts[prefix] = counts.get(prefix, 0) + 1
    return sorted(
        ((prefix, n) for prefix, n in counts.items() if n >= MIN_OCCURRENCES),
        key=lambda item: -item[1],
    )


def suggest_detected_prefixes(db: Session) -> list[tuple[str, int]]:
    """检测 + 写收件箱建议（幂等：同一前缀 upsert，消失的候选清理）。

    返回当前检测到的候选列表。失败静默（检测是引导增强，不阻塞管线）。
    """
    try:
        detected = detect_prefixes(db)
        alive = {prefix for prefix, _n in detected}
        for prefix, count in detected:
            upsert_suggestion(
                db, kind=DETECT_KIND, left_id=prefix, right_id=None,
                verdict=prefix, votes=0,
                reason=f"最近上传的素材里出现 {count} 次，未在市场前缀配置里",
            )
        # 已消失/已被配置的候选建议清掉（收件箱只进不出，靠状态消除）
        for row in db.scalars(
            select(JudgeSuggestion).where(JudgeSuggestion.kind == DETECT_KIND)
        ).all():
            if row.left_id not in alive:
                delete_suggestion(
                    db, kind=DETECT_KIND, left_id=row.left_id, right_id=None
                )
        return detected
    except Exception:  # noqa: BLE001
        logger.exception("市场前缀检测失败")
        return []
