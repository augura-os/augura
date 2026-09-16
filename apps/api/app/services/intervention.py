"""人工介入密度：半RSI 验收指标——每周人工裁决数 ÷ 每周新素材数。

准确率不是验收标准，接管率才是：这个比值往下走，说明 judge 自动
归族/合并与规则层裁决覆盖了原本要人工拍板的工作。收件箱顶部展示
近 12 周曲线，从现在就开始积累。

口径：
- 人工裁决 = edit_logs 中 new_value 不带 ``auto:`` 前缀（与
  judge_calibration 的自动/人工划分同一约定），且 (entity_type,
  action, field) 落在 RULING_SCOPES / 衍生关系范围内——只数
  "系统本可接管"的裁决类操作：
  - (creative, update, dna_id)：归族裁决（routes/dnas.py 人工改族；
    auto 归族带 auto: 前缀已被排除）
  - (creative, merge, "")：合并裁决（merge_ops auto=False 才进得来）
  - (creative, split, "")：拆分裁决（routes/graph.py）
  - (creative, update, observation_pair)：维持拆分登记/结案
  - entity_type="derivation"（任意 action/field）：衍生关系裁决——
    建链/解链/改因子/改 verdict（routes/derivations.py，含收件箱
    "采纳"走 update 的路径）
  排除：dna create（建族是分类法管理，不随新素材量伸缩）、
  lifecycle_state（运营状态流转，有独立 auto 机制）、cluster
  （目前只有 auto 写入）、asset 字段编辑（内容标注而非裁决）、
  system auto_scan。
- 新素材 = creatives.created_at 计数。
- density = human_rulings / new_creatives；new_creatives=0 时 None
  （没分母 ≠ 密度为 0，避免"系统完美接管"的误读）。
- 周 = ISO 周（周一始），按 UTC 归桶。

另含 hub 偏斜监控 ``attach_skew``（embedding 设计 §3.5）：自动 attach
次数按族的分布，复用本模块"从 edit_logs 现算指标"的思路。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import or_, select, tuple_
from sqlalchemy.orm import Session

from app.models import Creative, EditLog

WEEKS = 12

# 裁决类操作（entity_type, action, field）；derivation 全实体见 docstring
RULING_SCOPES: tuple[tuple[str, str, str], ...] = (
    ("creative", "update", "dna_id"),
    ("creative", "merge", ""),
    ("creative", "split", ""),
    ("creative", "update", "observation_pair"),
)


@dataclass
class WeekDensity:
    """单周密度；new_creatives=0 时 density 为 None。"""

    week_start: date
    human_rulings: int
    new_creatives: int
    density: float | None


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _monday(dt: datetime) -> date:
    day = _as_utc(dt).date()
    return day - timedelta(days=day.weekday())


def intervention_density(
    db: Session, *, now: datetime | None = None, weeks: int = WEEKS
) -> list[WeekDensity]:
    """近 ``weeks`` 个 ISO 周（旧→新，末尾是进行中本周）的密度序列。"""
    current_monday = _monday(now or datetime.now(timezone.utc))
    window_start = datetime.combine(
        current_monday - timedelta(weeks=weeks - 1), time.min,
        tzinfo=timezone.utc,
    )

    ruling_logs = list(
        db.scalars(
            select(EditLog.created_at).where(
                EditLog.created_at >= window_start,
                ~EditLog.new_value.like("auto:%"),
                or_(
                    EditLog.entity_type == "derivation",
                    tuple_(EditLog.entity_type, EditLog.action, EditLog.field).in_(
                        RULING_SCOPES
                    ),
                ),
            )
        ).all()
    )
    creative_dates = list(
        db.scalars(
            select(Creative.created_at).where(Creative.created_at >= window_start)
        ).all()
    )

    rulings_by_week: dict[date, int] = {}
    for created_at in ruling_logs:
        monday = _monday(created_at)
        rulings_by_week[monday] = rulings_by_week.get(monday, 0) + 1
    creatives_by_week: dict[date, int] = {}
    for created_at in creative_dates:
        monday = _monday(created_at)
        creatives_by_week[monday] = creatives_by_week.get(monday, 0) + 1

    series: list[WeekDensity] = []
    for offset in range(weeks):
        monday = window_start.date() + timedelta(weeks=offset)
        rulings = rulings_by_week.get(monday, 0)
        creatives = creatives_by_week.get(monday, 0)
        series.append(
            WeekDensity(
                week_start=monday,
                human_rulings=rulings,
                new_creatives=creatives,
                density=(rulings / creatives) if creatives else None,
            )
        )
    return series


@dataclass
class AttachSkew:
    """自动 attach 次数按族的分布摘要；无自动归入记录时全零/None。"""

    attach_total: int
    creatives_with_attaches: int
    top_creative_name: str | None
    top_count: int
    top_share: float  # top_count / attach_total（无记录时 0.0）


def attach_skew(db: Session) -> AttachSkew:
    """hub 偏斜监控（embedding 设计 §3.5）：每个族被自动 attach 的次数分布。

    数据源 = edit_logs 里 (creative, update, cluster) 且 new_value 带
    ``auto:`` 前缀的自动归入记录（pipeline._cluster 的唯一写入点）。
    top_share（最大族的占比）突然变大 = 均值代表向量的 hub 引力在作祟
    （大族 representative_embedding"像所有东西"，rich-get-richer）。
    只读计算，随 review queue 每次展示现算——记录量与 edit_logs 同量级。
    """
    rows = list(
        db.scalars(
            select(EditLog.entity_id).where(
                EditLog.entity_type == "creative",
                EditLog.action == "update",
                EditLog.field == "cluster",
                EditLog.new_value.like("auto:%"),
            )
        ).all()
    )
    if not rows:
        return AttachSkew(0, 0, None, 0, 0.0)
    counts: dict[str, int] = {}
    for creative_id in rows:
        counts[creative_id] = counts.get(creative_id, 0) + 1
    top_id, top_count = max(counts.items(), key=lambda item: item[1])
    top = db.get(Creative, top_id)
    return AttachSkew(
        attach_total=len(rows),
        creatives_with_attaches=len(counts),
        top_creative_name=top.name if top is not None else None,
        top_count=top_count,
        top_share=top_count / len(rows),
    )
