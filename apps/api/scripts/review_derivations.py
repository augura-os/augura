"""存量裂变边复核：批量调用 services/derivation_review 的单边复核。

测量复核逻辑在 app/services/derivation_review.py（与 POST /derivations
创建即复核共用同一实现）；本脚本只是批量壳：拉全部 DERIVED_FROM 边
逐条复核，尾部 Neo4j link_derivation 全量重同步 + rebuild_mirror
（同 backfill_derivations，兜底一致性）。

幂等，可随新变体入库重跑。dry-run 只打印测量证据和判定，不落库、
不写建议（LLM 仍会调用——它的输出也是 dry-run 要看的内容）。

Usage:
    python -m scripts.review_derivations --dry-run
    python -m scripts.review_derivations
"""
from __future__ import annotations

import sys
from collections import Counter

from sqlalchemy import text

from app.config import get_settings
from app.database import SessionLocal
from app.services.derivation_review import review_derivation_edge


def main() -> None:
    # Windows GBK 控制台打印 ↔ 等字符会 UnicodeEncodeError——强制 UTF-8 输出
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    dry_run = "--dry-run" in sys.argv

    settings = get_settings()
    db = SessionLocal()
    try:
        derivation_ids = [
            row[0]
            for row in db.execute(
                text("select id from variant_derivations order by created_at")
            ).fetchall()
        ]
        print(f"共 {len(derivation_ids)} 条 DERIVED_FROM 边"
              + ("（DRY-RUN）" if dry_run else ""))
        results: Counter[str] = Counter()
        for derivation_id in derivation_ids:
            result = review_derivation_edge(
                db, settings, derivation_id, dry_run=dry_run, emit=print
            )
            if result == "failed":
                db.rollback()  # 清掉失败边的半拉子状态，后续边继续
            results[result.split(":")[0]] += 1
        summary = ", ".join(f"{k} {v}" for k, v in sorted(results.items()))
        print(("DRY-RUN " if dry_run else "") + f"done: {summary}")
    finally:
        db.close()

    if dry_run:
        return

    # Neo4j sync + mirror rebuild（同 backfill_derivations 尾部）
    from app.repositories.graph_mirror import rebuild_mirror
    from app.services import graph_sync

    db = SessionLocal()
    try:
        repo = graph_sync.get_graph_repository(settings)
        rows = db.execute(
            text("select source_variant_id, target_variant_id, factor "
                 "from variant_derivations")
        ).fetchall()
        for source_id, target_id, factor in rows:
            try:
                repo.link_derivation(source_id, target_id, factor)
            except Exception as exc:  # noqa: BLE001
                print(f"!! neo4j link failed {source_id[:8]}: {exc}")
        rebuild_mirror(db)
        print(f"neo4j synced ({len(rows)} edges) + mirror rebuilt")
    finally:
        db.close()


if __name__ == "__main__":
    main()
