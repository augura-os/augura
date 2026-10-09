"""Unit tests for app.services.excel (Chinese Facebook export parsing)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from app.services.excel import metrics_from_raw, normalize_objective, parse_excel


def _write_xlsx(path: Path, rows: list[dict[str, object]]) -> None:
    pd.DataFrame(rows).to_excel(path, index=False)


def test_parse_chinese_columns(tmp_path: Path) -> None:
    path = tmp_path / "report.xlsx"
    _write_xlsx(path, [
        {
            "素材名称": "KS_EN-260101-素材A-竖",
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
    assert row.creative_name == "KS_EN-260101-素材A-竖"
    assert row.spend == 100.5
    assert row.impressions == 12000
    assert row.clicks == 240
    assert row.installs == 50
    assert row.payers == 3
    assert row.d1_roas == 0.021
    assert row.cpi == 2.01
    assert row.ipm == 4.17
    assert row.row_date is not None and row.row_date.isoformat() == "2026-07-15"
    assert row.raw["素材名称"] == "KS_EN-260101-素材A-竖"


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


def test_resolve_raw_columns_cache_keyed_by_keys_not_values() -> None:
    """列解析缓存以列名集合为 key：同布局不同数值的行不得串值。"""
    base = {"素材名称": "x", "消耗": 100.0, "安装数": 50}
    first = metrics_from_raw(dict(base))
    second = metrics_from_raw({**base, "消耗": 200.0, "安装数": 25})
    assert first["cpi"] == 2.0
    assert second["cpi"] == 8.0  # 若缓存误存结果而非列名，这里会错成 2.0


def test_resolve_raw_columns_equivalent_across_key_order() -> None:
    """key 顺序不同但集合相同 → 命中同一缓存项，解析结果一致。"""
    from app.services.excel import _resolve_raw_columns

    keys = ["素材名称", "消耗", "安装数", "展示数"]
    forward = _resolve_raw_columns(tuple(sorted(keys)))
    reverse = _resolve_raw_columns(tuple(sorted(keys, reverse=True)))
    assert forward == reverse


class TestOptimizationColumn:
    """优化方式列：中/英/无列三态 + 归一映射（迁移 0020 回填同口径）。"""

    def test_chinese_column_parsed_and_normalized(self, tmp_path: Path) -> None:
        path = tmp_path / "report.xlsx"
        _write_xlsx(path, [
            {"素材名称": "素材D", "消耗": 10.0, "优化方式": "安装"},
            {"素材名称": "素材E", "消耗": 10.0, "优化方式": "AEO"},
            {"素材名称": "素材F", "消耗": 10.0, "优化方式": "VO"},
        ])
        rows = parse_excel(str(path))
        assert [row.optimization_type for row in rows] == ["install", "aeo", "vo"]

    def test_english_column_alias(self, tmp_path: Path) -> None:
        # 每个别名列单独成文件（_find_column 是 first-match，不合并多别名列）
        path = tmp_path / "report-opt.xlsx"
        _write_xlsx(path, [{"name": "creative-g", "spend": 10.0, "optimization": "MAI"}])
        (row,) = parse_excel(str(path))
        assert row.optimization_type == "install"
        path2 = tmp_path / "report-obj.xlsx"
        _write_xlsx(path2, [{"name": "creative-h", "spend": 10.0, "objective": "value optimization"}])
        (row2,) = parse_excel(str(path2))
        assert row2.optimization_type == "vo"

    def test_missing_column_defaults_to_none(self, tmp_path: Path) -> None:
        path = tmp_path / "report.xlsx"
        _write_xlsx(path, [{"素材名称": "素材I", "消耗": 10.0}])
        (row,) = parse_excel(str(path))
        assert row.optimization_type is None

    def test_unrecognized_value_normalizes_to_none(self, tmp_path: Path) -> None:
        path = tmp_path / "report.xlsx"
        _write_xlsx(path, [{"素材名称": "素材J", "消耗": 10.0, "优化方式": "别的"}])
        (row,) = parse_excel(str(path))
        assert row.optimization_type is None

    def test_normalize_objective_mapping(self) -> None:
        for raw_value in ("安装", "install", "Installs", "MAI", " mai "):
            assert normalize_objective(raw_value) == "install"
        assert normalize_objective("AEO") == "aeo"
        assert normalize_objective("value optimization") == "vo"
        assert normalize_objective("VO") == "vo"
        assert normalize_objective("") is None
        assert normalize_objective(None) is None
        assert normalize_objective("未知") is None


def test_parsed_row_persisted_with_optimization_type(tmp_path: Path, db_session) -> None:
    """上传落库链路：ParsedPerformanceRow.optimization_type → Performance 列。"""
    import uuid

    from app.models import CreativeAsset
    from app.repositories.performance import PerformanceRepository

    asset = CreativeAsset(
        id=str(uuid.uuid4()),
        filename="KS_FAKE-excel-objective.xlsx",
        file_type="excel",
        storage_key=f"test/{uuid.uuid4()}",
    )
    db_session.add(asset)
    db_session.flush()
    path = tmp_path / "report.xlsx"
    _write_xlsx(path, [{"素材名称": "素材K", "消耗": 10.0, "优化方式": "AEO"}])
    rows = parse_excel(str(path))
    created = PerformanceRepository(db_session).bulk_create(asset.id, rows)
    assert created[0].optimization_type == "aeo"
