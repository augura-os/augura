"""Creative Lab prompt 资产（方法论内嵌版）。

四条 prompt 共用同一份受控词表（``lab_taxonomy``）：

1. **定性诊断**（M1，本文件已实现）：观察层 JSON → 六要素定性报告。
2. 方向池（M2）：定性报告 + product-facts → 三张方向卡。
3. Brief（M2）：方向卡 + 证据包 → A/B/C 三段 Brief。
4. S/G 自评审（M2）：先过"更简单"（S1–S4）再过"更强烈"（G1–G4），
   不过重写一次。

所有 prompt 的枚举字段由 ``lab_taxonomy`` 注入——模型只允许在词表内
取值，词表外自由发挥视为 taxonomy 漂移。prompt 文本的任何修改都必须
 bump ``LAB_PROMPT_VERSION``（诊断/Brief 落库带版本，可审计可回归）。
"""

from __future__ import annotations

import json
from typing import Any, Final

from app.services import lab_taxonomy as tax

LAB_PROMPT_VERSION: Final = "lab-v0.1.3"


def _enum(rows: Any, *extra: str) -> list[str]:
    """词表 id 列表 + 允许的兜底值，用于 schema enum 约束。"""
    return [row["id"] for row in rows] + list(extra)


# ---------------------------------------------------------------------------
# 定性诊断（M1）
# ---------------------------------------------------------------------------

DIAGNOSIS_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "required": [
        "verdict",
        "classification",
        "structure",
        "content",
        "emotion",
        "transferable_structure",
        "selling_point_candidates",
        "evidence_label",
        "uncertainties",
    ],
    "properties": {
        "verdict": {
            "type": "string",
            "description": "一句话结论：这条素材是什么、可迁移的核心是什么",
        },
        "classification": {
            "type": "object",
            "required": [
                "motif",
                "hook_mechanism",
                "form_tags",
                "timeliness",
                "opening_3s_solution",
                "brand_implant_level",
            ],
            "properties": {
                "motif": {
                    "type": "string",
                    "description": "母题一句话'X 被 Y 阻断，用 Z 解决'；说不清填 unclear",
                },
                "hook_mechanism": {
                    "type": "string",
                    "enum": _enum(tax.HOOK_MECHANISMS, "uncertain"),
                },
                "hook_reason": {"type": "string"},
                "form_tags": {
                    "type": "array",
                    "items": {"type": "string", "enum": _enum(tax.HOOK_FORM_TAGS)},
                },
                "timeliness": {
                    "type": "string",
                    "enum": _enum(tax.TIMELINESS_TAGS, "uncertain"),
                },
                "opening_3s_solution": {
                    "type": "string",
                    "enum": _enum(tax.OPENING_3S_SOLUTIONS, "uncertain"),
                },
                "opening_3s_relation_ok": {
                    "type": "boolean",
                    "description": "3 秒内是否完成关系说明（谁强谁弱/谁失控/谁被阻断）",
                },
                "brand_implant_level": {
                    "type": "string",
                    "enum": _enum(tax.BRAND_IMPLANT_LEVELS, "uncertain"),
                },
            },
        },
        "structure": {
            "type": "object",
            "required": ["segments", "ab_bridge_mode", "conflict_trigger"],
            "properties": {
                "segments": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["kind", "start_s", "end_s", "summary"],
                        "properties": {
                            "kind": {"type": "string", "enum": ["A", "B", "endcard"]},
                            "start_s": {"type": "number"},
                            "end_s": {"type": "number"},
                            "summary": {"type": "string"},
                        },
                    },
                },
                "ab_bridge_mode": {
                    "type": "string",
                    "enum": _enum(tax.AB_BRIDGE_MODES, "none", "uncertain"),
                    "description": "无桥接设计填 none，判不出填 uncertain",
                },
                "b_grammar_narrative": {
                    "type": "array",
                    "items": {"type": "string", "enum": _enum(tax.B_GRAMMAR_NARRATIVE)},
                },
                "b_grammar_ui": {
                    "type": "array",
                    "items": {"type": "string", "enum": _enum(tax.B_GRAMMAR_UI)},
                },
                "visual_route": {
                    "type": "string",
                    "enum": _enum(tax.VISUAL_ROUTES, "uncertain"),
                    "description": "按主情绪入口二选一，不混用",
                },
                "conflict_trigger": {"type": "string", "description": "冲突触发点"},
            },
        },
        "content": {
            "type": "object",
            "required": ["setting", "key_visuals"],
            "properties": {
                "setting": {"type": "string", "description": "场景/题材设定"},
                "characters": {"type": "string"},
                "key_visuals": {"type": "array", "items": {"type": "string"}},
                "choice_chain": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["beat", "choice", "feedback"],
                        "properties": {
                            "beat": {"type": "string"},
                            "choice": {
                                "type": "string",
                                "enum": ["正解", "作死"],
                            },
                            "feedback": {"type": "string"},
                        },
                    },
                },
                "strong_feedback_points": {"type": "array", "items": {"type": "string"}},
            },
        },
        "emotion": {
            "type": "object",
            "required": ["primary_entry", "emotion_curve"],
            "properties": {
                "primary_entry": {
                    "type": "string",
                    "enum": _enum(tax.EMOTION_ENTRIES, "uncertain"),
                },
                "secondary_entry": {
                    "type": ["string", "null"],
                    "enum": _enum(tax.EMOTION_ENTRIES) + [None],
                },
                "threat_ladder": {"type": ["string", "null"]},
                "emotion_curve": {
                    "type": "string",
                    "description": "一句话情绪轨迹：哪里心疼/哪里爽",
                },
            },
        },
        "transferable_structure": {
            "type": "object",
            "required": ["fixed", "reskinnable", "do_not_copy"],
            "properties": {
                "fixed": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "转化结构固定项（母题/骨架/桥接/情绪机制）",
                },
                "reskinnable": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "可换表皮项（场景/角色/美术/题材）",
                },
                "do_not_copy": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "禁止迁移项（合规与留存生命线）",
                },
            },
        },
        "selling_point_candidates": {
            "type": "object",
            "required": ["mappable_points", "spectrum_layer"],
            "properties": {
                "mappable_points": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "只能取自 product-facts 卖点清单；清单为空则给空数组",
                },
                "spectrum_layer": {
                    "type": "string",
                    "enum": _enum(tax.PRODUCT_TRUTH_SPECTRUM, "uncertain"),
                },
                "mapping_gaps": {"type": "array", "items": {"type": "string"}},
                "missing_product_facts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "product-facts 缺失项清单（缺失时列这里，不编造）",
                },
            },
        },
        "evidence_label": {
            "type": "string",
            "enum": _enum(tax.EVIDENCE_LABELS),
        },
        "uncertainties": {"type": "array", "items": {"type": "string"}},
        "vocab_gaps": {
            "type": "array",
            "description": "词表缺口提名（可选）：枚举判不出/母题说不清时必须提名；"
            "判得准的字段禁止凑数",
            "items": {
                "type": "object",
                "required": ["field", "proposal", "rationale"],
                "properties": {
                    "field": {
                        "type": "string",
                        "description": "缺口字段名，如 hook_mechanism / motif",
                    },
                    "proposal": {"type": "string", "description": "建议新词（短语）"},
                    "rationale": {
                        "type": "string",
                        "description": "一句话：为什么现有词表覆盖不了",
                    },
                },
            },
        },
    },
}


def _render_catalog() -> str:
    """把受控词表渲染成 prompt 注入文本（枚举允许值 + 一句话定义）。"""

    def rows(items: Any, keys: tuple[str, ...]) -> str:
        return "；".join(
            "/".join(str(item[key]) for key in keys if item.get(key)) for item in items
        )

    sections = [
        "钩子机制（hook_mechanism 允许值）："
        + rows(tax.HOOK_MECHANISMS, ("id", "name", "motive")),
        "形式标签（form_tags 允许值，可多选）："
        + rows(tax.HOOK_FORM_TAGS, ("id", "name")),
        "时效（timeliness 允许值）：" + rows(tax.TIMELINESS_TAGS, ("id", "name")),
        "0-3s 解法（opening_3s_solution 允许值）："
        + rows(tax.OPENING_3S_SOLUTIONS, ("id", "name")),
        "品牌植入级别（brand_implant_level 允许值，侵入感递增）："
        + rows(tax.BRAND_IMPLANT_LEVELS, ("id", "name")),
        "桥接模式（ab_bridge_mode 允许值，另可填 none/uncertain）："
        + rows(tax.AB_BRIDGE_MODES, ("id", "name")),
        "B 段叙事语法（b_grammar_narrative 允许值，可多选）："
        + rows(tax.B_GRAMMAR_NARRATIVE, ("id", "name")),
        "B 段 UI 语法（b_grammar_ui 允许值，可多选）："
        + rows(tax.B_GRAMMAR_UI, ("id", "name")),
        "画面路线（visual_route 允许值，二选一不混用，另可填 uncertain）："
        + rows(tax.VISUAL_ROUTES, ("id", "name")),
        "情绪入口（primary_entry/secondary_entry 允许值）："
        + rows(tax.EMOTION_ENTRIES, ("id", "name", "essence")),
        "三层光谱（spectrum_layer 允许值）："
        + rows(tax.PRODUCT_TRUTH_SPECTRUM, ("id", "name")),
        "证据标签（evidence_label 允许值）："
        + rows(tax.EVIDENCE_LABELS, ("id", "name")),
    ]
    return "\n".join(f"- {line}" for line in sections)


_RULE8_ZH = (
    "8. 输出语言：除枚举 id 外，全部字段一律使用简体中文，不夹杂英文"
    "（专有名词/品牌名/外文原词引用除外）。"
)
_RULE8_EN = (
    "8. Output language: except enum ids, write every field value in English "
    "(proper nouns / brand names / quoted original terms excepted). "
    "Selling-point names quoted from product-facts must also be rendered in "
    "English: give a faithful English translation followed by the original "
    "name in parentheses on first mention, e.g. \"Pirate Defense (抵御海盗)\"."
)


def diagnosis_system(lang: str = "zh") -> str:
    # lang 是运行时参数而非 prompt 变更：zh 文案逐字不变，故不 bump 版本；
    # 落库行经 audience_context.lang 记录实际输出语言。
    rule8 = _RULE8_EN if lang == "en" else _RULE8_ZH
    return "\n".join(
        [
            "你是资深效果广告创意分析师（游戏 UA 方向）。任务：基于「观察层」"
            "结构化数据，对一条广告素材输出「定性报告」。",
            "",
            "铁律：",
            "1. 只使用观察层提供的证据（summary/hook/conflict/gameplay/reward/"
            "tags 等字段）。观察层没有的信息一律进 uncertainties，禁止编造画面。",
            "2. 所有枚举字段必须命中给定受控词表；判不出填 uncertain，"
            "并在 uncertainties 说明候选与卡点。",
            "3. 母题检验：用一句话说清「X 被 Y 阻断，用 Z 解决」。"
            "说不清填 unclear 并给最近似候选——母题模糊的参考片不值得改编。",
            "4. 可迁移结构拆三列：fixed（转化结构固定项）/ reskinnable"
            "（可换表皮项）/ do_not_copy（合规与留存生命线，禁止迁移）。",
            "5. 卖点映射候选只能取自用户提供的 product-facts「卖点清单」；"
            "清单缺失或不足时在 missing_product_facts 列缺失清单，"
            "禁止编造产品事实。",
            "6. evidence_label 规则：无真实投放数据与历史图证据支撑时，"
            "一律 reference_only，并在 verdict 中明示「未验证的测试假设」。",
            "7. 输出严格 JSON，结构以 user message 中的 schema 为准，"
            "不输出 schema 之外的字段。",
            rule8,
            "9. 输出务必紧凑：各数组字段条数上限——选择链 ≤6、key_visuals ≤8、"
            "fixed/reskinnable/do_not_copy 各 ≤6、strong_feedback_points ≤6、"
            "mappable_points/mapping_gaps/missing_product_facts 各 ≤8、"
            "uncertainties ≤6；每条一句话，优先覆盖与质量，禁止凑数式冗长。",
            "10. 词表缺口提名：任何枚举字段判不出（uncertain）或母题说不清"
            "（unclear）时，除按第 2/3 条处理外，必须在 vocab_gaps 提名新词候选"
            "（field=缺口字段名、proposal=建议新词短语、rationale=一句话说明"
            "现有词表为何覆盖不了）；判得准的字段不要提名，禁止凑数。",
        ]
    )


def diagnosis_user(
    *,
    observation: dict[str, Any],
    product_facts: dict[str, Any] | None,
    audience: dict[str, Any] | None,
    graph_evidence: dict[str, Any] | None,
) -> str:
    return "\n\n".join(
        [
            "【观察层】\n" + json.dumps(observation, ensure_ascii=False, indent=2),
            "【产品事实 product-facts】\n"
            + (
                json.dumps(product_facts, ensure_ascii=False, indent=2)
                if product_facts
                else "未配置——卖点映射候选给空数组，并在 missing_product_facts "
                "列出需要补全的产品事实项"
            ),
            "【受众上下文】\n"
            + (
                json.dumps(audience, ensure_ascii=False, indent=2)
                if audience
                else "未提供"
            ),
            "【图证据】\n"
            + (
                json.dumps(graph_evidence, ensure_ascii=False, indent=2)
                if graph_evidence
                else "无——evidence_label 只能填 reference_only"
            ),
            "【受控词表（枚举允许值）】\n" + _render_catalog(),
            "【输出 schema】\n"
            + json.dumps(DIAGNOSIS_SCHEMA, ensure_ascii=False, indent=2),
        ]
    )


# ---------------------------------------------------------------------------
# 翻译 prompt（per-lang 报告的低成本跨语言路径）
# ---------------------------------------------------------------------------
# 定位：同一素材已有另一语言的当前版本诊断时，切语言 = 翻译而非重跑。
# 译文行沿用源行 prompt_version（内容血缘不变），audience_context 记
# translated_from；改本 prompt 不涉及分析口径，无需 bump LAB_PROMPT_VERSION。

_LANG_NAMES: Final = {"zh": "简体中文", "en": "英文"}


def translation_system() -> str:
    return "\n".join(
        [
            "你是专业的游戏广告创意本地化译者。任务：把一份广告素材「定性报告」"
            "JSON 完整翻译为目标语言。",
            "",
            "铁律：",
            "1. JSON 结构完全不变：键名、数组顺序、数字、布尔值原样保留。",
            "2. 枚举 id 一律原样（钩子 H1–H9、choice 的 正解/作死、"
            "evidence_label、各 enum 字段等），禁止翻译或改写。",
            "3. 只翻译自然语言文本值（verdict、summary、各类描述文字）。",
            "4. 专有名词/品牌名保留原文（如 Rise of Kingdoms），可附通译名。",
            "5. 忠实翻译，禁止增删信息、禁止重新分析、禁止修正结论。",
            "6. 输出严格 JSON，不输出任何额外文字或代码块标记。",
        ]
    )


def translation_user(*, source_lang: str, target_lang: str, report: dict[str, Any]) -> str:
    return (
        f"把以下定性报告 JSON 从{_LANG_NAMES.get(source_lang, source_lang)}翻译为"
        f"{_LANG_NAMES.get(target_lang, target_lang)}，遵守 system 的全部铁律：\n"
        + json.dumps(report, ensure_ascii=False, indent=2)
    )


# ---------------------------------------------------------------------------
# M2 预留：方向池 / Brief / S-G 自评审
# ---------------------------------------------------------------------------
# 三条 prompt 在 M2 前置评审后落地本文件，契约已在立项规划锁定：
# - 方向池：借鉴核心 × 突出卖点 × 情绪入口 三轴组合 + 方法论方向 schema
#   （核心假设 / A段 hook ≤12s 硬上限 15s / B段选择链 ✅正解❌作死 /
#   产品桥接+映射缺口 / 可扩展变体 / 测试指标带口径 / 风险 / 待人工决策）
# - Brief：A/B/C 三段结构 + S1–S4/G1–G4 生成约束 + 合规标注位
# - S/G 自评审：先 S 后 G，不过重写一次，评审结果随 Brief 落库
