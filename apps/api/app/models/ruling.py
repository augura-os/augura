"""SplitRuling model — 观察对结案后的机器可读裁决。

结案维持拆分（close observation pair）时写入，名称对按字典序存储。
候选生成器排除已裁决对、合并守卫拦截推翻裁决的合并——
结案必须只进不出，否则同一对会在候选列表里无限循环
（hand-written docs/case-rulings.json 之外的自动化沉淀通道）。
"""

from __future__ import annotations

from sqlalchemy import String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, new_uuid


class SplitRuling(TimestampMixin, Base):
    __tablename__ = "split_rulings"
    __table_args__ = (
        UniqueConstraint("name_a", "name_b", name="uq_split_ruling_pair"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    # 双方 Creative 名称，字典序存储（name_a <= name_b），与 case-rulings.json
    # 的 pair 语义一致——守卫与候选排除都按无序对匹配。
    name_a: Mapped[str] = mapped_column(String(512), nullable=False)
    name_b: Mapped[str] = mapped_column(String(512), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 裁决来源："inbox_close"（收件箱结案）| "llm_suggested"（采纳 LLM 建议）
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="inbox_close")
