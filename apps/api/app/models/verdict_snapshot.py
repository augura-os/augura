"""VerdictSnapshot — 决策快照：有状态刷新时规则引擎判定的输入/输出落库行。

回答"当时为什么这么判"：action/reason_code/params/reasons/supplementary/
priority_dollars/confidence 是判定输出（Verdict 本体），thresholds/
metrics/rules_version 是判定输入。只有内容变化才产生新行（content_hash
去重，见 services/verdict_snapshots）——快照是追加式审计轨迹，不用
TimestampMixin（immutable，无 updated_at；computed_at 即写入时刻，由
service 显式赋值保证同 creative 严格递增）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, new_uuid


class VerdictSnapshot(Base):
    __tablename__ = "verdict_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    creative_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("creatives.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    # 判定参数（i18n-ready，前端 brief.bit.<code> 模板插值用）
    params: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    # 旧版中文渲染（含 supplementary 的人读版），与看板/遥测消费口径一致
    reasons: Mapped[list[object]] = mapped_column(JSONB, nullable=False, default=list)
    # 补充理由 ReasonBit 列表（{code, params} 字典形态）
    supplementary: Mapped[list[object]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    priority_dollars: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # 判定出自哪版规则（services/recommendation_rules.RULES_VERSION）
    rules_version: Mapped[str] = mapped_column(String(32), nullable=False)
    # 判定用阈值（分市场解析后，与 build_report 内部同口径）
    thresholds: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    # 判定输入指标全集（dataclasses.asdict(CreativeMetrics)）
    metrics: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    # 赢家标签（LabelCode 字符串数组；rules-v2 起，旧行由 server_default '[]' 补齐）
    labels: Mapped[list[object]] = mapped_column(
        JSONB, nullable=True, default=list, server_default="[]"
    )
    # 去重指纹：{action, reason_code, params, rules_version, thresholds, metrics,
    # labels} 的 canonical JSON 的 sha256；同 creative 最新行相同则不插新行
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
