"""Unit tests for app.services.analysis._coerce_list_fields."""
from __future__ import annotations

from app.services.analysis import _coerce_list_fields


def test_string_split_on_english_comma() -> None:
    parsed = {"tags": "a, b, c"}
    assert _coerce_list_fields(parsed) == {"tags": ["a", "b", "c"]}


def test_string_split_on_cjk_separators() -> None:
    parsed = {"characters": "国王，厨师、村民"}
    assert _coerce_list_fields(parsed) == {"characters": ["国王", "厨师", "村民"]}


def test_real_lists_pass_through() -> None:
    parsed = {"tags": ["a", "b"], "summary": "text"}
    assert _coerce_list_fields(parsed) == parsed


def test_non_dict_passes_through() -> None:
    assert _coerce_list_fields("not a dict") == "not a dict"
    assert _coerce_list_fields(None) is None


def test_empty_string_becomes_empty_list() -> None:
    assert _coerce_list_fields({"emotion": ""}) == {"emotion": []}


def test_all_list_fields_covered() -> None:
    parsed = {field: "x, y" for field in
              ("characters", "environment", "emotion", "tags", "variant_factors")}
    result = _coerce_list_fields(parsed)
    assert isinstance(result, dict)
    for value in result.values():
        assert value == ["x", "y"]
