"""Shared filename ↔ Excel creative_name matching.

Filenames share a long boilerplate ("KS_EN-260101-58-制作人甲-模拟经营-AI-制作人乙-",
~31 chars), so short prefix matching cross-matches different assets, while
one-directional ``startswith`` misses Excel names that drop the filename's
trailing suffix (e.g. ``-竖``). The rule: one side must be a prefix of the
other AND the shared prefix must reach at least MIN_PREFIX chars, i.e. into
the distinguishing part of the name. Short CJK names like
"山林采集-制作人丙" (~39 chars) still pass.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from app.models import Performance

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


@dataclass
class PerformanceIndex:
    """distinct normalized creative_name → the rows sharing it.

    Built once per request/report from the preloaded performances table.
    Matching every variant stem against ~2k distinct names instead of ~11k
    raw rows — and normalizing each name exactly once — is the difference
    between a 26s and a sub-second recommendation pass (same ``matches``
    rule, far fewer calls).
    """

    entries: list[tuple[str, list["Performance"]]]


def index_performances(rows: Sequence["Performance"]) -> PerformanceIndex:
    grouped: dict[str, list[Performance]] = {}
    for row in rows:
        if not row.creative_name:
            continue
        grouped.setdefault(normalize(row.creative_name), []).append(row)
    return PerformanceIndex(entries=list(grouped.items()))


def match_rows(index: PerformanceIndex, asset_stem: str) -> list["Performance"]:
    """All indexed rows matching ``asset_stem`` under the same rule as ``matches``."""
    stem = normalize(asset_stem)
    found: list[Performance] = []
    for excel, rows in index.entries:
        shorter, longer = (stem, excel) if len(stem) <= len(excel) else (excel, stem)
        if len(shorter) >= MIN_PREFIX and longer.startswith(shorter):
            found.extend(rows)
    return found
