"""Contract guards: graph package enums must stay in sync with API schema.

The 2026-07-22 /graph 500 was caused by DERIVED_FROM being added to the
graph package + shared types but missed in the API response schema. These
tests make that class of drift fail in CI instead of in production.
"""
from __future__ import annotations

from typing import get_args

from graph import EdgeType, NodeType

from app.schemas.graph import GraphEdgeType, GraphNodeType


def test_edge_type_parity() -> None:
    assert set(get_args(EdgeType)) == set(get_args(GraphEdgeType))


def test_node_type_parity() -> None:
    assert set(get_args(NodeType)) == set(get_args(GraphNodeType))
