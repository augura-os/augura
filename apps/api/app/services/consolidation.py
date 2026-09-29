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
import threading
import time
from datetime import datetime, timezone

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import SessionLocal
from app.repositories.edit_logs import EditLogRepository
from app.repositories.jobs import JobRepository
from app.repositories.settings import SettingsRepository
from app.services.settings import AIConfig

logger = logging.getLogger(__name__)

LAST_RUN_SETTING = "consolidation_last_run_at"
NEW_SINCE_SETTING = "consolidation_new_since"
# 触发条件：距上次全扫 ≥ 该天数，或期间新增素材 ≥ 该计数（任一满足）
PERIOD_DAYS = 7
NEW_ASSETS_THRESHOLD = 50

# 异步低优先级（2026-09 实机教训）：全扫数百对 × 多轮 LLM 投票，曾在
# judge 槽内同步跑几小时，堵死批量上传的判定通道并一次烧穿 5h 额度窗口。
# 改为守护线程 + 分析队列积压时有界重试 defer（错过只在进程内，崩溃后
# last_run_at 未更新，下次分析完成自然重新触发，幂等可恢复）。
DEFER_CHECK_SECONDS = 300.0
DEFER_MAX_CHECKS = 36  # ≈3 小时；超了留待下次分析完成再触发
_spawn_lock = threading.Lock()


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


def note_analysis_completed(db: Session) -> int:
    """分析完成计数 +1（独立短事务：全扫的 LLM 调用不得跨未提交写）。

    计数留在判定通道内同步执行（轻量）；重活全扫挪到
    ``spawn_consolidation_async`` 的守护线程。返回当前计数。
    """
    repo = SettingsRepository(db)
    new_since = int(repo.get(NEW_SINCE_SETTING) or 0) + 1
    repo.set(NEW_SINCE_SETTING, str(new_since))
    db.commit()
    return new_since


def maybe_consolidate(
    db: Session, settings: Settings, config: AIConfig | None, *, count: bool = True
) -> bool:
    """满足条件则全量扫描 + 阈值校准。``count=True`` 时先计数 +1。

    返回是否真的跑了巩固。失败只记日志（调用方在管线上，不能炸）。
    异步线程路径（spawn_consolidation_async）已同步计过数，传
    ``count=False`` 避免 +2。

    F3 事务纪律：计数、扫描、留痕、刹车、回流、回填各自独立短事务
    （各自 commit）——此前全部堆在调用方（run_post_analysis）的长事务
    里，校准的 UPDATE settings 曾被管线行锁堵住 46 分钟。
    """
    try:
        repo = SettingsRepository(db)
        if count:
            new_since = note_analysis_completed(db)
        else:
            new_since = int(repo.get(NEW_SINCE_SETTING) or 0)

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

        stats = scan_missed_merges(
            db, settings, config, emit=lambda _msg: None, commit=True
        )
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
        db.commit()  # 巩固留痕独立短事务
        logger.info(
            "周期巩固完成：召回 %d，建议 %d，自动合并 %d",
            stats.recalled, stats.suggested, stats.merged,
        )

        # 刹车随行：judge 改判率超限自动降级（油门已自动，刹车不能靠人
        # 记得跑脚本）。独立 savepoint + 独立提交——闸门写是自己的短事务
        # （曾随管线长事务末尾统一提交，等锁 46 分钟）；失败降级
        # logger.debug，绝不波及巩固与管线。
        try:
            from app.services.judge_calibration import run_calibration

            with db.begin_nested():
                brake = run_calibration(db)
            db.commit()
            for event in brake.events:
                logger.info(
                    "judge 刹车：%s %s（改判率 %.1f%%，样本 %d）",
                    event.gate_key, event.action,
                    event.override_rate * 100, event.auto_total,
                )
        except Exception:  # noqa: BLE001 — 刹车失败不拖垮巩固
            logger.debug("judge 刹车校准失败", exc_info=True)

        # 规则层回流随行：人工改判挖差异词 → 收件箱建议（同一容错机制 +
        # 独立提交；样本不足时服务内自己跳过）
        try:
            from app.services import rule_feedback

            with db.begin_nested():
                suggested = rule_feedback.suggest_keywords(db)
            db.commit()
            if suggested:
                logger.info("规则层回流：浮出 %d 条规则词建议", suggested)
        except Exception:  # noqa: BLE001 — 挖掘失败不拖垮巩固
            logger.debug("规则词挖掘失败", exc_info=True)

        # 向量回填随行（E2 设计 §4.4）：每轮巩固顺带补一批存量向量
        # （≤ BACKFILL_BATCH_SIZE 条），几天内自然补完；单条失败跳过。
        # 同一容错机制 + 独立提交——回填失败绝不波及巩固与管线
        try:
            from app.services.embedding import (
                BACKFILL_BATCH_SIZE,
                backfill_embeddings,
            )

            with db.begin_nested():
                backfill = backfill_embeddings(
                    db, config, limit=BACKFILL_BATCH_SIZE
                )
            db.commit()
            if backfill.analyses:
                logger.info(
                    "向量随行回填：补 %d 条（剩余 %d）",
                    backfill.analyses, backfill.remaining,
                )
        except Exception:  # noqa: BLE001 — 回填失败不拖垮巩固
            logger.debug("向量随行回填失败", exc_info=True)
        return True
    except SQLAlchemyError:  # noqa: BLE001 — 巩固失败不拖垮分析管线
        db.rollback()  # 事务已中止：回滚恢复会话；已提交的阶段不受影响
        logger.exception("周期巩固失败")
        return False
    except Exception:  # noqa: BLE001 — 非 DB 异常事务未坏，无需回滚
        logger.exception("周期巩固失败")
        return False


def spawn_consolidation_async(
    settings: Settings, config: AIConfig | None
) -> threading.Thread:
    """在守护线程里低优先级跑巩固：judge 槽外、分析队列排空后执行。

    队列有积压（到期 queued > 0）时 defer：每 DEFER_CHECK_SECONDS 复查一次，
    最多 DEFER_MAX_CHECKS 轮；进程崩溃/超时未跑都不留状态——last_run_at
    只在全扫成功后更新，下次分析完成会重新 spawn，幂等可恢复。
    返回线程对象（测试可 join）；已在做时立即返回不做任何事的线程。
    """

    def _run() -> None:
        if not _spawn_lock.acquire(blocking=False):
            logger.info("巩固扫描已在进行/等待中，跳过本次触发")
            return
        try:
            for _ in range(DEFER_MAX_CHECKS):
                with SessionLocal() as db:
                    if not should_consolidate(db):
                        return
                    backlog = JobRepository(db).backlog_count()
                if backlog == 0:
                    break
                logger.info(
                    "分析队列积压 %d 条，巩固扫描 %.0fs 后复查",
                    backlog, DEFER_CHECK_SECONDS,
                )
                time.sleep(DEFER_CHECK_SECONDS)
            else:
                logger.info(
                    "等待 %d 轮后分析队列仍未排空，巩固留待下次触发",
                    DEFER_MAX_CHECKS,
                )
                return
            with SessionLocal() as db:
                # 计数由 run_post_analysis 同步做过（note_analysis_completed），
                # 这里 count=False 避免重复 +1
                maybe_consolidate(db, settings, config, count=False)
        finally:
            _spawn_lock.release()

    thread = threading.Thread(target=_run, name="consolidation", daemon=True)
    thread.start()
    return thread
