"""judge 校准 CLI 薄壳：实现已上移 ``app.services.judge_calibration``
（app 不能依赖 scripts；run_calibration 从此处 re-export 保持兼容）。
print 只留在本层——服务内的 run_calibration 返回 CalibrationResult。

Usage:
    python -m scripts.judge_calibration
"""
from __future__ import annotations

import sys

from app.repositories.settings import SettingsRepository
from app.services.judge_calibration import (
    JUDGE_KINDS,
    MASTER_KEY,
    MIN_SAMPLE,
    OVERRIDE_RATE_LIMIT,
    CalibrationResult,
    KindCalibration,
    run_calibration,
)


def _print_kind(bucket: KindCalibration) -> None:
    decided = bucket.sugg_accepted + bucket.sugg_rejected
    acceptance = (
        f"，建议采纳率 {bucket.acceptance_rate:.1%}"
        f"（{bucket.sugg_accepted}/{decided}）"
        if decided
        else ""
    )
    print(
        f"[{bucket.kind}] auto {bucket.auto_total} 条，"
        f"改判 {bucket.auto_overrides} 条，"
        f"改判率 {bucket.override_rate:.1%}{acceptance}"
    )


def print_report(result: CalibrationResult, *, master_gate: str) -> None:
    """CLI 输出：各类别摘要 + 整体改判率 + 本次刹车动作/维持现状。"""
    for kind in JUDGE_KINDS:
        _print_kind(result.by_kind[kind])
    # 动态类别（cluster / other 等无建议桶）：有 auto 数据才打印
    for kind, bucket in result.by_kind.items():
        if kind not in JUDGE_KINDS and bucket.auto_total:
            _print_kind(bucket)
    print(
        f"auto 判定 {result.auto_total} 条，改判 {result.overrides} 条，"
        f"改判率 {result.override_rate:.1%}（阈值 {OVERRIDE_RATE_LIMIT:.0%}）"
    )
    for event in result.events:
        if event.action == "downgraded":
            print(f"⚠️ 改判率超限：{event.gate_key} 已降级为 false（仅建议模式）")
        else:
            print(f"✓ 改判率达标：{event.gate_key} 恢复为 true")
    if not any(event.kind == "master" for event in result.events):
        print(
            f"维持现状：judge_auto_enabled={master_gate}"
            f"（样本量 {result.auto_total}/{MIN_SAMPLE}）"
        )


def main() -> None:
    # Windows GBK 控制台打印 ⚠️/✓ 会 UnicodeEncodeError——强制 UTF-8 输出
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        result = run_calibration(db)
        master_gate = SettingsRepository(db).get(MASTER_KEY) or "true"
        print_report(result, master_gate=master_gate)
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    main()
