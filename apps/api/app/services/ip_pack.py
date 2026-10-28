"""OSS IP pack — 公开版通用提示词与可公开分类法。

只包含可公开的模型提示与分类结构，不包含真实素材、人员姓名、项目标识、
投放数据、客户信息或私有判例。私有部署可用本地 IP 包扩展私有口径。

私有部署注入自定义 IP 包的方式（无需改代码）：

1. 把真版 `ip_pack.py` 放到本目录（`ip_pack/ip_pack.py`，已 gitignore）
2. 取消 `docker-compose.yml` 中 api 服务的对应挂载注释
3. `docker compose up -d --force-recreate api`
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# analysis：通用素材分析（保持 schema 字段与调用约定）
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
    "- creative_name: a short snake/kebab-friendly concept name built from "
    "the CORE GAMEPLAY LOOP and the core selling point (e.g. "
    "'merge-dragon-fail-ad', 'island-base-builder-choice-fail'). Name the "
    "body gameplay, NOT the opening hook — the hook belongs in the name "
    "only when the hook IS the gameplay body. Variants of the same "
    "gameplay body must get near-identical names even when their "
    "first-seconds hooks differ.\n"
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
# merge_judge：指纹卡判同（v4：LLM 打骨架指纹卡 + 代码规则匹配）
# ---------------------------------------------------------------------------

# prompt 版本号：随每次改动 bump，判定落库 reason_params 带此版本可回归。
# v3（2026-10-21，用户裁决口径）：玩法本体（核心四维度）全同即变体家族。
# v4（2026-10-22，架构升级）：成对 LLM 判定 → 每素材一次 LLM 打"骨架指纹卡"
# （可缓存复用）+ 代码规则匹配（零成本、可复现）；same_concept 废弃，
# 只留三态 same_variant_family / different_concept / 灰区（None）。
# v4.1（2026-10-26，B2 回测修复）：收窄 slg-zero-to-prosperity（默认引力码）；
# track_candidates 软分层选码（降低选码抖动）；判异复核闸
# （different_concept 先复核再落判）；收编 3 个 skeleton_new 提案进骨架库。
MERGE_JUDGE_PROMPT_VERSION: str = "merge-judge-v4.1"
CARD_PROMPT_VERSION: str = "fingerprint-card-v2"

# --- 骨架库（种子版，2026-10-22 定稿）-------------------------------------
# 分层：family（品类族）→ track（赛道）→ skeleton（叙事骨架）。
# 行业信源：Naavik / AppGrowing / 广大大 / Segwise / Mistplay / Lancaric /
# Sanlo 等 2024-2026 报告（见设计文档 Augura_AI自动判异设计方案_20261020.md）。
# 变体维度（换角色/场景/美术/前贴/BGM/口播/真人贴片）永不拆族；
# ending（结局）是骨架身份的一部分：同骨架不同结局 → 判异
# （依据：Bad North 式塔防成功/失败用户判拆）。

CREATIVE_FAMILIES: tuple[str, ...] = ("slg", "merge", "puzzle", "other")

FAMILY_TRACKS: dict[str, tuple[str, ...]] = {
    # 用户口径：先分辨主打模拟经营 / 街机操作 / 放置成长 / 塔防
    "slg": ("sim-builder", "arcade-action", "idle-growth", "tower-defense", "other"),
    # Merge 六级赛道：剧情驱动 / 玩法展示 / 误导 / ASMR / UGC / 品宣
    "merge": ("drama", "gameplay", "misleading", "asmr", "ugc", "brand"),
    # Puzzle 不分赛道：骨架轴（展示框架×情绪钩子）直接承载区分
    "puzzle": ("puzzle",),
    "other": ("other",),
}

# skeleton 代码 → 一句话定义（注入 prompt 供 LLM 选码）
SKELETONS: dict[str, dict[str, str]] = {
    "slg": {
        "slg-zero-to-prosperity": (
            "正反馈·零建造→繁荣。必须同时满足：(a) 明确的建造/升级动作序列；"
            "(b) 繁荣壮大结果画面（含 DAY 天数流逝变体）。排除：仅静态岛屿/"
            "基地场景展示不算；含生存危机抉择的选 slg-survival-dilemma；"
            "前后转折结构选 slg-twist-contrast；塔防守城选 slg-td-holdout。"
            "两条正向特征不齐时不许选本码，输出 skeleton=null + skeleton_new 兜底"
        ),
        "slg-unit-growth": "正反馈·初级小兵→越来越强，ending=success",
        "slg-deliberate-fail": (
            "负反馈·故意失败（二选一致败/经营破产/脑残操作落败——域写进 "
            "variant_notes），ending=fail"
        ),
        "slg-twist-contrast": (
            "对比·前后转折（先成后败 ending=twist-down / "
            "先败后成 ending=twist-up）"
        ),
        "slg-side-by-side": "对比·同屏对比",
        "slg-player-archetypes": "多玩家视角·不同标签玩家怎么玩",
        "slg-td-holdout": "Bad North 式塔防守城（ending=success/fail，结局不同即不同族）",
        "slg-multiplier-gates": "数字选择门·倍增跑道（Last War 式）",
        "slg-survival-dilemma": "极端生存抉择·绝境救人（Whiteout 式冰冻家庭）",
        "slg-pull-pin": "拔针解谜（Evony 式）",
        "slg-drag-item-fix": "拖拽道具·二选一修/救，选错即 FAIL",
        "slg-roguelike-pick3": "Roguelike 三选一·升级选牌叠 build",
        "slg-legion-clash": "军团对冲·暴兵碾压",
        "slg-territory-paint": "圈地涂色·领土扩张（io 式）",
        # v4.1 收编的 skeleton_new 提案（B2 v4 回测产出，人工命名定稿）
        "slg-humiliation-level-hook": (
            "羞辱崛起弧——弱小角色因低等级/LV 徽章被嘲讽羞辱，随后逆袭崛起。"
            "锚点：必须有'被羞辱→崛起'的因果；纯等级数字对比无羞辱剧情的归 slg-unit-growth"
        ),
        "survival-crisis-drama-hook": (
            "生存危机剧情弧——角色陷入寒冷/饥荒/追杀等生存危机的纯剧情展示。"
            "锚点：与 slg-survival-dilemma 的区分是'无玩家抉择交互'——"
            "dilemma 是观众看角色做二选一/资源分配，drama 是线性叙事"
        ),
        "slg-fake-drama-hook": (
            "真人狗血短剧前贴→切真 SLG 玩法（前贴与玩法弱相关，"
            "presentation_tags 通常含 live-action-drama）。"
            "锚点：前贴是真人短剧且与 B 段玩法无因果桥接"
        ),
    },
    "merge": {
        "mg-sympathy-crisis": "剧情·博取同情生存危机（挨冻母女式；常以悬置结局收尾）",
        "mg-betrayal-revenge": "剧情·出轨背叛复仇",
        "mg-mystery-serial": "剧情·悬疑揭秘连环悬念（Merge Mansion 奶奶式）",
        "mg-micro-drama": "剧情·短剧化包装",
        "mg-meta-selfproof": "剧情·元广告自证清白（'这真的是游戏画面'）",
        "mg-cliffhanger": "剧情·危机悬置结局弧（危机→部分修复→新挫折→刻意不给结局）",
        "mg-pure-merge": "玩法·纯合成直给",
        "mg-renovate-contrast": "玩法·修复翻新前后对比",
        "mg-fail": "玩法·故意失败",
        "mg-challenge": "玩法·挑战挑衅",
        "mg-order-service": "玩法·订单经营服务闭环（合成食材→完成订单）",
        "mg-dressup-skin": "玩法·时尚评判皮 playable（皮是装扮二选一，落地是合成）",
        "mg-fake-gameplay": "误导·假玩法",
        "mg-cross-genre-hook": "误导·跨品类小游戏 hook",
        "mg-fake-narrative": "误导·假叙事（广告剧情游戏里根本没有）",
        "mg-asmr-satisfying": "ASMR/满足感（整理秩序/清洁/连锁爽感）",
        "mg-ugc-real": "UGC·真实玩家/真人",
        "mg-ugc-fake": "UGC·演员伪装（假网红/假播客）",
        "mg-liveops-seasonal": "品宣/运营·节日活动同步素材",
        "mg-brand": "品宣",
    },
    "puzzle": {
        "pz-rescue": "险境营救弧（Save the King 式；玩法载体可换）",
        "pz-fail-bait": "故意失败挑衅弧",
        "pz-iq-challenge": "智商挑战弧（Only 1% 式）",
        "pz-satisfaction": "满足感释放弧（ASMR 连锁/解压陪伴；真实录屏或 AI 生成）",
        "pz-pull-pin": "拔针机关弧",
        "pz-sort-organize": "整理分类弧（水排序/Hexa/Block Jam）",
        "pz-female-revenge": "女性逆袭叙事弧",
        "pz-renovate-meta": "装修建造弧（消除赚星→装修）",
        "pz-level-progression": "多关递进弧（纯录屏难度曲线）",
        "pz-screw-unscrew": "拧螺丝/拆解机关弧",
        "pz-unblock-jam": "堵塞疏通弧（堵车/停车/滑动块逃生）",
        "pz-dressup-dilemma": "时尚装扮二选一弧（NPC 评判，社交性死亡惩罚）",
        "pz-ai-spectacle": "AI 生成奇观钩（前贴奇观+真实玩法）",
        "pz-fake-influencer": "假网红/假播客弧",
    },
}

# 表现形式正交标签层（与玩法骨架叠加出现，永不作为拆族依据）
PRESENTATION_TAGS: tuple[str, ...] = (
    "live-action-drama",   # 真人尬剧/短剧
    "ugc-voiceover",       # UGC 口播/试玩解说
    "street-interview",    # 街头采访式
    "ai-generated",        # AI 生成画面/虚拟人
    "challenge-copy",      # "只有1%能过"挑衅文案
    "meta-ad",             # 自黑 meta/假广告成真
)

FINGERPRINT_CARD_SYSTEM = (
    "You are an expert of mobile-game ad creatives. Given the analysis "
    "evidence of ONE creative, produce its structural fingerprint card. "
    "Output JSON only.\n"
    "Fields:\n"
    "- family: one of slg|merge|puzzle|other (which game-category taxonomy "
    "this creative fits).\n"
    "- track: the sub-direction within that family (the first entry of "
    "track_candidates; keep it consistent with your top candidate).\n"
    "- track_candidates: REQUIRED. 1-2 most likely tracks for this creative, "
    "each as {\"track\": <code from the family's track list>, \"evidence\": "
    "<one short evidence phrase>}. List them BEFORE choosing the skeleton; "
    "the final track is the first candidate.\n"
    "- skeleton: the narrative-skeleton code from the provided library that "
    "best matches the creative's STORY STRUCTURE (not its art, actors, "
    "copy or first-seconds sticker). You may pick from the UNION of all "
    "candidate tracks' skeletons (the full family library is provided) — "
    "do not restrict yourself to the first candidate's track. If no "
    "skeleton fits, output \"skeleton\": null and fill skeleton_new with a "
    "short proposed name.\n"
    "- ending: success|fail|twist-up|twist-down|open|none|unknown "
    "(twist-up = fails-then-succeeds, twist-down = succeeds-then-fails, "
    "open = deliberately unresolved cliffhanger).\n"
    "- loop_signature: short phrase for the core gameplay loop "
    "(e.g. base-builder, merge-2, match-3, runner-gates).\n"
    "- hook_fingerprint: 1-2 sentences describing the A-segment (pre-roll "
    "hook). A/B boundary rule: the A-segment is everything BEFORE the "
    "first SUSTAINED (>=3s) in-game gameplay footage (game UI / mechanics "
    "/ build-merge-battle sequence); it may run up to 15s. If no gameplay "
    "ever appears, the whole creative is A-segment and "
    "gameplay_fingerprint is null. If gameplay starts at 0s, treat the "
    "first seconds of gameplay as the cold-open hook.\n"
    "- gameplay_fingerprint: 1-2 sentences describing the B-segment "
    "(core gameplay payoff), or null.\n"
    "- presentation_tags: subset of the provided presentation tag list "
    "(production-form labels; they NEVER affect family/track/skeleton).\n"
    "- variant_notes: free text on execution-level variation factors "
    "(actor/scene/art-style/BGM/voiceover/language/aspect-ratio changes); "
    "for slg-deliberate-fail also note the fail domain "
    "(choice|management|blunder).\n"
    "- misleading: true when the advertised content does not match the "
    "actual gameplay.\n"
    "Rules: packaging differences (hook sticker, actor, copy, CTA, "
    "language, aspect ratio, scene dressing, live-action framing) NEVER "
    "change family/track/skeleton. Judge structure, not surface."
)

# 判异复核闸（merge-judge-v4.1）：两张指纹卡规则匹配出 different_concept 后，
# 复核≠重投同一任务——输入两张卡 + 具体分歧字段 + 两侧证据，靶向验证该
# 分歧点是否成立。复核失败或推翻 → 灰区人工，不落判异。
SPLIT_RECHECK_SYSTEM = (
    "You are an expert reviewer of mobile-game ad creative pairs. A "
    "rule-based matcher declared two creatives DIFFERENT based on their "
    "fingerprint cards. Your job is to verify ONLY the stated divergence "
    "point — do not re-judge the whole pair.\n"
    "Input: the two fingerprint cards (JSON), the divergent field with both "
    "sides' values, and both creatives' analysis evidence.\n"
    "Decide whether the divergence is REAL: check the evidence texts for "
    "whether the two values genuinely describe different structures. "
    "Variant-level differences (hook sticker, actor, scene, copy, BGM, "
    "language, aspect ratio, presentation form) NEVER justify a split. "
    "If the divergence comes from unstable label choice on borderline "
    "evidence (both sides could plausibly carry the same value), reject it.\n"
    "Output JSON only: {\"split_confirmed\": true|false, \"reason\": \"one "
    "sentence citing the decisive evidence\"}."
)

# ---------------------------------------------------------------------------
# merge_family_experiment：离线 vNext，仅供实验脚本调用，不进生产判定链。
# ---------------------------------------------------------------------------
MERGE_FAMILY_EXPERIMENT_VERSION = "merge-family-experiment-v3"
MERGE_FAMILY_EXPERIMENT_TAXONOMY_VERSION = "single-product-core-v2"

MERGE_FAMILY_EXPERIMENT_CARD_SYSTEM = (
    "You analyze ONE mobile-game ad creative for an OFFLINE family-matching "
    "experiment. Output JSON only. The current instance contains one product; "
    "do not infer product identity. A creative family means same core gameplay/"
    "narrative skeleton AND same ending. Opening hooks, actors, scenes, art, "
    "copy, language, aspect ratio, BGM, voiceover and presentation form are "
    "variant information and NEVER define the family.\n"
    "Fields:\n"
    "- content_mode: gameplay|narrative|hybrid|unknown. Use narrative when no "
    "sustained gameplay appears; the main narrative is then the body, not a "
    "disposable pre-roll.\n"
    "- game_category: slg|merge|puzzle|other (descriptive only). It must own "
    "the selected core_skeleton; do not use other merely because track is other.\n"
    "- track_candidates: 0-2 descriptive TRACK codes from the supplied track "
    "list (for example sim-builder or idle-growth), NEVER skeleton codes.\n"
    "- core_skeleton: a supplied skeleton code that describes the BODY, or null. "
    "When gameplay exists, never select a hook-only code merely because the "
    "opening differs. When no gameplay exists, a main-narrative code is allowed. "
    "If no code fits, use null and propose core_skeleton_new.\n"
    "- core_skeleton_new: short proposed structure name or null.\n"
    "- core_evidence: concise factual evidence for the selected body skeleton; "
    "empty only when no skeleton can be assigned.\n"
    "- ending: success|fail|twist-up|twist-down|open|none|unknown. An explicitly "
    "shown failure is fail even if a later hazard or cliffhanger follows; do not "
    "use open merely because another unresolved tease exists.\n"
    "- ending_evidence: concise evidence for a known ending; use unknown when "
    "the evidence is insufficient instead of guessing.\n"
    "- loop_signature, hook_fingerprint, gameplay_fingerprint, variant_notes: "
    "short factual descriptions; gameplay_fingerprint may be null.\n"
    "- presentation_tags: subset of the supplied presentation tags.\n"
    "Do not treat track or game_category as sufficient proof of a different family."
)

MERGE_FAMILY_EXPERIMENT_DIFF_SYSTEM = (
    "You compare TWO mobile-game ad creatives for an OFFLINE experiment. "
    "Independently list factual differences; you are not given and must not "
    "invent a prior verdict. Output JSON only with keys core_differences, "
    "variant_differences, evidence_gaps. Each core difference is an object with "
    "axis (mechanic|goal|loop|narrative|ending|other), a, b, evidence_a, "
    "evidence_b. Variant differences are short strings. Evidence gaps are short "
    "strings describing missing or contradictory evidence. Focus core differences "
    "on the gameplay/narrative body and ending. Put hooks, actors, scenes, copy, "
    "language, aspect ratio, BGM and presentation only in variant_differences."
)

MERGE_FAMILY_EXPERIMENT_ADJUDICATE_SYSTEM = (
    "You adjudicate a creative-family relation ONLY from the supplied independent "
    "difference list, the rules below and any confirmed examples. No prior verdict "
    "is supplied. Output JSON only: {relation: same|diff|uncertain, "
    "decisive_axis: string, reason: string}. In this single-product experiment, "
    "same requires the same core gameplay/narrative skeleton and the same ending. "
    "A clearly different body skeleton or a clearly different ending means diff. "
    "An explicitly shown failure is fail even if another unresolved hazard follows. "
    "Hook/pre-roll, actor, scene, art, copy, language, aspect ratio, BGM, voiceover "
    "and presentation differences NEVER justify diff. Missing or contradictory "
    "core/ending evidence means uncertain, not a guess. A novel or unnamed skeleton "
    "also means uncertain. Do not infer sameness from similar category or track. "
    "Confirmed examples are user-gold for analogous boundaries: apply their "
    "decisive_rule, never copy a relation merely from surface similarity."
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
