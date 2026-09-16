"""裂变因子归因仲裁（derivation-factor pre-adjudication）。

测量层（services/variant_diff）定不了案的裂变边——中段大面积发散的
换皮类改动、证据冲突——由 LLM 自洽性投票（N=3）在双方已有 AI 分析
（hook/conflict/gameplay/tags）+ 测量证据文本上归因：

- 合法 factor：models/derivation.FACTORS 的换皮词表（unknown 除外）
- ``not-a-derivation``：内容与测量都指向两条素材并非同源，疑似误链

**仅建议级**：不写库、不改边（Human > AI），建议进 judge_suggestions
收件箱人工确认。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.models.derivation import FACTORS
from app.services.llm_judge import vote_json
from app.services.settings import AIConfig

logger = logging.getLogger(__name__)

# unknown 不是归因结论，只是"还没定"；not-a-derivation 是测量层误链候选
FACTOR_CHOICES = tuple(f for f in FACTORS if f != "unknown") + ("not-a-derivation",)

_SYSTEM = (
    "你是手游广告买量分析师。一条素材裂变出了新版本（source 原版 → "
    "target 变体），现在给你双方的 AI 内容分析和视频测量证据，判断这次"
    "裂变的改动因子。只输出 JSON："
    "{\"factor\": \"<因子>\", \"reason\": \"一句话理由\"}。\n"
    "可选因子：\n"
    "- intro-sticker：前贴/片头改动\n"
    "- brand-endcard：品牌尾帧/片尾改动\n"
    "- aspect-ratio：画幅/分辨率改动\n"
    "- language-market：语言/市场本地化\n"
    "- voiceover-copy：配音/口播文案改动\n"
    "- character-reskin：角色/美术换皮\n"
    "- reward-reskin：奖励/数值表现换皮\n"
    "- live-action-vs-animation：真人与动画表现形式互换\n"
    "- remake：同一创意概念/玩法，但画面整体重拍或重剪（无共同连续片段）\n"
    "- not-a-derivation：两条素材并非同源，疑似误链\n"
    "判定规则：\n"
    "- 测量证据（分辨率/对齐比例/未匹配头尾段）是事实，优先于内容分析的印象\n"
    "- 测量显示中段大面积发散时，从内容分析判断换皮类型\n"
    "- 对齐比例低但核心玩法/创意概念同源（同一创意线重新拍摄或重剪，"
    "无共同连续片段）→ remake，不要判 not-a-derivation\n"
    "- 对齐比例很低且 hook/玩法完全不同 → not-a-derivation\n"
    "没有把握时输出 not-a-derivation 让人工复核，不要硬猜换皮因子。"
)


@dataclass
class DerivationJudgement:
    factor: str  # FACTOR_CHOICES 之一
    votes: int  # 自洽性票数（2 或 3；<2 不产出建议）
    reason: str


def judge_factor(
    config: AIConfig | None,
    *,
    source_name: str,
    target_name: str,
    current_factor: str,
    evidence: str,
    source_analysis: str,
    target_analysis: str,
) -> DerivationJudgement | None:
    """LLM 自洽性投票（N=3）；无 API key、票数 <2 或 factor 非法时返回 None。"""
    if config is None or not config.api_key:
        return None
    user = (
        f"source（原版）：{source_name}\n{source_analysis or '（无 AI 分析）'}\n"
        f"target（变体）：{target_name}\n{target_analysis or '（无 AI 分析）'}\n"
        f"当前因子（文件名猜测，可能误判）：{current_factor}\n"
        f"测量证据：{evidence}"
    )
    winner, count, reasons = vote_json(
        config, system=_SYSTEM, user=user, key="factor", n=3
    )
    if winner not in FACTOR_CHOICES or count < 2:
        return None
    reason = reasons[0] if reasons else "LLM 自洽性投票"
    return DerivationJudgement(factor=winner, votes=count, reason=reason)
