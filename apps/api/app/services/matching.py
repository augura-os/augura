"""Shared filename ↔ Excel creative_name matching.

Filenames share a long boilerplate ("KS_EN-260630-58-制作人甲-模拟经营-AI-制作人乙-",
~31 chars), so short prefix matching cross-matches different assets, while
one-directional ``startswith`` misses Excel names that drop the filename's
trailing suffix (e.g. ``-竖``). The rule: one side must be a prefix of the
other AND the shared prefix must reach at least MIN_PREFIX chars, i.e. into
the distinguishing part of the name. Short CJK names like
"丛林买枪-制作人丙" (~39 chars) still pass.
"""

from __future__ import annotations

import unicodedata

MIN_PREFIX = 34


def normalize(name: str) -> str:
    name = unicodedata.normalize("NFKC", name)
    if "." in name:
        name = name.rsplit(".", 1)[0]
    return name.strip().strip("-_ ").lower()


def matches(asset_filename: str, excel_creative_name: str) -> bool:
    stem = normalize(asset_filename)
    excel = normalize(excel_creative_name)
    shorter, longer = (stem, excel) if len(stem) <= len(excel) else (excel, stem)
    return len(shorter) >= MIN_PREFIX and longer.startswith(shorter)
