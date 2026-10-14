"""lab_validate.validate 对 DIAGNOSIS_SCHEMA 的校验测试（虚构内容负载）。"""
from __future__ import annotations

import copy

from app.services.lab_prompts import DIAGNOSIS_SCHEMA
from app.services.lab_validate import validate


def _valid_output() -> dict:
    """一份完整合法的诊断输出（全部枚举取词表内值）。"""
    return {
        "verdict": "虚构素材：机甲被强敌阻断，用合成升级解决；可迁移核心是损失重建。",
        "classification": {
            "motif": "机甲被强敌摧毁，用合成升级复仇",
            "hook_mechanism": "H5",
            "hook_reason": "先拥有后失去",
            "form_tags": ["engine_render", "ai_skit"],
            "timeliness": "evergreen",
            "opening_3s_solution": "event_in_progress",
            "opening_3s_relation_ok": True,
            "brand_implant_level": "endcard_only",
        },
        "structure": {
            "segments": [
                {"kind": "A", "start_s": 0.0, "end_s": 8.5, "summary": "基地被毁"},
                {"kind": "B", "start_s": 8.5, "end_s": 36.0, "summary": "合成重建"},
                {"kind": "endcard", "start_s": 36.0, "end_s": 40.0, "summary": "品牌收束"},
            ],
            "ab_bridge_mode": "theme_direct_link",
            "b_grammar_narrative": ["crisis_solution_flourish"],
            "b_grammar_ui": ["floater_escalation"],
            "visual_route": "survival",
            "conflict_trigger": "强敌夜袭摧毁基地",
        },
        "content": {
            "setting": "虚构废土基地",
            "characters": "指挥官与机甲",
            "key_visuals": ["废墟全景", "合成光效"],
            "choice_chain": [
                {"beat": "选防御方案", "choice": "作死", "feedback": "基地再被击穿"}
            ],
            "strong_feedback_points": ["合成成功飘字"],
        },
        "emotion": {
            "primary_entry": "loss_rebuild",
            "secondary_entry": None,
            "threat_ladder": None,
            "emotion_curve": "先心疼后爽",
        },
        "transferable_structure": {
            "fixed": ["损失重建循环"],
            "reskinnable": ["废土场景", "机甲角色"],
            "do_not_copy": ["写实血腥镜头"],
        },
        "selling_point_candidates": {
            "mappable_points": ["合成玩法"],
            "spectrum_layer": "strong_direct",
            "mapping_gaps": [],
            "missing_product_facts": [],
        },
        "evidence_label": "reference_only",
        "uncertainties": [],
    }


class TestValidPayload:
    def test_complete_valid_payload_passes(self) -> None:
        assert validate(_valid_output(), DIAGNOSIS_SCHEMA) == []

    def test_nullable_union_accepts_none(self) -> None:
        payload = _valid_output()
        payload["emotion"]["secondary_entry"] = None
        assert validate(payload, DIAGNOSIS_SCHEMA) == []

    def test_nullable_union_accepts_enum_value(self) -> None:
        payload = _valid_output()
        payload["emotion"]["secondary_entry"] = "superiority"
        assert validate(payload, DIAGNOSIS_SCHEMA) == []


class TestVocabGapsOptional:
    """vocab_gaps 是可选字段：缺省不拦；带则逐项校验 required。"""

    def test_payload_with_vocab_gaps_passes(self) -> None:
        payload = _valid_output()
        payload["vocab_gaps"] = [
            {
                "field": "hook_mechanism",
                "proposal": "假输赢过山车",
                "rationale": "输赢反复横跳，现有钩子机制覆盖不了",
            }
        ]
        assert validate(payload, DIAGNOSIS_SCHEMA) == []

    def test_payload_without_vocab_gaps_passes(self) -> None:
        assert "vocab_gaps" not in DIAGNOSIS_SCHEMA["required"]
        assert validate(_valid_output(), DIAGNOSIS_SCHEMA) == []

    def test_vocab_gap_item_missing_rationale_fails(self) -> None:
        payload = _valid_output()
        payload["vocab_gaps"] = [{"field": "motif", "proposal": "身份错位"}]
        errors = validate(payload, DIAGNOSIS_SCHEMA)
        assert "vocab_gaps[0].rationale: required missing" in errors


class TestViolations:
    def test_enum_violation_path(self) -> None:
        payload = _valid_output()
        payload["classification"]["hook_mechanism"] = "H99"
        errors = validate(payload, DIAGNOSIS_SCHEMA)
        assert "classification.hook_mechanism: enum violation" in errors

    def test_enum_violation_in_array_item(self) -> None:
        payload = _valid_output()
        payload["classification"]["form_tags"] = ["ai_skit", "hologram"]
        errors = validate(payload, DIAGNOSIS_SCHEMA)
        assert "classification.form_tags[1]: enum violation" in errors

    def test_required_missing_path(self) -> None:
        payload = _valid_output()
        del payload["classification"]["hook_mechanism"]
        errors = validate(payload, DIAGNOSIS_SCHEMA)
        assert "classification.hook_mechanism: required missing" in errors

    def test_required_missing_at_root(self) -> None:
        payload = _valid_output()
        del payload["verdict"]
        errors = validate(payload, DIAGNOSIS_SCHEMA)
        assert "verdict: required missing" in errors

    def test_type_violation_path(self) -> None:
        payload = _valid_output()
        payload["structure"]["segments"] = "not-an-array"
        errors = validate(payload, DIAGNOSIS_SCHEMA)
        assert "structure.segments: type violation (expected array)" in errors

    def test_boolean_not_accepted_as_number(self) -> None:
        payload = _valid_output()
        payload["structure"]["segments"][0]["start_s"] = True
        errors = validate(payload, DIAGNOSIS_SCHEMA)
        assert "structure.segments[0].start_s: type violation (expected number)" in errors

    def test_nullable_union_rejects_out_of_vocab_string(self) -> None:
        payload = _valid_output()
        payload["emotion"]["secondary_entry"] = "greed"
        errors = validate(payload, DIAGNOSIS_SCHEMA)
        assert "emotion.secondary_entry: enum violation" in errors

    def test_multiple_errors_all_reported(self) -> None:
        payload = _valid_output()
        broken = copy.deepcopy(payload)
        del broken["verdict"]
        broken["classification"]["timeliness"] = "weekly"
        broken["emotion"]["primary_entry"] = 42
        errors = validate(broken, DIAGNOSIS_SCHEMA)
        assert "verdict: required missing" in errors
        assert "classification.timeliness: enum violation" in errors
        assert "emotion.primary_entry: type violation (expected string)" in errors
