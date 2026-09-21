import { useSettings } from "./useSettings";
import { DEFAULT_MARKET_PREFIXES } from "../lib/short-name";
import { translate } from "../lib/i18n";

/** 与后端 services/settings.py 的 DISPLAY_METRICS 顺序一致。 */
export const METRIC_KEYS = [
  "spend",
  "payers",
  "cpp",
  "d1_roas",
  "cpi",
  "ipm",
  "impressions",
  "clicks",
  "installs",
  "d3_roas",
  "d1_retention",
] as const;

/** 指标显示名（随界面语言切换）；未知键原样返回。 */
export function metricLabel(key: string): string {
  return (METRIC_KEYS as readonly string[]).includes(key) ? translate(`metric.${key}`) : key;
}

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
  spend_min_signal: 10.0,
  impressions_min_signal: 5000.0,
};

/** 阈值输入框的键（Settings 页），顺序与旧版 THRESHOLD_LABEL 一致。 */
export const THRESHOLD_KEYS = [
  "cpp_red_line",
  "cpp_pause_line",
  "cpp_efficient",
  "roas_green_line",
  "roas_weak_line",
  "d3_roas_weak_line",
  "d1_retention_weak_line",
  "spend_min_signal",
  "impressions_min_signal",
] as const;

/** 阈值输入框的标签（随界面语言切换）。 */
export function thresholdLabel(key: string): string {
  return translate(`threshold.${key}`);
}

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
