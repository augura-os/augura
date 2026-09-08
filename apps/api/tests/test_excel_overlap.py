"""Tests for rolling-Excel overlap detection (services/excel.detect_overlap)."""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy.orm import Session

from app.models import CreativeAsset, Performance
from app.services.excel import ParsedPerformanceRow, detect_overlap


def _seed_excel_asset(db: Session, filename: str) -> CreativeAsset:
    asset = CreativeAsset(
        id=str(uuid.uuid4()),
        filename=filename,
        file_type="excel",
        analysis_status="none",
        storage_key=f"test/{uuid.uuid4()}",
    )
    db.add(asset)
    db.flush()
    return asset


def _perf(db: Session, asset_id: str, name: str, day: date) -> None:
    db.add(
        Performance(
            id=str(uuid.uuid4()),
            asset_id=asset_id,
            creative_name=name,
            date=day,
            spend=10.0,
            raw={},
        )
    )
    db.flush()


def _row(name: str, day: date) -> ParsedPerformanceRow:
    return ParsedPerformanceRow(
        creative_name=name,
        row_date=day,
        impressions=100,
        clicks=10,
        spend=1.0,
        installs=1,
        payers=1,
        d1_roas=0.01,
        cpi=1.0,
        ipm=1.0,
        raw={},
    )


def test_overlap_detected_with_source_file(db_session: Session) -> None:
    asset = _seed_excel_asset(db_session, "素材看板-old.xlsx")
    _perf(db_session, asset.id, "素材A", date(2026, 7, 10))
    _perf(db_session, asset.id, "素材A", date(2026, 7, 12))
    _perf(db_session, asset.id, "素材B", date(2026, 7, 12))

    rows = [
        _row("素材A", date(2026, 7, 10)),  # 重叠
        _row("素材A", date(2026, 7, 12)),  # 重叠
        _row("素材A", date(2026, 7, 20)),  # 新日期
    ]
    report = detect_overlap(db_session, rows)
    assert report is not None
    assert report.row_count == 2
    assert report.date_min == date(2026, 7, 10)
    assert report.date_max == date(2026, 7, 12)
    assert report.source_files == ["素材看板-old.xlsx"]


def test_no_overlap_returns_none(db_session: Session) -> None:
    asset = _seed_excel_asset(db_session, "素材看板-old.xlsx")
    _perf(db_session, asset.id, "素材A", date(2026, 7, 10))
    rows = [_row("素材A", date(2026, 7, 20)), _row("素材C", date(2026, 7, 10))]
    assert detect_overlap(db_session, rows) is None


def test_rows_without_date_never_overlap(db_session: Session) -> None:
    asset = _seed_excel_asset(db_session, "素材看板-old.xlsx")
    _perf(db_session, asset.id, "素材A", date(2026, 7, 10))
    rows = [_row("素材A", date(2026, 7, 10))]
    for row in rows:
        row.row_date = None
    assert detect_overlap(db_session, rows) is None
