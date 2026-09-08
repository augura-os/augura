"""Unit tests for app.services.excel (Chinese Facebook export parsing)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from app.services.excel import metrics_from_raw, parse_excel


def _write_xlsx(path: Path, rows: list[dict[str, object]]) -> None:
    pd.DataFrame(rows).to_excel(path, index=False)


def test_parse_chinese_columns(tmp_path: Path) -> None:
    path = tmp_path / "report.xlsx"
    _write_xlsx(path, [
        {
            "素材名称": "KS_EN-260630-素材A-竖",
            "消耗": 100.5,
            "展示数": 12000,
            "点击数": 240,
            "安装数": 50,
            "付费人数": 3,
            "D1_Roas": 0.021,
            "CPI": 2.01,
            "IPM": 4.17,
            "日期": "2026-07-15",
        }
    ])
    (row,) = parse_excel(str(path))
    assert row.creative_name == "KS_EN-260630-素材A-竖"
    assert row.spend == 100.5
    assert row.impressions == 12000
    assert row.clicks == 240
    assert row.installs == 50
    assert row.payers == 3
    assert row.d1_roas == 0.021
    assert row.cpi == 2.01
    assert row.ipm == 4.17
    assert row.row_date is not None and row.row_date.isoformat() == "2026-07-15"
    assert row.raw["素材名称"] == "KS_EN-260630-素材A-竖"


def test_rate_and_cost_columns_not_matched_as_counts(tmp_path: Path) -> None:
    """点击率 must not be read as clicks; 千次展示费用 not as impressions."""
    path = tmp_path / "report.xlsx"
    _write_xlsx(path, [
        {
            "素材名称": "素材B",
            "点击率": 0.02,
            "千次展示费用": 2.74,
            "消耗": 33.91,
            "展示数": 12364,
            "点击数": 89,
        }
    ])
    (row,) = parse_excel(str(path))
    assert row.clicks == 89
    assert row.impressions == 12364


def test_cpi_ipm_derived_when_columns_missing(tmp_path: Path) -> None:
    path = tmp_path / "report.xlsx"
    _write_xlsx(path, [
        {"素材名称": "素材C", "消耗": 100.0, "展示数": 5000, "安装数": 25}
    ])
    (row,) = parse_excel(str(path))
    assert row.cpi == 4.0
    assert row.ipm == 5.0
    assert row.payers is None


def test_metrics_from_raw_chinese_keys() -> None:
    raw = {
        "素材名称": "x",
        "消耗": 2055.82,
        "付费人数": 16,
        "D1_Roas": 0.0072,
        "CPI": 0.86,
        "IPM": 4.0,
    }
    metrics = metrics_from_raw(raw)
    assert metrics == {
        "payers": 16,
        "d1_roas": 0.0072,
        "d3_roas": None,
        "d1_retention": None,
        "cpi": 0.86,
        "ipm": 4.0,
    }


def test_metrics_from_raw_d3_and_retention() -> None:
    """D3_Roas / 次留 从历史 raw 行追溯提取（v0.11 指标扩展）。"""
    raw = {"素材名称": "x", "D3_Roas": 0.0606, "次留": 0.3077}
    metrics = metrics_from_raw(raw)
    assert metrics["d3_roas"] == 0.0606
    assert metrics["d1_retention"] == 0.3077


def test_metrics_from_raw_missing_keys() -> None:
    assert metrics_from_raw({}) == {
        "payers": None,
        "d1_roas": None,
        "d3_roas": None,
        "d1_retention": None,
        "cpi": None,
        "ipm": None,
    }
