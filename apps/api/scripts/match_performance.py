"""Match performance rows (from uploaded Excel) back to video assets by name.

Problem with the old ad-hoc matcher: it used a 30-char prefix, but our
filenames share a long boilerplate ("KS_EN-260630-58-制作人甲-模拟经营-AI-制作人乙-"),
so different assets cross-matched each other's rows.

Rule: normalize both sides, then accept a match only when one side is a
prefix of the other AND the shared prefix is at least MIN_PREFIX chars.
The shared boilerplate is ~31 chars, so 34 forces the match to reach into
the distinguishing part while still accepting short CJK names like
"丛林买枪-制作人丙" (~39 chars).

Usage:
    python -m scripts.match_performance            # print per-asset report
"""
from __future__ import annotations

import os
import unicodedata

from sqlalchemy import create_engine, text

MIN_PREFIX = 34


def normalize(name: str) -> str:
    name = unicodedata.normalize("NFKC", name)
    name = name.rsplit(".", 1)[0] if "." in name else name
    return name.strip().strip("-_ ").lower()


def num(v: object) -> float:
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def matches(stem: str, excel_name: str) -> bool:
    shorter, longer = (stem, excel_name) if len(stem) <= len(excel_name) else (excel_name, stem)
    return len(shorter) >= MIN_PREFIX and longer.startswith(shorter)


def main() -> None:
    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as c:
        assets = c.execute(
            text("select id, filename from creative_assets where file_type != 'excel'")
        ).fetchall()
        perfs = c.execute(
            text("select creative_name, spend, installs, raw from performances")
        ).fetchall()

    for asset_id, filename in assets:
        stem = normalize(filename)
        matched = [p for p in perfs if p[0] and matches(stem, normalize(p[0]))]
        spend = sum(num(p[1]) for p in matched)
        payers = sum(num((p[3] or {}).get("付费人数")) for p in matched)
        installs = sum(num(p[2]) for p in matched)

        def wavg(key: str) -> float:
            weighted = sum(num(p[1]) * num((p[3] or {}).get(key)) for p in matched)
            return weighted / spend if spend else 0.0

        roas = wavg("D1_Roas") * 100
        ipm = wavg("IPM")
        cpi = spend / installs if installs else 0.0
        cpp = spend / payers if payers else 0.0
        flag = "✅" if cpp and cpp < 120 else ("🔴" if cpp else "⚪")
        line = (
            "{} | {:>3}行 | 消耗${:>8.2f} | 付费{:>3} | 成本{:>8} | "
            "D1Roas {:>5.2f}% | CPI ${:>6.2f} | IPM {:>5.2f} {} | {}"
        )
        print(
            line.format(
                asset_id[:8], len(matched), spend, int(payers),
                ("$%.2f" % cpp) if cpp else "-", roas, cpi, ipm, flag, filename[:38],
            )
        )


if __name__ == "__main__":
    main()
