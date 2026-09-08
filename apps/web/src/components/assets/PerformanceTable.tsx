import type { PerformanceRecord } from "@shared";
import { METRIC_LABEL, useMetricConfig } from "../../hooks/useMetricConfig";

function money(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `$${value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function pct(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

function num(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString("en-US");
}

/** 每个指标键的行值/汇总值提取与格式化（profile 驱动渲染）。 */
const METRIC_FORMAT: Record<string, (value: number | null) => string> = {
  spend: money,
  cpp: money,
  cpi: money,
  d1_roas: pct,
  d3_roas: pct,
  d1_retention: pct,
  ipm: (value) => (value === null ? "—" : value.toFixed(1)),
  payers: num,
  impressions: num,
  clicks: num,
  installs: num,
};

function rowValue(row: PerformanceRecord, key: string): number | null {
  switch (key) {
    case "spend": return row.spend;
    case "payers": return row.payers;
    case "cpp": return row.cost_per_payer;
    case "d1_roas": return row.d1_roas;
    case "d3_roas": return row.d3_roas ?? null;
    case "d1_retention": return row.d1_retention ?? null;
    case "cpi": return row.cpi;
    case "ipm": return row.ipm;
    case "impressions": return row.impressions;
    case "clicks": return row.clicks;
    case "installs": return row.installs;
    default: return null;
  }
}

function useThresholdClasses() {
  const { thresholds } = useMetricConfig();
  return (key: string, value: number | null): string => {
    if (value === null) return "text-neutral-800";
    if (key === "cpp") {
      return value < thresholds.cpp_red_line
        ? "text-emerald-600 font-medium"
        : "text-red-600 font-medium";
    }
    if (key === "d1_roas") {
      return value >= thresholds.roas_green_line ? "text-emerald-600 font-medium" : "text-neutral-800";
    }
    if (key === "d3_roas") {
      return value >= thresholds.d3_roas_weak_line ? "text-emerald-600 font-medium" : "text-neutral-800";
    }
    if (key === "d1_retention") {
      return value >= thresholds.d1_retention_weak_line ? "text-emerald-600 font-medium" : "text-neutral-800";
    }
    return "text-neutral-800";
  };
}

function weighted(
  rows: PerformanceRecord[],
  key: "d1_roas" | "d3_roas" | "d1_retention",
): number | null {
  const valid = rows.filter((row) => row[key] !== null && row[key] !== undefined && (row.spend ?? 0) > 0);
  const spend = valid.reduce((sum, row) => sum + (row.spend ?? 0), 0);
  return spend
    ? valid.reduce((sum, row) => sum + (row[key] ?? 0) * (row.spend ?? 0), 0) / spend
    : null;
}

function SummaryItem({ label, value, className }: { label: string; value: string; className?: string }) {
  return (
    <div>
      <p className="text-[10px] font-medium uppercase tracking-wide text-neutral-400">{label}</p>
      <p className={`text-sm ${className ?? "text-neutral-900"}`}>{value}</p>
    </div>
  );
}

export function PerformanceSummary({ rows }: { rows: PerformanceRecord[] }) {
  const { profile } = useMetricConfig();
  const tone = useThresholdClasses();

  const spend = rows.reduce((sum, row) => sum + (row.spend ?? 0), 0);
  const installs = rows.reduce((sum, row) => sum + (row.installs ?? 0), 0);
  const impressions = rows.reduce((sum, row) => sum + (row.impressions ?? 0), 0);
  const clicks = rows.reduce((sum, row) => sum + (row.clicks ?? 0), 0);
  const payerRows = rows.filter((row) => row.payers !== null);
  const payers = payerRows.length
    ? payerRows.reduce((sum, row) => sum + (row.payers ?? 0), 0)
    : null;

  const totals: Record<string, number | null> = {
    spend,
    payers,
    cpp: payers ? spend / payers : null,
    d1_roas: weighted(rows, "d1_roas"),
    d3_roas: weighted(rows, "d3_roas"),
    d1_retention: weighted(rows, "d1_retention"),
    cpi: installs ? spend / installs : null,
    ipm: impressions ? (installs / impressions) * 1000 : null,
    impressions,
    clicks,
    installs,
  };

  return (
    <div className="grid grid-cols-3 gap-2 rounded-md border border-[#f0f0f0] bg-neutral-50 px-3 py-2">
      {profile.map((key) => (
        <SummaryItem
          key={key}
          label={METRIC_LABEL[key] ?? key}
          value={(METRIC_FORMAT[key] ?? num)(totals[key] ?? null)}
          className={tone(key, totals[key] ?? null)}
        />
      ))}
    </div>
  );
}

interface PerformanceTableProps {
  rows: PerformanceRecord[];
  /** Show the creative_name column (useful on the Excel asset detail). */
  showName?: boolean;
}

export function PerformanceTable({ rows, showName = false }: PerformanceTableProps) {
  const { profile } = useMetricConfig();
  const tone = useThresholdClasses();
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs text-neutral-800">
        <thead>
          <tr className="border-b border-[#e5e5e5] text-left text-neutral-500">
            <th className="py-1 pr-2 font-medium">Date</th>
            {showName ? <th className="py-1 pr-2 font-medium">素材</th> : null}
            {profile.map((key, index) => (
              <th key={key} className={index === profile.length - 1 ? "py-1 font-medium" : "py-1 pr-2 font-medium"}>
                {METRIC_LABEL[key] ?? key}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id} className="border-b border-[#f5f5f5] last:border-0">
              <td className="whitespace-nowrap py-1 pr-2">{row.date ?? "—"}</td>
              {showName ? (
                <td className="max-w-[220px] truncate py-1 pr-2" title={row.creative_name ?? ""}>
                  {row.creative_name ?? "—"}
                </td>
              ) : null}
              {profile.map((key, index) => {
                const value = rowValue(row, key);
                return (
                  <td key={key} className={`${index === profile.length - 1 ? "py-1" : "py-1 pr-2"} ${tone(key, value)}`}>
                    {(METRIC_FORMAT[key] ?? num)(value)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
