"""周期性巩固触发器（ER 管线的 consolidate 阶段）。

增量聚类必然漏（上传时只跟现有 creative 比一次），漏网扫描器补洞但
原来要手动跑。本模块让它周期自动触发：**距上次全扫 ≥7 天或期间新增
素材 ≥50 条**就跑一次全量 scan_missed_merges，顺带做阈值校准分析。

挂载在 run_post_analysis 末尾（上传驱动，不需要 cron）。状态存
settings 表（last_run_at + new_since 计数），每次触发写 edit_logs。
失败静默——巩固是增强，绝不拖垮分析管线。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config import Settings
from app.repositories.edit_logs import EditLogRepository
from app.repositories.settings import SettingsRepository
from app.services.settings import AIConfig

logger = logging.getLogger(__name__)

LAST_RUN_SETTING = "consolidation_last_run_at"
NEW_SINCE_SETTING = "consolidation_new_since"
# 触发条件：距上次全扫 ≥ 该天数，或期间新增素材 ≥ 该计数（任一满足）
PERIOD_DAYS = 7
NEW_ASSETS_THRESHOLD = 50


def should_consolidate(
    db: Session, *, now: datetime | None = None
) -> bool:
    """距上次全扫 ≥7 天或期间新增 ≥50 条素材 → 该巩固了。"""
    repo = SettingsRepository(db)
    new_since = int(repo.get(NEW_SINCE_SETTING) or 0)
    if new_since >= NEW_ASSETS_THRESHOLD:
        return True
    raw = repo.get(LAST_RUN_SETTING)
    if raw is None:
        return True  # 从未全扫过
    try:
        last = datetime.fromisoformat(raw)
    except ValueError:
        return True
    now = now or datetime.now(timezone.utc)
    return (now - last).days >= PERIOD_DAYS


def maybe_consolidate(
    db: Session, settings: Settings, config: AIConfig | None
) -> bool:
    """每次分析完成后调用：计数 +1；满足条件则全量扫描 + 阈值校准。

    返回是否真的跑了巩固。失败只记日志（调用方在管线上，不能炸）。
    """
    try:
        repo = SettingsRepository(db)
        new_since = int(repo.get(NEW_SINCE_SETTING) or 0) + 1
        repo.set(NEW_SINCE_SETTING, str(new_since))

        raw = repo.get(LAST_RUN_SETTING)
        days_since: int | None = None
        if raw:
            try:
                days_since = (
                    datetime.now(timezone.utc) - datetime.fromisoformat(raw)
                ).days
            except ValueError:
                days_since = None

        if not should_consolidate(db):
            return False

        from app.services.missed_merge_scan import scan_missed_merges
        from app.services.threshold_calibration import suggest_threshold

        stats = scan_missed_merges(db, settings, config, emit=lambda _msg: None)
        suggestion = suggest_threshold(db)  # 纯文本+分析特征，秒级

        repo.set(LAST_RUN_SETTING, datetime.now(timezone.utc).isoformat())
        repo.set(NEW_SINCE_SETTING, "0")
        EditLogRepository(db).record(
            entity_type="system",
            entity_id="consolidation",
            action="auto_scan",
            field="",
            new_value=(
                f"auto: 周期巩固扫描（新增 {new_since} 条素材 / "
                f"距上次 {days_since if days_since is not None else '—'} 天；"
                f"召回 {stats.recalled} 对，建议 {stats.suggested}，"
                f"自动合并 {stats.merged}"
                + (f"；阈值校准建议 {suggestion.suggested:.2f}" if suggestion else "")
                + "）"
            ),
        )
        logger.info(
            "周期巩固完成：召回 %d，建议 %d，自动合并 %d",
            stats.recalled, stats.suggested, stats.merged,
        )
        return True
    except Exception:  # noqa: BLE001 — 巩固失败不拖垮分析管线
        logger.exception("周期巩固失败")
        return False
