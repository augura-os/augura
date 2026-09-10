"""漏合并扫描：三路召回 + 双证据判定 + 传递闭包（安全网补洞）。

现有 merge_candidate_items 只覆盖「同文件名去市场前缀」和「文本边界带
0.20-0.34」两种召回，猪三兄弟式漏网（skinny-pigs ↔ pig-slaughterhouse
文本分 0.10，从未浮出）证明安全网有洞。本扫描器把召回做全——三路并集
（任一命中即召回），两两 creative 对：

1. 文本带内：review 的过滤相似度（名称 0.6 + 代表文本 0.4）∈ [0.20, 0.34)
2. 分析文本：双方 hook+gameplay 拼接的 token Jaccard ≥ 0.25
3. 视觉签名：双方代表视频的帧 pHash 集合交集率（Hamming ≤10 视为同帧，
   交集 / 较小集合）≥ 0.3——共享实拍片段的物证

排除：已有裁决（split_rulings / case-rulings）、观察对、同族（同 DNA）、
已归档 creative。判定沿用 merge_judge 3 票防偏；双证据齐（LLM 3/3 +
pHash 对齐 ≥0.90）且 merge_auto_enabled + judge_auto_enabled 开才自动
合并（merge_ops），否则写收件箱建议（reason 带三路证据）。

幂等：upsert 唯一约束 + 已合并的creative不再成对。挂载点：上传管线
run_post_analysis（新 creative vs 全部，O(N)）、merge_ops 合并成功后
局部重扫（传递闭包）、批量脚本全量重扫。
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Creative, JudgeSuggestion, SplitRuling
from app.repositories.settings import SettingsRepository
from app.services import markets, merge_ops
from app.services import review as review_service
from app.services.judge_suggestions import delete_suggestion, upsert_suggestion
from app.services.merge_guard import find_prior_ruling
from app.services.merge_judge import judge_pair
from app.services.merge_measure import (
    MIN_ALIGNED_FRACTION_FOR_AUTO,
    first_video_asset,
    measure_pair_alignment,
)
from app.services.settings import AIConfig, resolve_ai_config
from app.services.storage import StorageService
from app.services.variant_diff import frame_phash_sequence

logger = logging.getLogger(__name__)

# 召回阈值
TEXT_LOW = review_service.BORDERLINE_LOW  # 0.20
TEXT_HIGH = review_service.BORDERLINE_HIGH  # 0.34
ANALYSIS_JACCARD_MIN = 0.25
VISUAL_OVERLAP_MIN = 0.3
# 帧哈希 Hamming ≤ 此值视为同帧（与 variant_diff 对齐阈值一致）
MAX_HAMMING_SAME_FRAME = 10


@dataclass
class RecallPair:
    """一对召回的候选 creative 及三路证据分（None = 该路未命中/未测）。"""

    left: Creative
    right: Creative
    text_score: float | None = None
    analysis_score: float | None = None
    visual_score: float | None = None

    @property
    def evidence(self) -> str:
        parts = []
        if self.text_score is not None:
            parts.append(f"文本 {self.text_score:.2f}")
        if self.analysis_score is not None:
            parts.append(f"分析 {self.analysis_score:.2f}")
        if self.visual_score is not None:
            parts.append(f"视觉 {self.visual_score:.2f}")
        return " / ".join(parts)


@dataclass
class ScanStats:
    recalled: int = 0
    judged_merge: int = 0
    judged_split: int = 0
    no_ruling: int = 0
    merged: int = 0
    suggested: int = 0
    orphans_cleaned: int = 0
    skipped_pairs: list[str] = field(default_factory=list)


def _analysis_texts(db: Session) -> dict[str, str]:
    """creative_id → 首个分析的 hook+gameplay 拼接文本。"""
    rows = db.execute(
        text(
            "select v.creative_id, a.hook, a.gameplay from analysis_results a "
            "join creative_variants v on v.asset_id = a.asset_id "
            "order by a.created_at"
        )
    ).fetchall()
    texts: dict[str, str] = {}
    for creative_id, hook, gameplay in rows:
        if creative_id not in texts:
            texts[creative_id] = f"{hook or ''} {gameplay or ''}"
    return texts


def _visual_signatures(
    db: Session,
    settings: Settings,
    creative_ids: Sequence[str],
    emit: Callable[[str], None],
) -> dict[str, list[int]]:
    """每个 creative 代表视频的帧 pHash 序列；无视频/失败的不进字典。

    每次扫描现算（不建缓存表）：百级 creative 分钟级跑完，量级到万再缓存。
    """
    storage = StorageService(settings)
    signatures: dict[str, list[int]] = {}
    tmp_dir = Path(tempfile.mkdtemp(prefix="missed-merge-"))
    try:
        for creative_id in creative_ids:
            asset = first_video_asset(db, creative_id)
            if asset is None:
                continue
            storage_key, filename = asset
            local = str(tmp_dir / f"{creative_id}{Path(filename).suffix.lower()}")
            try:
                storage.download_to(storage_key, local)
                signatures[creative_id] = frame_phash_sequence(local)
            except Exception as exc:  # noqa: BLE001 — 单族失败不影响整轮扫描
                logger.warning("视觉签名失败 creative=%s: %s", creative_id, exc)
                emit(f"!! 视觉签名失败：{creative_id[:8]} {exc}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    return signatures


def visual_overlap(source: list[int], target: list[int]) -> float:
    """两个帧哈希集合的交集率：|互相有同帧（Hamming≤10）的元素| / 较小集合。"""
    if not source or not target:
        return 0.0
    smaller, larger = (
        (source, target) if len(source) <= len(target) else (target, source)
    )
    hits = sum(
        1
        for frame in smaller
        if any(
            (frame ^ other).bit_count() <= MAX_HAMMING_SAME_FRAME
            for other in larger
        )
    )
    return hits / len(smaller)


def _excluded_pairs(db: Session, creatives: Sequence[Creative]) -> set[frozenset]:
    """裁决（split_rulings 表）+ 观察对 + 同族 → 永不召回的 creative id 对。"""
    id_by_name = {c.name: c.id for c in creatives}
    excluded: set[frozenset] = set()
    for ruling in db.scalars(select(SplitRuling)).all():
        left, right = id_by_name.get(ruling.name_a), id_by_name.get(ruling.name_b)
        if left and right:
            excluded.add(frozenset((left, right)))
    excluded |= review_service._observation_pair_refs()
    dna_ids = {c.id: c.dna_id for c in creatives if c.dna_id}
    by_dna: dict[str, list[str]] = {}
    for creative_id, dna_id in dna_ids.items():
        by_dna.setdefault(dna_id, []).append(creative_id)
    for members in by_dna.values():
        for i, left in enumerate(members):
            for right in members[i + 1 :]:
                excluded.add(frozenset((left, right)))
    return excluded


def recall_pairs(
    db: Session,
    settings: Settings,
    *,
    creative_id: str | None = None,
    include_visual: bool = True,
    emit: Callable[[str], None] = lambda _msg: None,
) -> list[RecallPair]:
    """三路召回并集。``creative_id`` 给了就只扫它 vs 全部（O(N)，上传/
    合并后的局部重扫）；不给则全量两两。排除已裁决/观察对/同族/已归档。
    """
    stmt = (
        select(Creative)
        .where(Creative.lifecycle_state != "archived")
        .order_by(Creative.name)
    )
    creatives = list(db.scalars(stmt))
    excluded = _excluded_pairs(db, creatives)
    generic_tokens = markets.resolve_generic_tokens(db)
    analysis_texts = _analysis_texts(db)

    def text_score(a: Creative, b: Creative) -> float:
        return 0.6 * review_service._filtered_similarity(
            a.name, b.name, generic_tokens
        ) + 0.4 * review_service._filtered_similarity(
            a.representative_text or a.name,
            b.representative_text or b.name,
            generic_tokens,
        )

    def analysis_score(a: Creative, b: Creative) -> float:
        text_a, text_b = analysis_texts.get(a.id, ""), analysis_texts.get(b.id, "")
        tokens_a = review_service._signal_tokens(text_a, generic_tokens)
        tokens_b = review_service._signal_tokens(text_b, generic_tokens)
        if not tokens_a or not tokens_b:
            return 0.0
        return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)

    # 候选对：scoped 时 (creative_id, 其他全部)，否则全量两两
    if creative_id is not None:
        anchor = next((c for c in creatives if c.id == creative_id), None)
        if anchor is None:
            return []
        pairs = [(anchor, c) for c in creatives if c.id != anchor.id]
    else:
        pairs = [
            (a, b)
            for index, a in enumerate(creatives)
            for b in creatives[index + 1 :]
        ]

    recalled: dict[frozenset, RecallPair] = {}
    for left, right in pairs:
        if frozenset((left.id, right.id)) in excluded:
            continue
        if find_prior_ruling(left.name, right.name) is not None:
            continue  # case-rulings 既定拆分裁决
        pair = RecallPair(left=left, right=right)
        score = text_score(left, right)
        if TEXT_LOW <= score < TEXT_HIGH:
            pair.text_score = score
        a_score = analysis_score(left, right)
        if a_score >= ANALYSIS_JACCARD_MIN:
            pair.analysis_score = a_score
        if pair.text_score is not None or pair.analysis_score is not None:
            recalled[frozenset((left.id, right.id))] = pair

    if include_visual and pairs:
        signatures = _visual_signatures(
            db, settings, [c.id for c in creatives], emit
        )
        for left, right in pairs:
            key = frozenset((left.id, right.id))
            if key in excluded:
                continue
            sig_a = signatures.get(left.id)
            sig_b = signatures.get(right.id)
            if not sig_a or not sig_b:
                continue
            overlap = visual_overlap(sig_a, sig_b)
            if overlap >= VISUAL_OVERLAP_MIN:
                pair = recalled.get(key)
                if pair is None:
                    pair = RecallPair(left=left, right=right)
                    recalled[key] = pair
                pair.visual_score = overlap
    return list(recalled.values())


def cleanup_orphan_merge_suggestions(db: Session) -> int:
    """清掉 left/right 任一 creative 已不存在的 merge_pair 建议（脏数据）。"""
    creative_ids = set(db.scalars(select(Creative.id)).all())
    orphans = 0
    for row in db.scalars(
        select(JudgeSuggestion).where(JudgeSuggestion.kind == "merge_pair")
    ).all():
        if row.left_id not in creative_ids or (
            row.right_id is not None and row.right_id not in creative_ids
        ):
            db.delete(row)
            orphans += 1
    if orphans:
        db.flush()
    return orphans


def scan_missed_merges(
    db: Session,
    settings: Settings,
    config: AIConfig | None,
    *,
    creative_id: str | None = None,
    dry_run: bool = False,
    recall_only: bool = False,
    emit: Callable[[str], None] = print,
) -> ScanStats:
    """扫描主流程：召回 → 判定 → 出件（自动合并或收件箱建议）。

    不写 commit——事务边界归调用方（脚本末尾统一 commit；
    run_post_analysis 的 begin_nested 里由外层提交）。dry_run 只打印；
    recall_only 连 LLM 判定都跳过（纯召回体检）。
    """
    stats = ScanStats()
    stats.orphans_cleaned = 0 if dry_run else cleanup_orphan_merge_suggestions(db)

    pairs = recall_pairs(
        db, settings, creative_id=creative_id,
        include_visual=True, emit=emit,
    )
    stats.recalled = len(pairs)
    auto_merge = (
        not dry_run
        and (SettingsRepository(db).get("judge_auto_enabled") or "true") != "false"
        and (SettingsRepository(db).get("merge_auto_enabled") or "true") != "false"  # 默认开
    )

    analysis_texts = _analysis_texts(db)
    alive = set(db.scalars(select(Creative.id)).all())
    for pair in pairs:
        # 前面的自动合并可能已经吃掉其中一方（传递闭包链式合并）——跳过
        if pair.left.id not in alive or pair.right.id not in alive:
            stats.skipped_pairs.append(f"{pair.left.name} ↔ {pair.right.name}")
            continue
        title = f"{pair.left.name[:36]} ↔ {pair.right.name[:36]}"
        emit(f"recall {title}（{pair.evidence}）")
        if recall_only:
            continue
        judgement = judge_pair(
            config,
            analysis_a=analysis_texts.get(pair.left.id, ""),
            analysis_b=analysis_texts.get(pair.right.id, ""),
        )
        if judgement is None:
            stats.no_ruling += 1
            emit("       判定: 无（LLM 不可用或票数不足）")
            continue
        if not judgement.same_creative:
            stats.judged_split += 1
            emit(f"       判定: split ({judgement.votes}/3) :: {judgement.reason[:40]}")
            if not dry_run:
                upsert_suggestion(
                    db, kind="merge_pair", left_id=pair.left.id,
                    right_id=pair.right.id, verdict="split",
                    votes=judgement.votes,
                    reason=f"{judgement.reason}（{pair.evidence}）",
                )
                stats.suggested += 1
            continue
        stats.judged_merge += 1
        alignment = None
        if judgement.votes == 3:
            alignment = measure_pair_alignment(
                db, settings, pair.left.id, pair.right.id
            )
        if (
            auto_merge
            and judgement.votes == 3
            and alignment is not None
            and alignment >= MIN_ALIGNED_FRACTION_FOR_AUTO
        ):
            try:
                merge_ops.merge_creatives(
                    db, settings, pair.right.id, pair.left.id,
                    auto=True, commit=False,
                )
            except merge_ops.MergeBlocked as exc:
                emit(f"       blocked: {exc}")
            else:
                stats.merged += 1
                alive.discard(pair.right.id)  # source 已被并入 left
                delete_suggestion(
                    db, kind="merge_pair",
                    left_id=pair.left.id, right_id=pair.right.id,
                )
                delete_suggestion(
                    db, kind="merge_pair",
                    left_id=pair.right.id, right_id=pair.left.id,
                )
                emit(f"       判定: 自动合并（对齐 {alignment:.0%}）")
                continue
        if not dry_run:
            upsert_suggestion(
                db, kind="merge_pair", left_id=pair.left.id,
                right_id=pair.right.id, verdict="merge",
                votes=judgement.votes,
                reason=f"{judgement.reason}（{pair.evidence}）",
            )
            stats.suggested += 1
        emit(f"       判定: merge ({judgement.votes}/3) → 建议")
    return stats


def scan_for_creative(db: Session, settings: Settings, creative_id: str) -> None:
    """局部重扫入口（合并后传递闭包 / 上传管线）：只扫该 creative vs 全部。

    自行 resolve AI 配置 + 提交（调用方不感知事务细节）；失败静默。
    """
    config = resolve_ai_config(db, settings)
    stats = scan_missed_merges(
        db, settings, config, creative_id=creative_id, emit=lambda _msg: None
    )
    db.commit()
    logger.info(
        "局部重扫 creative=%s: 召回 %d，合并 %d，建议 %d",
        creative_id, stats.recalled, stats.merged, stats.suggested,
    )
