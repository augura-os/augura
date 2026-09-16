"""漏合并扫描批量入口（services/missed_merge_scan 的全量壳）。

全量两两召回（文本带 / 分析文本 / 视觉签名三路并集）→ merge_judge 判定
→ 双证据齐且开关开才自动合并，否则写收件箱建议（reason 带三路证据）。

Usage:
    python -m scripts.scan_missed_merges --dry-run      # 召回+判定，不写库
    python -m scripts.scan_missed_merges --recall-only  # 只跑召回（不调 LLM）
    python -m scripts.scan_missed_merges                # 正式扫描
"""
from __future__ import annotations

import sys

from app.config import get_settings
from app.database import SessionLocal
from app.services.missed_merge_scan import scan_missed_merges
from app.services.settings import resolve_ai_config


def main() -> None:
    # Windows GBK 控制台打印 ↔ 等字符会 UnicodeEncodeError——强制 UTF-8 输出
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    dry_run = "--dry-run" in sys.argv
    recall_only = "--recall-only" in sys.argv

    settings = get_settings()
    db = SessionLocal()
    try:
        config = resolve_ai_config(db, settings)
        stats = scan_missed_merges(
            db, settings, config, dry_run=dry_run, recall_only=recall_only,
            emit=print,
        )
        print(
            ("DRY-RUN " if dry_run else "")
            + f"done: 召回 {stats.recalled}，判 merge {stats.judged_merge}，"
            f"判 split {stats.judged_split}，LLM 无判定 {stats.no_ruling}，"
            f"自动合并 {stats.merged}，写建议 {stats.suggested}，"
            f"清孤儿建议 {stats.orphans_cleaned}"
        )
        if stats.skipped_pairs:
            print(f"跳过已合并对 {len(stats.skipped_pairs)} 对")
        if not dry_run:
            db.commit()
            print("committed")
    finally:
        db.close()


if __name__ == "__main__":
    main()
