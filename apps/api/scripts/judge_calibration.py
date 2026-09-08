"""judge 校准 CLI 薄壳：实现已上移 ``app.services.judge_calibration``
（app 不能依赖 scripts；run_calibration 从此处 re-export 保持兼容）。

Usage:
    python -m scripts.judge_calibration
"""
from __future__ import annotations

import sys

from app.services.judge_calibration import run_calibration


def main() -> None:
    # Windows GBK 控制台打印 ⚠️/✓ 会 UnicodeEncodeError——强制 UTF-8 输出
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        run_calibration(db)
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    main()
