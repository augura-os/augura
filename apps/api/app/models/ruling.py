"""SplitRuling model — 观察对结案后的机器可读裁决。

结案维持拆分时写入；新记录保存规范 Creative ID 与裁决类型，名称对保留兼容。
候选生成器排除已裁决对、合并守卫拦截推翻裁决的合并——
结案必须只进不出，否则同一对会在候选列表里无限循环
（hand-written docs/case-rulings.json 之外的自动化沉淀通道）。
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, new_uuid


class SplitRuling(TimestampMixin, Base):
    __tablename__ = "split_rulings"
    __table_args__ = (
        UniqueConstraint("name_a", "name_b", name="uq_split_ruling_pair"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    # 新裁决以稳定 Creative ID 为主；名称保留给旧数据与合并后的守卫兼容。
    creative_id_a: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("creatives.id", ondelete="SET NULL"), nullable=True
    )
    creative_id_b: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("creatives.id", ondelete="SET NULL"), nullable=True
    )
    name_a: Mapped[str] = mapped_column(String(512), nullable=False)
    name_b: Mapped[str] = mapped_column(String(512), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # inbox_close（人工）| auto_observation（已认证自动策略）
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="inbox_close")
    # content_split（内容/人工语义裁决）| performance_split（仅管理分层）| legacy
    ruling_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="content_split", server_default="content_split"
    )
