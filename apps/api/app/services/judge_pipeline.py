"""分析后自动判定（judge pipeline）：DNA 归族 + 合并候选预裁。

从 scripts/judge_candidates 上移的批量 LLM 预裁逻辑（service 层承载，
脚本与上传管线共用）：

- 归族：dna_id IS NULL 的 creative 走 dna_classifier（规则→LLM N=3），
  3/3 一致自动归族（edit_logs 标 auto:），2/3 写建议缓存
- 合并：review.merge_candidate_items 的候选对走 merge_judge（3 票防偏），
  不满足自动门禁的一律只写建议缓存供收件箱一键确认；LLM 3/3 判同一 +
  pHash 帧对齐率 ≥ 0.90（视频物证）+ 无既定裁决 + merge_auto_enabled
  开启（默认开）→ 自动执行合并（edit_logs 标 auto:）
- 失败静默，不阻塞；judge_auto_enabled=false 时只写缓存不自动执行

两个 run_* 都接受 ``creative_id`` 做范围限定（上传管线只复核新素材
涉及的 creative）；不给则全量（批量脚本行为）。
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Creative
from app.repositories.settings import SettingsRepository
from app.services import merge_ops
from app.services import review as review_service
from app.services.dna_classifier import suggest_dna
from app.services.judge_suggestions import delete_suggestion, upsert_suggestion
from app.services.merge_guard import find_db_ruling, find_prior_ruling
from app.services.merge_judge import judge_pair
from app.services.merge_measure import (
    MIN_ALIGNED_FRACTION_FOR_AUTO,
    measure_pair_alignment,
)
from app.services.settings import AIConfig

logger = logging.getLogger(__name__)


def log(db: Session, entity_id: str, field: str, new_value: str, action: str = "update") -> None:
    db.execute(
        text(
            "insert into edit_logs "
            "(id, entity_type, entity_id, action, field, old_value, new_value) "
            "values (:id, 'creative', :eid, :action, :field, '', :val)"
        ),
        {"id": str(uuid.uuid4()), "eid": entity_id, "action": action,
         "field": field, "val": new_value},
    )


def run_dna_assignments(
    db: Session,
    config: AIConfig | None,
    *,
    dry_run: bool,
    auto_enabled: bool,
    creative_id: str | None = None,
) -> None:
    stmt = select(Creative).where(Creative.dna_id.is_(None))
    if creative_id is not None:
        stmt = stmt.where(Creative.id == creative_id)
    creatives = list(db.scalars(stmt))
    for creative in creatives:
        analyses = db.execute(
            text(
                "select a.hook, a.gameplay from analysis_results a "
                "join creative_variants v on v.asset_id = a.asset_id "
                "where v.creative_id = :cid limit 1"
            ),
            {"cid": creative.id},
        ).first()
        if analyses is None:
            continue
        suggestion = suggest_dna(creative, analyses[0] or "", analyses[1] or "", db, config)
        if suggestion is None:
            continue
        upsert_suggestion(
            db, kind="dna_assign", left_id=creative.id, right_id=suggestion.dna.id,
            verdict=f"{suggestion.dna.code} {suggestion.dna.name}",
            votes=suggestion.votes, reason=suggestion.reason,
        )
        if suggestion.votes >= 3 and auto_enabled:
            print(f"auto  D{ suggestion.dna.code} <- {creative.name}")
            if not dry_run:
                db.execute(
                    text("update creatives set dna_id = :d where id = :id"),
                    {"d": suggestion.dna.id, "id": creative.id},
                )
                log(db, creative.id, "dna_id",
                    f"auto: {suggestion.dna.code} {suggestion.dna.name}（{suggestion.reason}）")
        else:
            print(f"sugg  D{suggestion.dna.code} ({suggestion.votes}/3) <- {creative.name}")


def run_merge_judgements(
    db: Session,
    config: AIConfig | None,
    *,
    dry_run: bool,
    auto_enabled: bool,
    settings: Settings | None = None,
    creative_id: str | None = None,
) -> None:
    merge_auto = (
        auto_enabled
        and not dry_run
        and settings is not None
        and (SettingsRepository(db).get("merge_auto_enabled") or "true") != "false"  # 默认开
    )
    candidates = review_service.merge_candidate_items(db)
    for item in candidates:
        if not item.creative_id or not item.related_creative_id:
            continue
        if creative_id is not None and creative_id not in (
            item.creative_id, item.related_creative_id
        ):
            continue  # scoped：只预裁涉及新 creative 的候选对
        if find_prior_ruling(item.creative_name or "", item.related_creative_name or ""):
            continue  # 既定裁决永不自动
        if find_db_ruling(db, item.creative_name or "", item.related_creative_name or ""):
            continue  # 收件箱结案裁决同样永不自动
        analyses = {}
        for cid in (item.creative_id, item.related_creative_id):
            row = db.execute(
                text(
                    "select a.hook, a.conflict, a.gameplay from analysis_results a "
                    "join creative_variants v on v.asset_id = a.asset_id "
                    "where v.creative_id = :cid limit 1"
                ),
                {"cid": cid},
            ).first()
            if row is None:
                break
            analyses[cid] = f"钩子：{row[0]}\n冲突：{row[1]}\n玩法：{row[2]}"
        if len(analyses) != 2:
            continue
        judgement = judge_pair(
            config,
            analysis_a=analyses[item.creative_id],
            analysis_b=analyses[item.related_creative_id],
        )
        if judgement is None:
            continue
        verdict = "merge" if judgement.same_creative else "split"

        # 自动执行：LLM 3/3 判同一 + pHash 对齐率 ≥ 0.90（视频物证）+
        # 开关开 + 无既定裁决（上面已查）+ merge_guard 无 block，
        # 全部满足才合并；任一不满足维持只写建议
        if merge_auto and judgement.same_creative and judgement.votes == 3:
            alignment = measure_pair_alignment(
                db, settings, item.creative_id, item.related_creative_id
            )
            if alignment is not None and alignment >= MIN_ALIGNED_FRACTION_FOR_AUTO:
                try:
                    # 方向：item.related → item.creative（候选对的首个成员保留）
                    merge_ops.merge_creatives(
                        db,
                        settings,
                        item.related_creative_id,
                        item.creative_id,
                        auto=True,
                        commit=False,  # 事务边界交给调用方（见 merge_ops docstring）
                    )
                except merge_ops.MergeBlocked as exc:
                    print(f"blocked {item.title[:50]} :: {exc}")
                else:
                    # 已结案：不写建议，并清掉此前可能残留的同对建议
                    delete_suggestion(
                        db, kind="merge_pair", left_id=item.creative_id,
                        right_id=item.related_creative_id,
                    )
                    delete_suggestion(
                        db, kind="merge_pair", left_id=item.related_creative_id,
                        right_id=item.creative_id,
                    )
                    print(f"auto  merged {item.title[:50]}（对齐 {alignment:.0%}）")
                    continue
            else:
                print(f"measure {alignment!r} < 0.90，只写建议：{item.title[:50]}")

        upsert_suggestion(
            db, kind="merge_pair", left_id=item.creative_id,
            right_id=item.related_creative_id, verdict=verdict,
            votes=judgement.votes, reason=judgement.reason,
        )
        print(
            f"judge {verdict:5s} ({judgement.votes}/3) "
            f"{item.title[:50]} :: {judgement.reason[:40]}"
        )


def run_post_analysis(
    db: Session,
    config: AIConfig | None,
    creative_id: str,
    settings: Settings | None = None,
) -> None:
    """分析成功后的自动判定：归族 + 涉及该 creative 的合并预裁。

    在上传管线的 session 上运行并自行 commit；begin_nested 把失败回滚
    限定在判定工作内（绝不波及管线此前已提交的 completed 状态），
    失败只记日志（Human > AI：判定只是建议/自动级辅助）。
    """
    try:
        with db.begin_nested():
            auto_enabled = (
                SettingsRepository(db).get("judge_auto_enabled") or "true"
            ) != "false"
            run_dna_assignments(
                db, config, dry_run=False, auto_enabled=auto_enabled,
                creative_id=creative_id,
            )
            run_merge_judgements(
                db, config, dry_run=False, auto_enabled=auto_enabled,
                settings=settings, creative_id=creative_id,
            )
            # 漏网补洞：新 creative 的三路召回局部扫描（文本带外的对也覆盖）
            if settings is not None:
                from app.services.missed_merge_scan import scan_missed_merges

                scan_missed_merges(
                    db, settings, config, creative_id=creative_id,
                    emit=lambda _msg: None,
                )
                # 周期性巩固：距上次全扫 ≥7 天或新增 ≥50 条 → 全量扫描+校准
                from app.services.consolidation import maybe_consolidate

                maybe_consolidate(db, settings, config)

                # 市场前缀引导：新用户上传即发现命名约定（只建议不配置）
                from app.services.market_detect import suggest_detected_prefixes

                suggest_detected_prefixes(db)
        db.commit()
    except Exception:  # noqa: BLE001 — 判定失败不拖垮分析管线
        # begin_nested 已把失败回滚限定在判定工作内，这里只记日志；
        # 不再 db.rollback()——共享 session 场景下会误伤调用方既有状态
        logger.exception("post-analysis judge 失败 creative=%s", creative_id)
