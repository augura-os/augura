"""Review queue DTOs (GET /review/queue)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

ReviewKind = Literal[
    "low_confidence",
    "dna_unassigned",
    "merge_candidate",
    "observation_pair",
    "pending_verdict",
    "derivation_review",
    "archive_suggestion",
    "market_conflict",
    "threshold_calibration",
    "market_detect",
    "family_bootstrap",
    "auto_brake",
    "rule_keyword",
]


class FamilyMember(BaseModel):
    id: str
    name: str


class FamilyProposal(BaseModel):
    """智能建族提案负载（judge_suggestions kind="family_bootstrap" 的 reason JSON）。"""

    suggestion_id: str
    name: str
    core_mechanic: str = ""
    hook_prototype: str = ""
    narrative_structure: str = ""
    keywords: list[str] = Field(default_factory=list)
    members: list[FamilyMember] = Field(default_factory=list)
    # 挂接提案：并入这个已确认家族（不建族）；None = 新建家族提案
    existing_dna_code: str | None = None


class RuleKeywordProposal(BaseModel):
    """规则词建议负载（judge_suggestions kind="rule_keyword" 的 reason JSON）。"""

    suggestion_id: str
    target: str  # "mechanic" | "hook" | "generic"
    word: str
    score: float = 0.0
    same_pair_rate: float = 0.0
    cross_pair_rate: float = 0.0
    support: int = 0
    learned_at: str = ""


class ReviewItem(BaseModel):
    kind: ReviewKind
    title: str
    reason: str
    creative_id: str | None = None
    creative_name: str | None = None
    asset_id: str | None = None
    related_creative_id: str | None = None
    related_creative_name: str | None = None
    derivation_id: str | None = None
    # 短名展示（裂变待判定）：市场标签 + 文件名尾部区分段
    source_label: str | None = None
    target_label: str | None = None
    factor: str | None = None
    # LLM 预裁建议（judge_suggestions 缓存；自洽性采样票数与理由）
    suggestion: str | None = None
    suggestion_votes: int | None = None
    suggestion_reason: str | None = None
    suggested_dna_id: str | None = None
    # 智能建族提案（kind="family_bootstrap" 时携带）
    family: FamilyProposal | None = None
    # 规则词建议（kind="rule_keyword" 时携带）
    rule_keyword: RuleKeywordProposal | None = None


class JudgeKindStats(BaseModel):
    """单个建议类别的准确率摘要（services/judge_calibration 只读计算）。"""

    auto_total: int = 0
    override_rate: float = 0.0
    # 无已决建议时为 null（没数据 ≠ 0% 采纳）
    acceptance_rate: float | None = None


class FamilyBootstrapHint(BaseModel):
    """稳态增量扩族提示：散点攒够一批且无未处理提案时主动催一次。"""

    # 未归族（dna_id IS NULL）且有分析结果的 creative 数
    unassigned: int = 0
    # 未处理的 family_bootstrap 提案数（>0 时卡片已在收件箱，不再催）
    pending_proposals: int = 0
    suggest: bool = False


class InterventionWeek(BaseModel):
    """单周人工介入密度（services/intervention）；分母为 0 时 density 为 null。"""

    week_start: date
    human_rulings: int = 0
    new_creatives: int = 0
    density: float | None = None


class InterventionDensity(BaseModel):
    """人工介入密度：近 12 周序列（旧→新）+ 本周当前值（半RSI 验收指标）。"""

    weeks: list[InterventionWeek] = Field(default_factory=list)
    current: InterventionWeek | None = None


class HubSkew(BaseModel):
    """hub 偏斜监控（embedding 设计 §3.5）：自动 attach 次数按族的分布。

    top_share（最大族占比）突然变大 = 均值代表向量的 hub 引力在作祟。
    无自动归入记录时全零/null。
    """

    attach_total: int = 0
    creatives_with_attaches: int = 0
    top_creative_name: str | None = None
    top_count: int = 0
    top_share: float = 0.0


class ReviewQueue(BaseModel):
    generated_at: datetime
    low_confidence: list[ReviewItem] = Field(default_factory=list)
    dna_unassigned: list[ReviewItem] = Field(default_factory=list)
    merge_candidates: list[ReviewItem] = Field(default_factory=list)
    observation_pairs: list[ReviewItem] = Field(default_factory=list)
    pending_verdicts: list[ReviewItem] = Field(default_factory=list)
    derivation_reviews: list[ReviewItem] = Field(default_factory=list)
    # 建议归档（score 低于阈值且长期无消耗；人工确认才置 archived）
    archive_suggestions: list[ReviewItem] = Field(default_factory=list)
    # 市场存疑（文件名前缀 × 分析标签冲突；仅提示人工核对，无操作）
    market_conflicts: list[ReviewItem] = Field(default_factory=list)
    # 阈值校准建议（修正驱动；人工在 Settings 改阈值才生效）
    threshold_calibrations: list[ReviewItem] = Field(default_factory=list)
    # 检测到的未配置市场前缀（引导新用户配置，人工确认）
    market_detects: list[ReviewItem] = Field(default_factory=list)
    # 智能建族提案（LLM 批量划分，人工逐族确认/跳过）
    family_bootstraps: list[ReviewItem] = Field(default_factory=list)
    # 刹车通知（judge 改判率超限自动降级/回落恢复，纯告知）
    auto_brakes: list[ReviewItem] = Field(default_factory=list)
    # 规则词建议（人工改判挖出的候选词，确认才落 settings 词表）
    rule_keywords: list[ReviewItem] = Field(default_factory=list)
    # 稳态增量扩族提示（散点攒够一批时建议运行智能建族）
    family_bootstrap_hint: FamilyBootstrapHint = Field(
        default_factory=FamilyBootstrapHint
    )
    # 各建议类别的改判率/采纳率摘要（judge 校准，供收件箱顶部展示）
    judge_stats: dict[str, JudgeKindStats] = Field(default_factory=dict)
    # 人工介入密度（每周人工裁决数 ÷ 新素材数，收件箱顶部曲线）
    intervention_density: InterventionDensity = Field(
        default_factory=InterventionDensity
    )
    # hub 偏斜监控（自动 attach 次数按族的分布，收件箱顶部展示）
    hub_skew: HubSkew = Field(default_factory=HubSkew)