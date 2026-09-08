"""观察对预裁（observation pair pre-adjudication）。

观察对 = 疑似同创意但证据不足、被人工拆开观察的组合（boundary-rules §4.1）。
数据跑一段时间后，LLM 自洽性投票给出处置建议：

- ``merge``：效率同档且钩子等价——数据证实同源，建议合并
- ``split``：效率显著分层——数据证实分化，建议结案维持拆分
- ``observe``：数据仍不足，继续观察

**仅建议级**：不写图、不改状态，人工在收件箱一键确认（Human > AI）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Sequence

from sqlalchemy.orm import Session

from app.models import Creative, Performance
from app.services import ip_pack
from app.services import recommendation as rec
from app.services.llm_judge import vote_json
from app.services.settings import AIConfig

logger = logging.getLogger(__name__)

VERDICTS = ("merge", "split", "observe")

# 任一方消耗低于此值 → 数据不足以裁定（提示词也会强调）
MIN_SPEND_FOR_RULING = 300.0

_SYSTEM = ip_pack.OBSERVATION_JUDGE_SYSTEM_TEMPLATE.format(
    min_spend=f"{MIN_SPEND_FOR_RULING:.0f}"
)


@dataclass
class ObservationJudgement:
    verdict: str  # "merge" | "split" | "observe"
    votes: int  # 自洽性票数（2 或 3；<2 不产出建议）
    reason: str


def _brief(
    db: Session,
    creative: Creative,
    *,
    max_date: date | None,
    all_performances: Sequence[Performance] | None,
) -> str:
    rows = rec.collect_creative_performance(
        db, creative, all_performances=all_performances
    )
    m = rec.aggregate(creative, None, None, rows, max_date=max_date, variant_count=1)
    cpp = f"${m.cpp:,.2f}" if m.cpp is not None else "无付费"
    roas = f"{m.roas * 100:.2f}%" if m.roas is not None else "-"
    return (
        f"名称 {creative.name}；消耗 ${m.spend:,.0f}；付费 {m.payers} 人；"
        f"付费成本 {cpp}；D1 Roas {roas}"
    )


def judge_observation_pair(
    db: Session,
    config: AIConfig | None,
    source: Creative,
    target: Creative,
    *,
    max_date: date | None,
    all_performances: Sequence[Performance] | None = None,
) -> ObservationJudgement | None:
    """LLM 自洽性投票（N=3）；无 API key、票数 <2 或 verdict 非法时返回 None。"""
    if config is None or not config.api_key:
        return None
    user = (
        f"素材 A：{_brief(db, source, max_date=max_date, all_performances=all_performances)}\n"
        f"素材 B：{_brief(db, target, max_date=max_date, all_performances=all_performances)}"
    )
    winner, count, reasons = vote_json(
        config, system=_SYSTEM, user=user, key="verdict", n=3
    )
    if winner not in VERDICTS or count < 2:
        return None
    reason = reasons[0] if reasons else "LLM 自洽性投票"
    return ObservationJudgement(verdict=winner, votes=count, reason=reason)
