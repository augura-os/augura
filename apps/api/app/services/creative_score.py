"""Creative Score：0-100 四要素加权评分（计算即得，不落库）。

输入是 recommendation.CreativeMetrics（aggregate 已提供全部数据），
纯函数、可测。四要素各自归一化到 0-100 后按配置权重加权：

- 效果（默认 40%）：CPP 越低于红线越高分（≤efficient→100，≥pause_line
  →0，线性插值；无付费数据→50 中性分）；ROAS 达绿线辅助加分
- 新鲜度（25%）：days_idle 从 0 到 30 天线性衰减到 0；无数据→50
- 演化力（20%）：裂变有效率 positive/judged ×100；无裂变→50；
  variant_count > 1 加分
- 置信度（15%）：AI 分析 confidence 均值 ×100；无分析→30

分数管可见性（lifecycle），与推荐引擎的 KEEP/ITERATE/PAUSE/ARCHIVE
动作建议互补，互不影响。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.recommendation import CreativeMetrics
from app.services.settings import ScoreConfig

# 新鲜度衰减窗口：days_idle 从 0 到该天数线性降到 0
FRESHNESS_DECAY_DAYS = 30
# 无数据要素的中性分（不知道好坏，不奖不罚）
NO_DATA_SCORE = 50.0
# 无 AI 分析时的置信度分（低于中性分——没分析过就是更没底）
NO_ANALYSIS_SCORE = 30.0
# 多变体加分（有裂变尝试 = 演化活性信号）
MULTI_VARIANT_BONUS = 10.0
# 效果分里 CPP 与 ROAS 的内部权重
_CPP_SHARE = 0.7


@dataclass
class ScoreBreakdown:
    total: float
    performance: float
    freshness: float
    evolution: float
    confidence: float


def performance_score(
    cpp: float | None, roas: float | None, config: ScoreConfig
) -> float:
    """CPP 主判（≤efficient→100，≥pause_line→0，线性插值），ROAS 辅助。"""
    if cpp is None or cpp <= 0:
        cpp_score = NO_DATA_SCORE
    elif cpp <= config.cpp_efficient:
        cpp_score = 100.0
    elif cpp >= config.cpp_pause_line:
        cpp_score = 0.0
    else:
        span = config.cpp_pause_line - config.cpp_efficient
        cpp_score = 100.0 * (config.cpp_pause_line - cpp) / span
    if roas is None:
        return cpp_score
    if roas >= config.roas_green_line:
        roas_score = 100.0
    elif roas <= 0:
        roas_score = 0.0
    else:
        roas_score = 100.0 * roas / config.roas_green_line
    return _CPP_SHARE * cpp_score + (1 - _CPP_SHARE) * roas_score


def freshness_score(days_idle: int | None) -> float:
    """days_idle 从 0 到 FRESHNESS_DECAY_DAYS 线性衰减到 0；无数据给中性分。"""
    if days_idle is None:
        return NO_DATA_SCORE
    return max(0.0, 100.0 * (1 - days_idle / FRESHNESS_DECAY_DAYS))


def evolution_score(
    derivation_count: int,
    judged_count: int,
    positive_count: int,
    variant_count: int,
) -> float:
    """裂变有效率为主；未判定的裂变按中性分；多变体加分。"""
    if derivation_count == 0 or judged_count == 0:
        base = NO_DATA_SCORE
    else:
        base = 100.0 * positive_count / judged_count
    if variant_count > 1:
        base += MULTI_VARIANT_BONUS
    return min(100.0, base)


def confidence_score(confidence_mean: float | None) -> float:
    """AI 分析置信度均值 ×100；无分析给 30（低于中性分）。"""
    if confidence_mean is None:
        return NO_ANALYSIS_SCORE
    return max(0.0, min(100.0, confidence_mean * 100.0))


def score_creative(
    metrics: CreativeMetrics,
    confidence_mean: float | None,
    config: ScoreConfig,
) -> ScoreBreakdown:
    """四要素归一化 + 加权（权重按总和归一，配置不成 100 也不溢出）。"""
    performance = performance_score(metrics.cpp, metrics.roas, config)
    freshness = freshness_score(metrics.days_idle)
    evolution = evolution_score(
        metrics.derivation_count,
        metrics.judged_count,
        metrics.positive_count,
        metrics.variant_count,
    )
    confidence = confidence_score(confidence_mean)
    weights = (
        (config.weight_performance, performance),
        (config.weight_freshness, freshness),
        (config.weight_evolution, evolution),
        (config.weight_confidence, confidence),
    )
    total_weight = sum(weight for weight, _ in weights) or 1.0
    total = sum(weight * value for weight, value in weights) / total_weight
    return ScoreBreakdown(
        total=round(total, 1),
        performance=round(performance, 1),
        freshness=round(freshness, 1),
        evolution=round(evolution, 1),
        confidence=round(confidence, 1),
    )
