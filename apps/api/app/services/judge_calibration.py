"""judge 校准：分类别统计自动判定改判率 + 建议级采纳率，并按需升降级
（LangChain 2026 方法论——judge 当生产代码管，用人工修正持续校准）。

- 改判 = edit_logs 中 auto: 判定之后，同一实体又出现了同字段的人工
  变更（非 auto:）或 split/rename（回退痕迹）
- auto: 记录本身不带 kind，按 field/action 推断归属类别：
  field="dna_id" → dna_assign；action="merge" → merge_pair；
  field="cluster" → cluster（聚类自动关联，被人工移动/拆分 = 改判）；
  其余推断不了的进 "other" 桶（不硬猜）
- 建议级采纳率：对 judge_suggestions 里的建议，看 left_id 实体后续的
  人工 edit_logs——与建议一致 = 采纳，不一致/实体被删 = 驳回，
  无后续动作 = 未决（不计入分母）
- 改判率 > 30%（约 κ < 0.7）→ 对应类别 judge_auto_enabled:<kind>
  降级为 false；总开关 judge_auto_enabled 仍按整体改判率升降，
  生效条件 = 总闸开 AND 类别闸开（judge_auto_allowed）
- 样本量 < MIN_SAMPLE 不动任何开关

由 ``scripts/judge_calibration``（薄壳 CLI）与 ``scripts/judge_candidates``
（跑完后携载校准）调用；收件箱 GET /review/queue 用 judge_stats 做只读展示。
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import EditLog, JudgeSuggestion
from app.repositories.settings import SettingsRepository

OVERRIDE_RATE_LIMIT = 0.30
MIN_SAMPLE = 10

MASTER_KEY = "judge_auto_enabled"
KIND_KEY_TEMPLATE = "judge_auto_enabled:{kind}"

# judge_suggestions.kind 的全部类别（other 桶仅用于 auto 改判归类，不在此列）
JUDGE_KINDS = (
    "dna_assign",
    "merge_pair",
    "verdict",
    "derivation-factor",
    "observation_pair",
)


@dataclass
class KindCalibration:
    """单个建议类别的校准指标。"""

    kind: str
    auto_total: int = 0
    auto_overrides: int = 0
    override_rate: float = 0.0
    sugg_accepted: int = 0
    sugg_rejected: int = 0
    # 无已决建议时为 None（而不是 0，避免"没数据"被误读成"0% 采纳"）
    acceptance_rate: float | None = None


# ----------------------------------------------------------------------
# auto 改判：按类别拆分
# ----------------------------------------------------------------------
def _auto_kind(log: EditLog) -> str:
    """从 edit_logs 记录推断 auto 判定归属的建议类别；推断不了进 "other"。"""
    if log.field == "dna_id":
        return "dna_assign"
    if log.action == "merge":
        return "merge_pair"
    if log.field == "cluster":
        # 聚类自动关联（pipeline._cluster 写入）；被人工移动/拆分 = 改判
        return "cluster"
    return "other"


def _count_later_overrides(db: Session, log: EditLog) -> int:
    return int(
        db.scalar(
            select(func.count(EditLog.id)).where(
                EditLog.entity_id == log.entity_id,
                EditLog.created_at > log.created_at,
                ~EditLog.new_value.like("auto:%"),
                or_(
                    EditLog.field == log.field,
                    EditLog.action.in_(("split", "rename")),
                ),
            )
        )
        or 0
    )


# ----------------------------------------------------------------------
# 建议级采纳率：每种 kind 的"人工后续动作 → 采纳/驳回"映射
#
# - dna_assign（left_id=creative，verdict="D{code} {name}"）：
#   该 creative 后续首条人工 dna_id 变更的 new_value 与建议一致 → 采纳，
#   不一致（含取消归族）→ 驳回；creative 被 merge 进别人（merge 记录的
#   old_value 含 "(left_id)"）→ 驳回。auto: 日志不算人工动作。
# - merge_pair（left_id/right_id=两个 creative，verdict=merge|split）：
#   采纳 = edit_logs 里有这两条 creative 的 merge 记录且方向一致
#   （entity_id 为存续方，old_value 含对方 "(id)"）；任一方被 merge 进
#   第三方 → 驳回。verdict=split 时观察对登记（field="observation_pair"
#   落在任一方）视为采纳——该日志只记双方名字不记 id，按落在任一方
#   近似归属，是启发式。
# - verdict（left_id=derivation，verdict=positive|negative）：
#   该 derivation 后续人工 verdict 变更与建议一致 → 采纳，不一致 → 驳回；
#   derivation 被解链（action="delete"）→ 驳回。
# - derivation-factor（left_id=derivation，verdict=因子或
#   "not-a-derivation"）：后续 factor 变更为建议值 → 采纳，改成别的值
#   → 驳回；delete 记录对 "not-a-derivation" 建议 = 采纳，对因子建议
#   = 驳回（人工选择解链而不是改因子）。
# - observation_pair：处置动作（结案写 split_rulings 按名字、登记观察对
#   不含对方 id）无法可靠归属到具体建议，拿不准——只统计 auto 改判，
#   不算采纳率。
# ----------------------------------------------------------------------
def _later_logs(
    db: Session, suggestion: JudgeSuggestion, *, entity_type: str, entity_id: str
) -> list[EditLog]:
    return list(
        db.scalars(
            select(EditLog)
            .where(
                EditLog.entity_type == entity_type,
                EditLog.entity_id == entity_id,
                EditLog.created_at > suggestion.created_at,
            )
            .order_by(EditLog.created_at)
        ).all()
    )


def _later_merges(db: Session, suggestion: JudgeSuggestion) -> list[EditLog]:
    return list(
        db.scalars(
            select(EditLog)
            .where(
                EditLog.action == "merge",
                EditLog.created_at > suggestion.created_at,
            )
            .order_by(EditLog.created_at)
        ).all()
    )


def _dna_label(value: str) -> str:
    """归一化 dna_id 日志值：auto 写作 "auto: D1 名称（理由）"，人工写作 "D1 名称"。"""
    return value.removeprefix("auto:").split("（")[0].strip()


def _dna_assign_outcome(db: Session, suggestion: JudgeSuggestion) -> str:
    events: list[tuple] = []  # (created_at, outcome)
    for log in _later_logs(db, suggestion, entity_type="creative",
                           entity_id=suggestion.left_id):
        if log.field != "dna_id" or log.new_value.startswith("auto:"):
            continue
        outcome = (
            "accepted" if _dna_label(log.new_value) == suggestion.verdict.strip()
            else "rejected"
        )
        events.append((log.created_at, outcome))
    # creative 被 merge 进别人 = 实体删除 → 驳回
    for log in _later_merges(db, suggestion):
        if f"({suggestion.left_id})" in log.old_value:
            events.append((log.created_at, "rejected"))
    if not events:
        return "pending"
    return min(events)[1]


def _merge_pair_outcome(db: Session, suggestion: JudgeSuggestion) -> str:
    left, right = suggestion.left_id, suggestion.right_id
    events: list[tuple] = []
    for log in _later_merges(db, suggestion):
        if log.entity_id in (left, right):
            other = right if log.entity_id == left else left
            if f"({other})" in log.old_value:
                # 这两条的 merge 记录（方向一致：存续方为 entity_id）
                outcome = "accepted" if suggestion.verdict == "merge" else "rejected"
                events.append((log.created_at, outcome))
        elif f"({left})" in log.old_value or f"({right})" in log.old_value:
            # 任一方被 merge 进第三方 = 实体删除 → 驳回
            events.append((log.created_at, "rejected"))
    if suggestion.verdict in ("split", "observe"):
        # 维持拆分类建议：任一方后续出现观察对登记/结案即视为采纳（启发式，
        # 见模块注释——observation_pair 日志不含对方 id）
        for log in _later_logs(db, suggestion, entity_type="creative",
                               entity_id=left) + _later_logs(
                                   db, suggestion, entity_type="creative",
                                   entity_id=right):
            if log.field == "observation_pair":
                events.append((log.created_at, "accepted"))
    if not events:
        return "pending"
    return min(events)[1]


def _derivation_outcome(db: Session, suggestion: JudgeSuggestion) -> str:
    """verdict 与 derivation-factor 共用：主体都是 derivation 的后续日志。"""
    for log in _later_logs(db, suggestion, entity_type="derivation",
                           entity_id=suggestion.left_id):
        if log.action == "delete":
            if suggestion.kind == "derivation-factor":
                return ("accepted" if suggestion.verdict == "not-a-derivation"
                        else "rejected")
            return "rejected"  # verdict 建议 vs 解链 = 驳回
        if suggestion.kind == "verdict" and log.field == "verdict":
            return ("accepted" if log.new_value == suggestion.verdict
                    else "rejected")
        if suggestion.kind == "derivation-factor" and log.field == "factor":
            return ("accepted" if log.new_value == suggestion.verdict
                    else "rejected")
    return "pending"


def suggestion_outcome(db: Session, suggestion: JudgeSuggestion) -> str:
    """建议的人工处置结果："accepted" | "rejected" | "pending"（未决不计入）。"""
    if suggestion.kind == "dna_assign":
        return _dna_assign_outcome(db, suggestion)
    if suggestion.kind == "merge_pair":
        return _merge_pair_outcome(db, suggestion)
    if suggestion.kind in ("verdict", "derivation-factor"):
        return _derivation_outcome(db, suggestion)
    # observation_pair 等拿不准的类别：不算采纳率
    return "pending"


# ----------------------------------------------------------------------
# 汇总
# ----------------------------------------------------------------------
def calibration_by_kind(db: Session) -> dict[str, KindCalibration]:
    """按建议类别统计 auto 改判率与建议级采纳率（只读，不动任何开关）。"""
    stats = {kind: KindCalibration(kind=kind) for kind in JUDGE_KINDS}

    auto_logs = list(
        db.scalars(
            select(EditLog)
            .where(EditLog.new_value.like("auto:%"))
            .order_by(EditLog.created_at)
        ).all()
    )
    for log in auto_logs:
        kind = _auto_kind(log)
        if kind not in stats:
            stats[kind] = KindCalibration(kind=kind)
        bucket = stats[kind]
        bucket.auto_total += 1
        if _count_later_overrides(db, log):
            bucket.auto_overrides += 1

    for suggestion in db.scalars(select(JudgeSuggestion)).all():
        bucket = stats.get(suggestion.kind)
        if bucket is None:
            continue
        outcome = suggestion_outcome(db, suggestion)
        if outcome == "accepted":
            bucket.sugg_accepted += 1
        elif outcome == "rejected":
            bucket.sugg_rejected += 1

    for bucket in stats.values():
        if bucket.auto_total:
            bucket.override_rate = bucket.auto_overrides / bucket.auto_total
        decided = bucket.sugg_accepted + bucket.sugg_rejected
        if decided:
            bucket.acceptance_rate = bucket.sugg_accepted / decided
    return stats


def judge_stats(db: Session) -> dict[str, dict[str, object]]:
    """收件箱展示用的各类别准确率摘要（只读计算不存储）；无数据的类别不出现。"""
    return {
        kind: {
            "auto_total": bucket.auto_total,
            "override_rate": bucket.override_rate,
            "acceptance_rate": bucket.acceptance_rate,
        }
        for kind, bucket in calibration_by_kind(db).items()
        if bucket.auto_total > 0 or bucket.sugg_accepted + bucket.sugg_rejected > 0
    }


# ----------------------------------------------------------------------
# 开关
# ----------------------------------------------------------------------
def judge_auto_allowed(db: Session, kind: str) -> bool:
    """某类别的自动判定当前是否生效：总闸开 AND 类别闸开。"""
    repo = SettingsRepository(db)
    if (repo.get(MASTER_KEY) or "true") == "false":
        return False
    return (repo.get(KIND_KEY_TEMPLATE.format(kind=kind)) or "true") != "false"


def _set_gate(db: Session, key: str, value: str) -> None:
    repo = SettingsRepository(db)
    repo.set(key, value)


def run_calibration(db: Session) -> tuple[int, int, float]:
    """统计改判率并按需升降级（总闸按整体、类别闸按类别）。

    返回 (auto 总数, 改判数, 整体改判率)。
    """
    stats = calibration_by_kind(db)
    repo = SettingsRepository(db)

    def _print_kind(bucket: KindCalibration) -> None:
        kind = bucket.kind
        decided = bucket.sugg_accepted + bucket.sugg_rejected
        acceptance = (
            f"，建议采纳率 {bucket.acceptance_rate:.1%}"
            f"（{bucket.sugg_accepted}/{decided}）"
            if decided
            else ""
        )
        print(
            f"[{kind}] auto {bucket.auto_total} 条，改判 {bucket.auto_overrides} 条，"
            f"改判率 {bucket.override_rate:.1%}{acceptance}"
        )

    for kind in JUDGE_KINDS:
        _print_kind(stats[kind])
    # 动态类别（cluster / other 等无建议桶）：有 auto 数据才打印
    for kind, bucket in stats.items():
        if kind not in JUDGE_KINDS and bucket.auto_total:
            _print_kind(bucket)

    total = sum(bucket.auto_total for bucket in stats.values())
    overrides = sum(bucket.auto_overrides for bucket in stats.values())
    rate = overrides / total if total else 0.0
    current = repo.get(MASTER_KEY) or "true"
    print(
        f"auto 判定 {total} 条，改判 {overrides} 条，"
        f"改判率 {rate:.1%}（阈值 {OVERRIDE_RATE_LIMIT:.0%}）"
    )

    if total >= MIN_SAMPLE and rate > OVERRIDE_RATE_LIMIT and current != "false":
        _set_gate(db, MASTER_KEY, "false")
        print("⚠️ 改判率超限：judge_auto_enabled 已降级为 false（仅建议模式）")
    elif total >= MIN_SAMPLE and rate <= OVERRIDE_RATE_LIMIT and current == "false":
        _set_gate(db, MASTER_KEY, "true")
        print("✓ 改判率达标：judge_auto_enabled 恢复为 true")
    else:
        print(f"维持现状：judge_auto_enabled={current}（样本量 {total}/{MIN_SAMPLE}）")

    # 类别闸：单类别超限只降该类别，互不影响（含 cluster/other 等动态桶）
    for kind, bucket in stats.items():
        if bucket.auto_total < MIN_SAMPLE:
            continue
        key = KIND_KEY_TEMPLATE.format(kind=kind)
        kind_current = repo.get(key) or "true"
        if bucket.override_rate > OVERRIDE_RATE_LIMIT and kind_current != "false":
            _set_gate(db, key, "false")
            print(f"⚠️ [{kind}] 改判率超限：{key} 已降级为 false（仅建议模式）")
        elif bucket.override_rate <= OVERRIDE_RATE_LIMIT and kind_current == "false":
            _set_gate(db, key, "true")
            print(f"✓ [{kind}] 改判率达标：{key} 恢复为 true")

    return total, overrides, rate
