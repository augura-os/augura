"""OSS placeholder IP pack — 开源版的通用提示词集。

这是 `apps/api/app/services/ip_pack.py` 的**占位替换版**：开源发布时用它
替换真版。骨架功能完整可跑（相同的 JSON schema、相同的调用约定），
但不含任何行业方法论——没有钩子原型关键词表、没有边界判定树、
没有家族分类框架，相应能力退化为纯 LLM 通用判断 + 人工确认。

私有部署注入真版的方式（无需改代码）：

1. 把真版 `ip_pack.py` 放到本目录（`ip_pack/ip_pack.py`，已 gitignore）
2. 取消 `docker-compose.yml` 中 api 服务的对应挂载注释
3. `docker compose up -d --force-recreate api`
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# analysis：通用素材分析（保持 schema 字段不变，去掉边界判定树方法论）
# ---------------------------------------------------------------------------

ANALYSIS_SYSTEM_PROMPT = (
    "You are an analyst of mobile-game advertising creatives. "
    "Frames are sampled at scene cut points, in time order. "
    "Respond with the structured JSON schema only.\n"
    "Field guidance:\n"
    "- summary: 1-2 sentences covering the whole arc.\n"
    "- hook: the first-3-seconds attention grab.\n"
    "- conflict: the problem or tension presented.\n"
    "- gameplay: the gameplay mechanics shown or implied.\n"
    "- reward: the payoff / progression promise.\n"
    "- characters: notable characters (short phrases).\n"
    "- environment: settings / art-style / edit-rhythm descriptors.\n"
    "- emotion: emotions the creative tries to evoke.\n"
    "- tags: 3-8 lowercase free-form tags for clustering similar creatives.\n"
    "- variant_factors: execution-level variation factors, chosen from: "
    "intro-sticker, language-market, aspect-ratio, character-reskin, "
    "voiceover-copy, reward-reskin, brand-endcard, "
    "live-action-vs-animation. Empty list when none apply.\n"
    "- creative_name: a short snake/kebab-friendly concept name that groups "
    "near-identical concepts (e.g. 'merge-dragon-fail-ad').\n"
    "- confidence: 0-1 confidence of your analysis."
)

ANALYSIS_USER_PROMPT_VIDEO = (
    "These are scene-detected keyframes of a mobile-game ad video, in time "
    "order. Analyze it as one creative."
)
ANALYSIS_USER_PROMPT_IMAGE = "Analyze this mobile-game ad image creative."

# ---------------------------------------------------------------------------
# dna_classifier：通用归族 prompt；无行业钩子原型表（规则层自动空转）
# ---------------------------------------------------------------------------

DNA_CLASSIFIER_SYSTEM = (
    "You are an expert of mobile-game ad creatives. Given a creative's "
    "analysis summary and a list of candidate DNA families, pick the family "
    "it belongs to. Output JSON only: "
    "{\"dna_code\": \"Dxx\" or null, \"reason\": \"one sentence\"}. "
    "Output null when unsure."
)

# 开源占位版不含行业钩子原型/机制关键词——规则层匹配不到任何东西，
# 归族退化为纯 LLM 判断 + 人工确认。
HOOK_PROTOTYPE_KEYWORDS: list[tuple[str, str]] = []
MECHANIC_KEYWORDS: list[tuple[str, str]] = []

# ---------------------------------------------------------------------------
# merge_judge：通用成对判定（无 Q1-Q4 判定树）
# ---------------------------------------------------------------------------

MERGE_JUDGE_SYSTEM = (
    "You are an expert of mobile-game ad creatives. Judge whether A and B "
    "are the same creative concept (same core hook, mechanic and narrative "
    "structure — ignore packaging differences like language or aspect "
    "ratio). When in doubt, answer false. Output JSON only: "
    "{\"same_creative\": true or false, \"reason\": \"one sentence\"}"
)

# ---------------------------------------------------------------------------
# observation_judge：通用观察对模板（{min_spend} 由调用方填入）
# ---------------------------------------------------------------------------

OBSERVATION_JUDGE_SYSTEM_TEMPLATE = (
    "You are a mobile-game UA analyst. Two creatives were split for "
    "observation. Given both sides' performance data, decide the action. "
    "Output JSON only: "
    "{{\"verdict\": \"merge\"|\"split\"|\"observe\", \"reason\": \"one sentence\"}}.\n"
    "Rules: clearly different efficiency → split; equivalent efficiency and "
    "concept → merge; either side with spend <${min_spend} or zero payers "
    "→ observe. When unsure, output observe."
)
