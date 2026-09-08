"""修正驱动的合并阈值校准（主动学习：边界该从裁决数据学，不该拍脑袋）。

用户的每次合并/拆分裁决都是标注数据：
- 正样本（该合）：edit_logs 里 action="merge" 的记录
  （old_value 是被合并方名，new_value 是存续方名，可能带 auto: 前缀）
- 负样本（该拆）：split_rulings 表 + docs/case-rulings.json（verdict=split）

特征只算**文本分 + 分析分**（视觉分要下载视频逐帧抽哈希，太贵；
且视觉是"同一素材源"的物证，阈值学的是文本/语义边界——视觉判据
保留给双证据自动合并，不参与校准）。被合并方已删除的creative只剩
名字可算文本分（分析分 None）。

校准 = 对"文本/分析两路取 max 的召回分"在 [0.20, 0.50] 网格搜索
（步长 0.01）F1 最优阈值。选网格而非逻辑回归：几十条样本上单调阈值
就是最优可解释模型，逻辑回归的两特征权重在这么小样本上学不稳，
且阈值能直接落到现有 TEXT_CLUSTER_THRESHOLD 配置语义上。

**只建议不自动改**（Human > AI）：建议写 judge_suggestions
（kind="threshold_calibration"），进收件箱人工确认。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import Creative, SplitRuling
from app.services import markets
from app.services import review as review_service
from app.services.clustering import TEXT_CLUSTER_THRESHOLD
from app.services.judge_suggestions import delete_suggestion, upsert_suggestion
from app.services.merge_guard import _rulings
from app.services.missed_merge_scan import _analysis_texts

logger = logging.getLogger(__name__)

CALIBRATION_KIND = "threshold_calibration"
CALIBRATION_SUBJECT = "merge-text-threshold"  # 单主题：收件箱只留最新一条
# 网格搜索区间与步长（覆盖现边界带 0.20-0.34 并留外扩余量）
GRID_LOW, GRID_HIGH, GRID_STEP = 0.20, 0.50, 0.01
MIN_F1_IMPROVEMENT = 0.02  # F1 提升超过 2 个百分点才建议（噪声免疫）
MIN_SAMPLES = 4  # 标注对太少网格搜索无意义


@dataclass
class LabeledPair:
    """一条裁决标注对：两个 creative 名 + 该合（True）/ 该拆（False）。"""

    name_a: str
    name_b: str
    should_merge: bool
    source: str  # "edit_log" | "split_ruling" | "case_ruling"
    text_score: float
    analysis_score: float | None

    @property
    def score(self) -> float:
        """召回分 = 两路取 max（扫描器是并集召回，口径一致）。"""
        return max(self.text_score, self.analysis_score or 0.0)


@dataclass
class ThresholdSuggestion:
    current: float
    suggested: float
    f1_current: float
    f1_suggested: float
    sample_count: int


def _parse_merge_log_name(value: str) -> str:
    """edit_logs 的 merge 记录值 → creative 名（剥 auto: 前缀 / (id) 后缀）。"""
    value = value.removeprefix("auto: ")
    if " [forced:" in value:
        value = value.split(" [forced:", 1)[0]
    return value.rsplit(" (", 1)[0].strip()


def extract_labeled_pairs(db: Session) -> list[LabeledPair]:
    """从 edit_logs（merge）+ split_rulings + case-rulings 提取标注对。

    同一对出现多次时先去重（后来的裁决覆盖先前的）；merge 与 split
    冲突时 split 优先（拆是更强的人工信号，破坏性操作更谨慎）。
    """
    generic_tokens = markets.resolve_generic_tokens(db)
    analysis_texts = _analysis_texts(db)
    creatives = {c.name: c.id for c in db.scalars(select(Creative)).all()}

    def analysis_score(name_a: str, name_b: str) -> float | None:
        id_a, id_b = creatives.get(name_a), creatives.get(name_b)
        if id_a is None or id_b is None:
            return None  # 一方已被合并/删除，只剩名字
        text_a = analysis_texts.get(id_a, "")
        text_b = analysis_texts.get(id_b, "")
        tokens_a = review_service._signal_tokens(text_a, generic_tokens)
        tokens_b = review_service._signal_tokens(text_b, generic_tokens)
        if not tokens_a or not tokens_b:
            return None
        return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)

    # 无序对去重；split 覆盖 merge
    labels: dict[frozenset, tuple[str, str, bool, str]] = {}
    for name_a, name_b, should_merge, source in _raw_labeled_names(db):
        key = frozenset((name_a, name_b))
        if key in labels and labels[key][2] is False:
            continue  # 已有 split 标注，不被 merge 覆盖
        labels[key] = (name_a, name_b, should_merge, source)

    return [
        LabeledPair(
            name_a=name_a,
            name_b=name_b,
            should_merge=should_merge,
            source=source,
            text_score=review_service._filtered_similarity(
                name_a, name_b, generic_tokens
            ),
            analysis_score=analysis_score(name_a, name_b),
        )
        for name_a, name_b, should_merge, source in labels.values()
    ]


def _raw_labeled_names(db: Session) -> list[tuple[str, str, bool, str]]:
    """(name_a, name_b, should_merge, source) 原始记录（未去重）。"""
    rows: list[tuple[str, str, bool, str]] = []
    for old_value, new_value in db.execute(
        text(
            "select old_value, new_value from edit_logs "
            "where entity_type = 'creative' and action = 'merge'"
        )
    ).fetchall():
        rows.append(
            (
                _parse_merge_log_name(old_value),
                _parse_merge_log_name(new_value),
                True,
                "edit_log",
            )
        )
    for ruling in db.scalars(select(SplitRuling)).all():
        rows.append((ruling.name_a, ruling.name_b, False, "split_ruling"))
    for ruling in _rulings():
        if ruling.get("verdict") == "split":
            pair = ruling.get("pair") or []
            if len(pair) == 2:
                rows.append((str(pair[0]), str(pair[1]), False, "case_ruling"))
    return rows


def _f1(pairs: list[LabeledPair], threshold: float) -> float:
    tp = fp = fn = 0
    for pair in pairs:
        predicted = pair.score >= threshold
        if predicted and pair.should_merge:
            tp += 1
        elif predicted:
            fp += 1
        elif pair.should_merge:
            fn += 1
    if tp == 0:
        return 0.0
    precision = tp / (tp + fp)
    recall = tp / (tp + fn)
    return 2 * precision * recall / (precision + recall)


def suggest_threshold(
    db: Session, *, current: float = TEXT_CLUSTER_THRESHOLD
) -> ThresholdSuggestion | None:
    """网格搜索 F1 最优阈值；比当前好 >2% 才产出建议并写收件箱缓存。

    无建议时清掉旧校准建议（数据变了旧建议可能已不适用）。
    """
    pairs = extract_labeled_pairs(db)
    merge_count = sum(1 for p in pairs if p.should_merge)
    split_count = len(pairs) - merge_count
    suggestion: ThresholdSuggestion | None = None
    if len(pairs) >= MIN_SAMPLES and merge_count > 0 and split_count > 0:
        steps = round((GRID_HIGH - GRID_LOW) / GRID_STEP) + 1
        grid = [GRID_LOW + i * GRID_STEP for i in range(steps)]
        best_t = max(grid, key=lambda t: (_f1(pairs, t), -abs(t - current)))
        f1_best, f1_current = _f1(pairs, best_t), _f1(pairs, current)
        if (
            round(best_t, 2) != round(current, 2)
            and f1_best - f1_current > MIN_F1_IMPROVEMENT
        ):
            suggestion = ThresholdSuggestion(
                current=current,
                suggested=round(best_t, 2),
                f1_current=round(f1_current, 3),
                f1_suggested=round(f1_best, 3),
                sample_count=len(pairs),
            )

    if suggestion is None:
        delete_suggestion(
            db, kind=CALIBRATION_KIND, left_id=CALIBRATION_SUBJECT, right_id=None
        )
        return None
    upsert_suggestion(
        db,
        kind=CALIBRATION_KIND,
        left_id=CALIBRATION_SUBJECT,
        right_id=None,
        verdict=f"{suggestion.suggested:.2f}",
        votes=0,  # 非 LLM 投票，票数恒 0（样本数在 reason 里）
        reason=(
            f"根据 {suggestion.sample_count} 条裁决（{merge_count} 合 / "
            f"{split_count} 拆），建议合并阈值 {suggestion.current:.2f}→"
            f"{suggestion.suggested:.2f}，F1 从 {suggestion.f1_current:.2f} "
            f"升到 {suggestion.f1_suggested:.2f}（Settings 页手动改生效）"
        ),
    )
    return suggestion
