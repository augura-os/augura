"""VariantDerivation model — DERIVED_FROM 裂变边（轻量 Experiment）。

A derivation links two variants of the same creative: the target was
produced by changing one reskin factor (variant_factors vocabulary,
boundary-rules §3.3) from the source. That single change IS the
lightweight experiment: source = control, target = variant,
factor = change factor, result = performance delta (computed on read,
never stored). The human verdict (pending/positive/negative) is the
experiment's conclusion; every change is audit-logged (P06).
"""

from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, new_uuid

VERDICTS = ("pending", "positive", "negative")

FACTORS = (
    "intro-sticker",
    "language-market",
    "aspect-ratio",
    "voiceover-copy",
    "brand-endcard",
    "character-reskin",
    "reward-reskin",
    "live-action-vs-animation",
    # 同一创意概念/玩法，但画面整体重拍或重剪（无共同连续片段）——
    # 是创意传承而非文件级改版，2026-08 视觉审查 17 条低对齐边后增设
    "remake",
    "unknown",
)


class VariantDerivation(TimestampMixin, Base):
    __tablename__ = "variant_derivations"
    __table_args__ = (
        UniqueConstraint(
            "source_variant_id", "target_variant_id", name="uq_derivation_pair"
        ),
        CheckConstraint(
            "source_variant_id != target_variant_id", name="ck_derivation_not_self"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    source_variant_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("creative_variants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    target_variant_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("creative_variants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    factor: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    # True = 因子由人工拍板（创建时显式指定 / PUT 修改），自动与批量复核
    # 一律跳过此边，不修正、不重新建议（Human > AI）
    factor_reviewed: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default="false"
    )
    # pending / positive / negative — 人工判定的实验结论
    verdict: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
