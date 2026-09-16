"""Creative 生命周期状态流转（services/creative_score 的落地侧）。

状态机：active（默认）→ watch（低分观察，自动标记）→ archived（人工
确认归档）。归档 ≠ 删除：所有数据保留，随时可恢复（restore）。

- 自动流转只做 active → watch（标记性质，可逆且无感）；score 更低且
  长期无消耗的"建议归档"只进收件箱，人工确认才置 archived
- 人工操作永远优先（Human > AI）：人工置的 archived 不会被自动恢复
- 每次流转写 edit_logs（AI Constitution §5）
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Creative
from app.repositories.creatives import CreativeRepository
from app.repositories.edit_logs import EditLogRepository
from app.services.settings import ScoreConfig

logger = logging.getLogger(__name__)

LIFECYCLE_STATES = ("active", "watch", "archived")
# score 低于此值 → watch（观察标记，自动流转只走这一步）
WATCH_SCORE_THRESHOLD = 50.0


def compute_auto_state(
    score: float, days_idle: int | None, config: ScoreConfig
) -> str:
    """纯函数：分数 + 闲置天数 → 应有状态。

    "archived" 在这里只是候选判定（建议归档的条件），自动流转不会真的
    置 archived——那一步永远在收件箱人工确认。
    """
    if (
        score < config.archive_score_threshold
        and days_idle is not None
        and days_idle > config.archive_idle_days
    ):
        return "archived"
    if score < WATCH_SCORE_THRESHOLD:
        return "watch"
    return "active"


def apply_auto_transitions(
    db: Session,
    scores: dict[str, tuple[float, int | None]],
    config: ScoreConfig,
) -> int:
    """批量自动流转：只把 active → watch（及 watch 恢复 active）。

    archived 不参与任何自动流转（人工领地）；建议归档由
    review.archive_suggestion_items 进收件箱。``scores`` 是
    creative_id → (total_score, days_idle)。返回流转次数。
    """
    if not config.auto_enabled:
        return 0
    creatives = {
        c.id: c
        for c in db.scalars(
            select(Creative).where(Creative.lifecycle_state != "archived")
        ).all()
    }
    log_repo = EditLogRepository(db)
    changed = 0
    for creative_id, (score, days_idle) in scores.items():
        creative = creatives.get(creative_id)
        if creative is None:
            continue
        auto_state = compute_auto_state(score, days_idle, config)
        # archived 候选不自动落；watch 以下只标记 watch
        target = "watch" if auto_state in ("watch", "archived") else "active"
        if target == creative.lifecycle_state:
            continue
        log_repo.record(
            entity_type="creative",
            entity_id=creative.id,
            action="update",
            field="lifecycle_state",
            old_value=creative.lifecycle_state,
            new_value=f"{target}（auto: 评分 {score:.1f}）",
        )
        creative.lifecycle_state = target
        changed += 1
    if changed:
        db.flush()
    return changed


def set_lifecycle(
    db: Session, creative_id: str, state: str, *, reason: str = ""
) -> Creative | None:
    """人工置状态（收件箱确认归档/保留观察/恢复都走这里），留痕。"""
    if state not in LIFECYCLE_STATES:
        raise ValueError(f"未知生命周期状态：{state}")
    creative = CreativeRepository(db).get(creative_id)
    if creative is None:
        return None
    if creative.lifecycle_state == state:
        return creative
    EditLogRepository(db).record(
        entity_type="creative",
        entity_id=creative.id,
        action="update",
        field="lifecycle_state",
        old_value=creative.lifecycle_state,
        new_value=f"{state}（{reason}）" if reason else state,
    )
    creative.lifecycle_state = state
    db.flush()
    return creative


def archive_creative(db: Session, creative_id: str, reason: str = "") -> Creative | None:
    """人工确认归档（数据全留，随时可 restore）。"""
    return set_lifecycle(db, creative_id, "archived", reason=reason or "人工归档")


def restore_creative(db: Session, creative_id: str) -> Creative | None:
    """恢复为 active（人工恢复优先级高于一切自动规则）。"""
    return set_lifecycle(db, creative_id, "active", reason="人工恢复")
