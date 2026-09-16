"""Shared per-variant performance briefs for evolution views.

Used by the derivations route (evolution chain) and the review service
(pending-verdict queue) so the spend/payers/cpp/roas aggregation for one
variant lives in exactly one place.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Sequence

from sqlalchemy.orm import Session

from app.models import CreativeVariant, Performance
from app.repositories.assets import AssetRepository
from app.repositories.performance import PerformanceRepository
from app.schemas.derivation import VariantBrief
from app.services.excel import metrics_from_raw
from app.services.matching import matches, normalize


def variant_brief(
    db: Session,
    variant: CreativeVariant,
    *,
    all_performances: Sequence[Performance] | None = None,
) -> VariantBrief:
    asset = AssetRepository(db).get(variant.asset_id)
    filename = asset.filename if asset is not None else ""
    if asset is None:
        rows: list[Performance] = []
    elif all_performances is not None:
        stem = normalize(PurePosixPath(filename).stem)
        rows = [
            row
            for row in all_performances
            if row.creative_name and matches(stem, row.creative_name)
        ]
    else:
        rows = PerformanceRepository(db).list_for_creative_name(
            PurePosixPath(filename).stem
        )
    spend = sum(row.spend for row in rows)
    payers = sum(int(metrics_from_raw(row.raw or {})["payers"] or 0) for row in rows)
    roas_spend = 0.0
    weighted = 0.0
    for row in rows:
        roas = metrics_from_raw(row.raw or {})["d1_roas"]
        if roas is None:
            continue
        roas_spend += row.spend
        weighted += row.spend * roas
    return VariantBrief(
        variant_id=variant.id,
        name=variant.name,
        filename=filename,
        spend=spend,
        payers=payers,
        cpp=spend / payers if payers else None,
        roas=weighted / roas_spend if roas_spend else None,
    )
