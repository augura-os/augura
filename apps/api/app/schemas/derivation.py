"""Derivation / evolution DTOs (GET /creatives/{id}/evolution, /derivations)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

DerivationVerdict = Literal["pending", "positive", "negative"]
DerivationFactor = Literal[
    "intro-sticker",
    "language-market",
    "aspect-ratio",
    "voiceover-copy",
    "brand-endcard",
    "character-reskin",
    "reward-reskin",
    "live-action-vs-animation",
    "remake",
    "unknown",
]


class DerivationOut(BaseModel):
    id: str
    source_variant_id: str
    target_variant_id: str
    factor: str
    verdict: str
    note: str


class DerivationCreatePayload(BaseModel):
    source_variant_id: str
    target_variant_id: str
    factor: DerivationFactor = "unknown"
    note: str = ""


class DerivationUpdatePayload(BaseModel):
    factor: DerivationFactor | None = None
    verdict: DerivationVerdict | None = None
    note: str | None = None


class VariantBrief(BaseModel):
    variant_id: str
    name: str
    filename: str
    spend: float
    payers: int
    cpp: float | None
    roas: float | None


class EvolutionStep(BaseModel):
    derivation: DerivationOut
    source: VariantBrief
    target: VariantBrief
    cpp_delta: float | None  # target.cpp - source.cpp（负值 = 降本，好）
    roas_delta: float | None


class EvolutionChain(BaseModel):
    creative_id: str
    steps: list[EvolutionStep] = Field(default_factory=list)
    pending_count: int = 0
