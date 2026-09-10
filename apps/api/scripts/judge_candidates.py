"""Batch LLM pre-adjudication: DNA 归族 + 合并候选预裁（自动级执行）。

归族/合并预裁的实现已上移到 app/services/judge_pipeline（上传管线复用
同一实现）；本脚本保留批量入口 + 依赖投放数据的观察对/裂变判定预裁。

- 归族：dna_id IS NULL 的 creative 走 dna_classifier（规则→LLM N=3），
  3/3 一致自动归族（edit_logs 标 auto:），2/3 写建议缓存
- 合并：review.merge_candidate_items 的候选对走 merge_judge（3 票防偏）+
  pHash 帧对齐测量物证，全部一致"同一"且对齐率 ≥ 0.90 且无既定裁决、
  merge_auto_enabled 开启（默认开）→ 自动合并；其余写建议缓存供收件箱一键确认
- 失败静默，不阻塞；judge_auto_enabled=false 时只写缓存不自动执行

Usage:
    python -m scripts.judge_candidates --dry-run
    python -m scripts.judge_candidates
"""
from __future__ import annotations

import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.models import Creative
from app.services import review as review_service
from app.services.judge_calibration import judge_auto_allowed, run_calibration
from app.services.judge_pipeline import run_dna_assignments, run_merge_judgements
from app.services.judge_suggestions import upsert_suggestion
from app.services.settings import resolve_ai_config


def run_observation_judgements(db: Session, config, *, dry_run: bool) -> None:
    """观察对预裁：给未结案观察对写 LLM 处置建议（仅建议级，不自动结案）。"""
    from app.models import Performance
    from app.services.observation_judge import judge_observation_pair

    max_date = db.scalar(select(Performance.date).order_by(Performance.date.desc()).limit(1))
    all_performances = list(db.scalars(select(Performance)).all())
    pairs = review_service.observation_pair_items(
        db, max_date=max_date, all_performances=all_performances
    )
    for item in pairs:
        if not item.creative_id or not item.related_creative_id:
            continue
        source = db.get(Creative, item.creative_id)
        target = db.get(Creative, item.related_creative_id)
        if source is None or target is None:
            continue
        judgement = judge_observation_pair(
            db, config, source, target,
            max_date=max_date, all_performances=all_performances,
        )
        if judgement is None:
            continue
        upsert_suggestion(
            db, kind="observation_pair", left_id=source.id, right_id=target.id,
            verdict=judgement.verdict, votes=judgement.votes, reason=judgement.reason,
        )
        print(
            f"obs   {judgement.verdict:7s} ({judgement.votes}/3) "
            f"{item.title[:50]} :: {judgement.reason[:40]}"
        )


def run_verdict_judgements(db: Session, config, *, dry_run: bool) -> None:
    """裂变判定预裁：给 pending 裂变边写 LLM 有效/无效建议（仅建议级）。"""
    from app.models import CreativeVariant, Performance, VariantDerivation
    from app.services.evolution import variant_brief
    from app.services.market_stats import market_baselines
    from app.services.markets import market_tag, resolve_market_prefixes
    from app.services.verdict_judge import judge_verdict

    all_performances = list(db.scalars(select(Performance)).all())
    # 跨市场（language-market）边的市场内相对口径基准（样本不足的市场
    # 由 judge_verdict 自动退回 source-vs-target 旧规则）
    prefixes = resolve_market_prefixes(db)
    baselines = market_baselines(db, all_performances=all_performances)
    stmt = (
        select(VariantDerivation, CreativeVariant, Creative)
        .join(CreativeVariant, CreativeVariant.id == VariantDerivation.source_variant_id)
        .join(Creative, Creative.id == CreativeVariant.creative_id)
        .where(VariantDerivation.verdict == "pending")
    )
    variants = {v.id: v for v in db.scalars(select(CreativeVariant)).all()}
    for derivation, _sv, creative in db.execute(stmt).all():
        source = variants.get(derivation.source_variant_id)
        target = variants.get(derivation.target_variant_id)
        if source is None or target is None:
            continue
        source_brief = variant_brief(db, source, all_performances=all_performances)
        target_brief = variant_brief(db, target, all_performances=all_performances)
        target_market, _rest = market_tag(target_brief.filename, prefixes)
        judgement = judge_verdict(
            config,
            creative_name=creative.name,
            factor=derivation.factor,
            source=source_brief,
            target=target_brief,
            market_baseline=baselines.get(target_market),
        )
        if judgement is None:
            continue
        upsert_suggestion(
            db, kind="verdict", left_id=derivation.id, right_id=None,
            verdict=judgement.verdict, votes=judgement.votes, reason=judgement.reason,
        )
        print(
            f"verd  {judgement.verdict:12s} ({judgement.votes}/3) "
            f"{creative.name[:36]} [{derivation.factor}] :: {judgement.reason[:36]}"
        )


def main() -> None:
    # Windows GBK 控制台打印 ↔ 等字符会 UnicodeEncodeError——强制 UTF-8 输出
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    dry_run = "--dry-run" in sys.argv
    db = SessionLocal()
    try:
        settings = get_settings()
        config = resolve_ai_config(db, settings)
        # 总闸 AND 类别闸（judge_calibration 按类别单独降级）
        run_dna_assignments(db, config, dry_run=dry_run,
                            auto_enabled=judge_auto_allowed(db, "dna_assign"))
        run_merge_judgements(db, config, dry_run=dry_run,
                             auto_enabled=judge_auto_allowed(db, "merge_pair"),
                             settings=settings)
        run_observation_judgements(db, config, dry_run=dry_run)
        run_verdict_judgements(db, config, dry_run=dry_run)
        if not dry_run:
            db.commit()
            print("committed")
            # 校准看门狗随行：自动判定改判率超限自动降级（失败不阻塞主流程）
            try:
                run_calibration(db)
            except Exception as exc:  # noqa: BLE001
                print(f"calibration skipped: {exc}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
