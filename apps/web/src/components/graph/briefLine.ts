import type { RecommendationItem } from "@shared";

/** 金额展示：大数取整，小数保精度（决策卡片空间紧凑）。 */
export function formatDollars(value: number): string {
  if (!Number.isFinite(value)) return "-";
  if (value >= 100) return Math.round(value).toLocaleString("en-US");
  if (value >= 10) return value.toFixed(1);
  return value.toFixed(2);
}

function asNumber(value: unknown): number {
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : NaN;
}

function int(value: unknown): string {
  const n = asNumber(value);
  return Number.isFinite(n) ? Math.round(n).toLocaleString("en-US") : "-";
}

function num(value: unknown, digits = 2): string {
  const n = asNumber(value);
  return Number.isFinite(n) ? n.toFixed(digits) : "-";
}

function pct(value: unknown, digits = 1): string {
  const n = asNumber(value);
  return Number.isFinite(n) ? (n * 100).toFixed(digits) : "-";
}

/**
 * 一句话决策：按 reason_code 取 locales 模板，用 reason_params 插值。
 * 缺模板/缺参数时回退后端旧中文理由（reasons[0]），永远有内容可显示。
 */
export function briefLine(
  item: RecommendationItem,
  t: (key: string) => string,
): string {
  const p = item.reason_params ?? {};
  const key = `brief.line.${item.reason_code}`;
  const template = t(key);
  if (template === key) return item.reasons[0] ?? "";
  return template
    .replace("{days_idle}", int(p.days_idle))
    .replace("{judged_count}", int(p.judged_count))
    .replace("{spend}", int(p.spend))
    .replace("{spend_min}", int(p.spend_min))
    .replace("{impressions}", int(p.impressions))
    .replace("{impressions_min}", int(p.impressions_min))
    .replace("{cpp}", num(p.cpp))
    .replace("{red}", int(p.red))
    .replace("{roas_pct}", pct(p.roas))
    .replace("{green_pct}", pct(p.roas_green, 0))
    .replace("{d3_pct}", pct(p.d3_roas))
    .replace("{weak_pct}", pct(p.d3_roas_weak ?? p.d1_retention_weak, 0))
    .replace("{ret_pct}", pct(p.d1_retention));
}

/** priority 展示文案：≈ $X/天 · Y% 把握；金额≈0 时不显示（返回 null）。 */
export function priorityText(
  item: RecommendationItem,
  t: (key: string) => string,
): string | null {
  if (!Number.isFinite(item.priority_dollars) || item.priority_dollars < 0.01) {
    return null;
  }
  return t("brief.priority")
    .replace("{dollars}", formatDollars(item.priority_dollars))
    .replace("{pct}", String(Math.round(item.confidence * 100)));
}
