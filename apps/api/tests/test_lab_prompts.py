"""lab_prompts 漂移守卫：DIAGNOSIS_SCHEMA 枚举与 lab_taxonomy 词表必须同源。

词表与 schema 是「单一事实源 + 派生物」关系——两边改一边不改即 taxonomy
漂移，本文件用集合断言把漂移挡在 CI。
"""
from __future__ import annotations

from app.services import lab_taxonomy as tax
from app.services.lab_prompts import (
    DIAGNOSIS_SCHEMA,
    _render_catalog,
    diagnosis_user,
)


def _ids(rows) -> set[str]:  # noqa: ANN001, ANN202
    return {row["id"] for row in rows}


def _prop(*path: str) -> dict:
    node: dict = DIAGNOSIS_SCHEMA
    for key in path:
        node = node["properties"][key]
    return node


class TestEnumDriftGuard:
    def test_hook_mechanism_enum_matches_taxonomy(self) -> None:
        enum = set(_prop("classification", "hook_mechanism")["enum"])
        assert enum == _ids(tax.HOOK_MECHANISMS) | {"uncertain"}

    def test_h10_in_hook_mechanism_enum(self) -> None:
        # H10 荒诞奇观：词表新词必须同步进 schema enum（漂移即拦截）
        assert any(row["id"] == "H10" for row in tax.HOOK_MECHANISMS)
        assert "H10" in _prop("classification", "hook_mechanism")["enum"]

    def test_primary_entry_enum_matches_taxonomy(self) -> None:
        enum = set(_prop("emotion", "primary_entry")["enum"])
        assert enum == _ids(tax.EMOTION_ENTRIES) | {"uncertain"}

    def test_secondary_entry_enum_matches_taxonomy_plus_null(self) -> None:
        enum = set(_prop("emotion", "secondary_entry")["enum"])
        assert enum == _ids(tax.EMOTION_ENTRIES) | {None}

    def test_spectrum_layer_enum_matches_taxonomy(self) -> None:
        enum = set(_prop("selling_point_candidates", "spectrum_layer")["enum"])
        assert enum == _ids(tax.PRODUCT_TRUTH_SPECTRUM) | {"uncertain"}

    def test_ab_bridge_mode_enum_matches_taxonomy(self) -> None:
        enum = set(_prop("structure", "ab_bridge_mode")["enum"])
        assert enum == _ids(tax.AB_BRIDGE_MODES) | {"none", "uncertain"}

    def test_evidence_label_enum_matches_taxonomy(self) -> None:
        enum = set(_prop("evidence_label")["enum"])
        assert enum == _ids(tax.EVIDENCE_LABELS)


class TestProductFactsMissingGuidance:
    def test_user_prompt_without_product_facts_lists_missing_guidance(self) -> None:
        prompt = diagnosis_user(
            observation={"summary": "虚构观察层"},
            product_facts=None,
            audience=None,
            graph_evidence=None,
        )
        assert "未配置" in prompt
        assert "missing_product_facts" in prompt  # 缺失清单指引文案
        assert "reference_only" in prompt  # 无图证据时的标签指引

    def test_user_prompt_with_product_facts_embeds_json(self) -> None:
        prompt = diagnosis_user(
            observation={"summary": "虚构观察层"},
            product_facts={"core_loop": "合成部件组装机甲", "selling_points": ["合成"]},
            audience={"region": "FAKE"},
            graph_evidence={"similar_count": 2, "threshold": 0.85},
        )
        assert "合成部件组装机甲" in prompt
        assert "FAKE" in prompt
        assert "similar_count" in prompt


class TestRenderCatalog:
    def test_catalog_covers_all_hook_mechanisms(self) -> None:
        catalog = _render_catalog()
        for row in tax.HOOK_MECHANISMS:
            assert row["id"] in catalog

    def test_catalog_covers_all_emotion_entries(self) -> None:
        catalog = _render_catalog()
        assert len(tax.EMOTION_ENTRIES) == 8
        for entry in tax.EMOTION_ENTRIES:
            assert entry["id"] in catalog
