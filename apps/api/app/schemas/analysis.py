"""AI analysis shapes — strictly mirrors contract §4."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AnalysisPayload(BaseModel):
    """The AI analysis JSON (§4). Also the editable form body of
    ``PUT /assets/{id}`` (all §4 fields including ``creative_name``)."""

    summary: str = ""
    hook: str = ""
    conflict: str = ""
    gameplay: str = ""
    reward: str = ""
    characters: list[str] = Field(default_factory=list)
    environment: list[str] = Field(default_factory=list)
    emotion: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    # Reskin factors observed in this execution (boundary-rules §3.3) — they
    # decide only the Variant layer, never the Creative.
    variant_factors: list[str] = Field(default_factory=list)
    creative_name: str = ""
    confidence: float = 0.0


class AnalysisRequest(BaseModel):
    """Body of ``POST /analysis``."""

    asset_id: str
