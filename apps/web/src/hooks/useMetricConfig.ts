import { useSettings } from "./useSettings";
import { DEFAULT_MARKET_PREFIXES } from "../lib/short-name";

/** 与后端 services/settings.py 的 DISPLAY_METRICS / DEFAULT_* 保持一致。 */
export const METRIC_LABEL: Record<string, string> = {
  spend: "消耗",
  payers: "付费人数",
  cpp: "付费成本",
  d1_roas: "首日Roas",
  cpi: "CPI",
  ipm: "IPM",
  impressions: "展示",
  clicks: "点击",
  installs: "安装",
  d3_roas: "D3 Roas",
  d1_retention: "次留",
};

/** 可参与判定的指标（与后端 JUDGEABLE_METRICS 一致）。 */
export const JUDGEABLE_METRICS = ["cpp", "d1_roas", "d3_roas", "d1_retention"] as const;

export const DEFAULT_METRIC_PROFILE = ["payers", "cpp", "spend", "d1_roas", "cpi", "ipm"];

export const DEFAULT_THRESHOLDS: Record<string, number> = {
  cpp_red_line: 120.0,
  cpp_pause_line: 180.0,
  cpp_efficient: 60.0,
  roas_green_line: 0.02,
  roas_weak_line: 0.01,
  d3_roas_weak_line: 0.04,
  d1_retention_weak_line: 0.35,
};

/** 阈值输入框的中文标签（Settings 页）。 */
export const THRESHOLD_LABEL: Record<string, string> = {
  cpp_red_line: "付费成本红线（$）",
  cpp_pause_line: "付费成本暂停线（$）",
  cpp_efficient: "效率领先线（$）",
  roas_green_line: "D1 Roas 绿线（0.02 = 2%）",
  roas_weak_line: "D1 Roas 弱线",
  d3_roas_weak_line: "D3 Roas 弱线",
  d1_retention_weak_line: "次留弱线（0.35 = 35%）",
};

export interface MetricConfig {
  profile: string[];
  judgeMetrics: string[];
  thresholds: Record<string, number>;
  marketPrefixes: string[];
}

/** 指标配置：settings 未加载/未配置时回退默认值（行为与旧版一致）。 */
export function useMetricConfig(): MetricConfig {
  const { data } = useSettings();
  return {
    profile:
      data?.metric_profile?.length ? data.metric_profile : DEFAULT_METRIC_PROFILE,
    judgeMetrics: data?.judge_metrics?.length
      ? data.judge_metrics
      : ["cpp", "d1_roas"],
    thresholds: { ...DEFAULT_THRESHOLDS, ...(data?.metric_thresholds ?? {}) },
    marketPrefixes: data?.market_prefixes?.length
      ? data.market_prefixes
      : DEFAULT_MARKET_PREFIXES,
  };
}
