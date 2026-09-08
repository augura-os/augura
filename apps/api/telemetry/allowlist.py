"""Field allowlist — the only data that may ever leave this instance.

Every telemetry event is validated against this table before enqueueing;
fields not listed are dropped silently. This file is intentionally small
and readable — it is the audit surface referenced by PRIVACY.md §1.1.
"""
from __future__ import annotations

ALLOWED_EVENTS: dict[str, list[str]] = {
    "session_start": ["app_version", "os_family", "genre", "timestamp_bucket"],
    "feature_click": ["feature_name", "timestamp_bucket"],
    "error": ["error_code", "stack_signature", "timestamp_bucket"],
    # ★ 监督修正对：AI 判定 → 人工修正（模型训练核心资产）；
    # genre/dna_code/market 是上下文维度（不含身份），供家族级误判率分析
    "correction": [
        "entity_type",
        "field",
        "old_value",
        "new_value",
        "genre",
        "dna_code",
        "market",
        "timestamp_bucket",
    ],
    # ★ 品类指标区间：只收聚合桶（bucket + count），单行原始值永不出本机
    # dna_code/dna_name 是方法论家族标识（不含素材身份）——code 是实例内编号，
    # 跨实例对齐靠 name（v0.12.3）；market 是市场标签（三维分桶：市场×品类×DNA）
    "aggregate_metric": [
        "genre", "market", "dna_code", "dna_name", "metric", "bucket", "count",
        "timestamp_bucket",
    ],
    # ★ 素材生命周期分布（衰退趋势）：同样只收聚合桶
    "creative_lifecycle": [
        "genre",
        "market",
        "dna_code",
        "dna_name",
        "lifetime_days_bucket",
        "count",
        "timestamp_bucket",
    ],
}

# 分层（v0.12.2 评审反馈）：运行保障（必需，不设开关）vs 共建计划（可关闭）。
# feature_click 是产品分析不是运行必需——归共建层（评审时的修正）。
CORE_EVENTS = {"session_start", "error"}
PROGRAM_EVENTS = {"correction", "aggregate_metric", "creative_lifecycle", "feature_click"}

# 永不收集（文档级承诺，代码层面不读取）：
# - 素材文件本体 / 本地文件路径与文件名
# - 投放明细行（spend / ROAS / CPI 原始值）
# - 用户身份信息 / 设备唯一标识 / 精确时间戳


def validate(event_type: str, fields: dict[str, object]) -> dict[str, object] | None:
    """Return the allowlisted subset of ``fields``, or None for unknown events."""
    allowed = ALLOWED_EVENTS.get(event_type)
    if allowed is None:
        return None
    return {key: fields[key] for key in allowed if key in fields}
