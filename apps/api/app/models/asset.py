"""CreativeAsset, Performance and AnalysisResult models (contract §4/§6)."""

from __future__ import annotations

from datetime import date

from sqlalchemy import BigInteger, Date, Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, new_uuid


class CreativeAsset(TimestampMixin, Base):
    __tablename__ = "creative_assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    project_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    # "video" | "image" | "excel"
    file_type: Mapped[str] = mapped_column(String(16), nullable=False)
    mime_type: Mapped[str] = mapped_column(
        String(128), nullable=False, default="application/octet-stream"
    )
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    thumbnail_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # "pending" | "processing" | "completed" | "failed" | "none" (excel)
    analysis_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending"
    )
    status_message: Mapped[str] = mapped_column(Text, nullable=False, default="")


class Performance(TimestampMixin, Base):
    __tablename__ = "performances"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    asset_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("creative_assets.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    creative_name: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    date: Mapped[date | None] = mapped_column(Date, nullable=True)
    impressions: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    clicks: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    spend: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    installs: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # Original Excel row (column names are not fixed — §6 loose parsing).
    raw: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    # 优化方式（install/aeo/vo；NULL = 未知，判定回落 aeo 口径）——上传时从
    # 「优化方式」列归一（services/excel.normalize_objective），存量行由迁移
    # 0020 从 raw JSONB 回填。
    optimization_type: Mapped[str | None] = mapped_column(String(16), nullable=True)


class AnalysisResult(TimestampMixin, Base):
    __tablename__ = "analysis_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    asset_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("creative_assets.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    hook: Mapped[str] = mapped_column(Text, nullable=False, default="")
    conflict: Mapped[str] = mapped_column(Text, nullable=False, default="")
    gameplay: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reward: Mapped[str] = mapped_column(Text, nullable=False, default="")
    characters: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    environment: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    emotion: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    # Reskin factors observed (boundary-rules §3.3); Variant-layer only.
    variant_factors: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    creative_name: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # Which engine produced this analysis (AI Constitution §11), e.g.
    # "auto:kimi-k2.7" or "manual:kimi-work". Human edits keep the origin.
    engine_version: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    # Embedding of ``summary + tags`` (§5.1).
    embedding: Mapped[list[float] | None] = mapped_column(JSONB, nullable=True)
    # 产出 embedding 的模型 id（行级 provenance，格式同 settings 表
    # embedding_model_active，如 "local:BAAI/bge-small-zh-v1.5"）；
    # NULL = 未知模型（存量行）或无向量——戳与向量同生同灭。
    embedding_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
