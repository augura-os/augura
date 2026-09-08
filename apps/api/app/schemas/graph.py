"""Graph DTOs (contract §3 ``/graph``, ``/graph/merge``, ``/graph/split``)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

GraphNodeType = Literal["creative", "variant", "asset", "tag", "dna"]
GraphEdgeType = Literal[
    "HAS_VARIANT", "HAS_ASSET", "HAS_TAG", "SIMILAR_TO", "HAS_CREATIVE", "DERIVED_FROM"
]


class GraphNodeOut(BaseModel):
    id: str
    type: GraphNodeType
    label: str
    ref_id: str
    # creative 节点的生命周期（active/watch/archived）；其他类型为 None
    lifecycle_state: str | None = None


class GraphEdgeOut(BaseModel):
    id: str
    source: str
    target: str
    type: GraphEdgeType


class GraphOut(BaseModel):
    nodes: list[GraphNodeOut] = Field(default_factory=list)
    edges: list[GraphEdgeOut] = Field(default_factory=list)


class MergeRequest(BaseModel):
    source_creative_id: str
    target_creative_id: str
    # block 级守卫命中（推翻既定"维持拆分"裁决）时必须填写理由才能执行
    force_reason: str = ""


class SimilarRequest(BaseModel):
    source_creative_id: str
    target_creative_id: str
    reason: str = ""


class SimilarCloseRequest(BaseModel):
    source_creative_id: str
    target_creative_id: str
    # 结案理由（沉淀进 split_rulings，推翻该裁决的合并必须带新理由）
    reason: str = ""


class SplitRequest(BaseModel):
    creative_id: str
    variant_ids: list[str] = Field(default_factory=list)
