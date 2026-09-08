"""Facebook Excel export parsing (contract §6 — loose column matching).

Column names are not fixed; a column is recognized when its (lowercased)
header contains one of the known keywords. Both English exports
(name / impression / click / spend / install) and Chinese exports
(素材名称 / 展示数 / 点击数 / 消耗 / 安装数 / 日期) are supported.
A date column is optional.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Sequence

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.exceptions import ApiError
from app.models import CreativeAsset, Performance


@dataclass
class ParsedPerformanceRow:
    creative_name: str
    row_date: date | None
    impressions: int
    clicks: int
    spend: float
    installs: int
    payers: int | None
    d1_roas: float | None
    cpi: float | None
    ipm: float | None
    raw: dict[str, object]
    d3_roas: float | None = None
    d1_retention: float | None = None


# Headers carrying ratios or unit costs must never be matched as count
# metrics: 点击率 is not clicks, 千次展示费用 is not impressions.
_RATE_COST_TOKENS = (
    "率", "ctr", "cvr", "cpc", "cpm", "cpi", "ipm", "roas", "费用", "成本", "单价",
)

# Loose column matching, most specific keyword first. Both Facebook English
# exports and Chinese 素材看板 exports are recognized.
_NAME_KEYS = ("素材名称", "素材名", "广告名称", "ad name", "ad_name", "creative", "name", "名称")
_IMPRESSION_KEYS = ("展示数", "展示次数", "曝光量", "曝光次数", "impression", "展示", "曝光")
_CLICK_KEYS = ("点击数", "点击次数", "链接点击", "click", "点击")
_SPEND_KEYS = ("消耗", "花费金额", "花费", "spend", "amount spent", "amount")
_INSTALL_KEYS = ("安装数", "安装次数", "install", "安装")
_DATE_KEYS = ("日期", "date", "day", "时间")
# Priority metrics for the UA workflow (paid users, D1 ROAS, CPI, IPM).
_PAYER_KEYS = ("付费人数", "付费用户", "payers", "paying users", "payer", "付费")
_D1_ROAS_KEYS = ("d1_roas", "d1roas", "d1 roas", "首日roas", "首日")
_D3_ROAS_KEYS = ("d3_roas", "d3roas", "d3 roas", "三日roas")
_D1_RETENTION_KEYS = ("次留", "次日留存", "d1_retention", "d1 retention")
_CPI_KEYS = ("cpi", "安装成本", "单次安装")
_IPM_KEYS = ("ipm",)


def _find_column(
    columns: Sequence[str], *keywords: str, exclude_rate_cost: bool = False
) -> str | None:
    """Return the first column matching any keyword, most specific first."""
    lowered = [(column, column.lower()) for column in columns]
    for keyword in keywords:
        needle = keyword.lower()
        for original, low in lowered:
            if needle not in low:
                continue
            if exclude_rate_cost and any(
                token in low for token in _RATE_COST_TOKENS
            ):
                continue
            return original
    return None


def _to_number(value: object) -> float:
    if value is None:
        return 0.0
    if isinstance(value, float) and math.isnan(value):
        return 0.0
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if math.isnan(number) else number


def _to_date(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    parsed = pd.to_datetime(text, errors="coerce")
    if isinstance(parsed, pd.Timestamp) and not pd.isna(parsed):
        return parsed.date()
    return None


def _clean_cell(value: object) -> object:
    """Convert a pandas/numpy cell value into a JSON-safe python value."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        number = float(value)
        return None if math.isnan(number) else number
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def parse_excel(path: str) -> list[ParsedPerformanceRow]:
    try:
        frame = pd.read_excel(path)
    except Exception as exc:
        raise ApiError(400, f"Excel 解析失败：{exc}") from exc

    frame = frame.dropna(how="all")
    if frame.empty:
        raise ApiError(400, "Excel 内容为空")

    columns = [str(column) for column in frame.columns]
    frame.columns = columns

    name_col = _find_column(columns, *_NAME_KEYS)
    impressions_col = _find_column(columns, *_IMPRESSION_KEYS, exclude_rate_cost=True)
    clicks_col = _find_column(columns, *_CLICK_KEYS, exclude_rate_cost=True)
    spend_col = _find_column(columns, *_SPEND_KEYS, exclude_rate_cost=True)
    installs_col = _find_column(columns, *_INSTALL_KEYS, exclude_rate_cost=True)
    date_col = _find_column(columns, *_DATE_KEYS)
    payers_col = _find_column(columns, *_PAYER_KEYS, exclude_rate_cost=True)
    d1_roas_col = _find_column(columns, *_D1_ROAS_KEYS)
    d3_roas_col = _find_column(columns, *_D3_ROAS_KEYS)
    d1_retention_col = _find_column(columns, *_D1_RETENTION_KEYS)
    cpi_col = _find_column(columns, *_CPI_KEYS)
    ipm_col = _find_column(columns, *_IPM_KEYS)

    if name_col is None:
        raise ApiError(400, "Excel 缺少名称列（列名需包含 'name' 或 '素材名称/名称'）")

    rows: list[ParsedPerformanceRow] = []
    for _, record in frame.iterrows():
        raw = {column: _clean_cell(record[column]) for column in columns}
        creative_name = str(record[name_col]).strip()
        if not creative_name or creative_name.lower() == "nan":
            continue
        spend = _to_number(record[spend_col]) if spend_col else 0.0
        installs = int(_to_number(record[installs_col])) if installs_col else 0
        impressions = (
            int(_to_number(record[impressions_col])) if impressions_col else 0
        )
        # Prefer the exported CPI/IPM columns; fall back to deriving them.
        cpi = _to_number(record[cpi_col]) if cpi_col else (
            spend / installs if installs else None
        )
        ipm = _to_number(record[ipm_col]) if ipm_col else (
            installs / impressions * 1000 if impressions else None
        )
        rows.append(
            ParsedPerformanceRow(
                creative_name=creative_name,
                row_date=_to_date(record[date_col]) if date_col else None,
                impressions=impressions,
                clicks=int(_to_number(record[clicks_col])) if clicks_col else 0,
                spend=spend,
                installs=installs,
                payers=(
                    int(_to_number(record[payers_col])) if payers_col else None
                ),
                d1_roas=(
                    _to_number(record[d1_roas_col]) if d1_roas_col else None
                ),
                d3_roas=(
                    _to_number(record[d3_roas_col]) if d3_roas_col else None
                ),
                d1_retention=(
                    _to_number(record[d1_retention_col])
                    if d1_retention_col
                    else None
                ),
                cpi=cpi,
                ipm=ipm,
                raw=raw,
            )
        )

    if not rows:
        raise ApiError(400, "Excel 中没有可用的数据行")
    return rows


def metrics_from_raw(raw: dict[str, object]) -> dict[str, float | int | None]:
    """Normalize a stored ``Performance.raw`` row into display metrics.

    The raw JSON keeps the original Excel cells, so rows imported before
    the payer/ROAS/CPI/IPM fields existed still surface them here.
    """
    keys = list(raw.keys())
    payers_col = _find_column(keys, *_PAYER_KEYS, exclude_rate_cost=True)
    d1_roas_col = _find_column(keys, *_D1_ROAS_KEYS)
    d3_roas_col = _find_column(keys, *_D3_ROAS_KEYS)
    d1_retention_col = _find_column(keys, *_D1_RETENTION_KEYS)
    cpi_col = _find_column(keys, *_CPI_KEYS)
    ipm_col = _find_column(keys, *_IPM_KEYS)
    spend_col = _find_column(keys, *_SPEND_KEYS, exclude_rate_cost=True)
    installs_col = _find_column(keys, *_INSTALL_KEYS, exclude_rate_cost=True)
    impressions_col = _find_column(keys, *_IMPRESSION_KEYS, exclude_rate_cost=True)

    spend = _to_number(raw[spend_col]) if spend_col else None
    installs = _to_number(raw[installs_col]) if installs_col else None
    impressions = _to_number(raw[impressions_col]) if impressions_col else None

    cpi = _to_number(raw[cpi_col]) if cpi_col else None
    if cpi is None and spend is not None and installs:
        cpi = spend / installs
    ipm = _to_number(raw[ipm_col]) if ipm_col else None
    if ipm is None and installs is not None and impressions:
        ipm = installs / impressions * 1000

    return {
        "payers": int(_to_number(raw[payers_col])) if payers_col else None,
        "d1_roas": _to_number(raw[d1_roas_col]) if d1_roas_col else None,
        "d3_roas": _to_number(raw[d3_roas_col]) if d3_roas_col else None,
        "d1_retention": (
            _to_number(raw[d1_retention_col]) if d1_retention_col else None
        ),
        "cpi": cpi,
        "ipm": ipm,
    }


@dataclass
class OverlapReport:
    """Rolling-Excel window overlap with rows already in the library."""

    row_count: int
    date_min: date | None
    date_max: date | None
    source_files: list[str] = field(default_factory=list)


def detect_overlap(
    db: Session, parsed_rows: Sequence[ParsedPerformanceRow]
) -> OverlapReport | None:
    """Rows from the new Excel whose (creative_name, date) already exist.

    Rolling Facebook exports always overlap previous windows — silently
    importing both double-counts everything downstream (沙滩烤肉 86→154).
    The import still proceeds; this report becomes an upload warning.
    """
    keys = {(row.creative_name, row.row_date) for row in parsed_rows if row.row_date}
    if not keys:
        return None
    stmt = select(
        Performance.creative_name, Performance.date, CreativeAsset.filename
    ).join(CreativeAsset, CreativeAsset.id == Performance.asset_id)
    hit_rows = 0
    hit_dates: list[date] = []
    files: set[str] = set()
    for creative_name, row_date, filename in db.execute(stmt).all():
        if creative_name is None or row_date is None:
            continue
        if (creative_name, row_date) in keys:
            hit_rows += 1
            hit_dates.append(row_date)
            files.add(filename)
    if hit_rows == 0:
        return None
    return OverlapReport(
        row_count=hit_rows,
        date_min=min(hit_dates),
        date_max=max(hit_dates),
        source_files=sorted(files),
    )
