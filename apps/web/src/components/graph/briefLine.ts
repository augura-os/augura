import type { ReasonBit, RecommendationItem } from "@shared";
import { getLang } from "../../lib/i18n";

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

/** 裂变因子码 → 本地化标签（factor.* 词表；缺词回退原始码）。 */
function factorLabel(value: unknown, t: (key: string) => string): string {
  const code = typeof value === "string" ? value : "";
  if (!code) return "-";
  const key = `factor.${code}`;
  const label = t(key);
  return label === key ? code : label;
}

/** 逗号串的因子码列表 → 逐码本地化后用顿号（中）/逗号（英）连接。 */
function factorsLabel(value: unknown, t: (key: string) => string): string {
  const codes =
    typeof value === "string"
      ? value
          .split(",")
          .map((code) => code.trim())
          .filter(Boolean)
      : [];
  if (codes.length === 0) return "-";
  return codes.map((code) => factorLabel(code, t)).join(getLang() === "zh" ? "、" : ", ");
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
    .replace("{payers}", int(p.payers))
    .replace("{cpp}", num(p.cpp))
    .replace("{red}", int(p.red))
    .replace("{roas_pct}", pct(p.roas))
    .replace("{green_pct}", pct(p.roas_green, 0))
    .replace("{d3_pct}", pct(p.d3_roas))
    .replace("{weak_pct}", pct(p.d3_roas_weak ?? p.d1_retention_weak, 0))
    .replace("{ret_pct}", pct(p.d1_retention))
    .replace("{factors}", factorsLabel(p.factors, t))
    .replace("{factor}", factorLabel(p.factor, t));
}

/**
 * 一条补充理由：按 brief.bit.<code> 模板插值；模板缺失返回 null
 * （调用方回退后端中文 reasons，兼容旧数据/未来新码）。
 */
export function briefBitLine(bit: ReasonBit, t: (key: string) => string): string | null {
  const key = `brief.bit.${bit.code}`;
  const template = t(key);
  if (template === key) return null;
  const p = bit.params ?? {};
  return template
    .replace("{recent_cpp}", num(p.recent_cpp))
    .replace("{cpp}", num(p.cpp))
    .replace("{variant_count}", int(p.variant_count))
    .replace("{judged_count}", int(p.judged_count))
    .replace("{positive_count}", int(p.positive_count))
    .replace("{pending}", int(p.pending))
    .replace("{partner}", typeof p.partner === "string" ? p.partner : "-");
}

/**
 * 建议全文行：决策句（i18n）+ 补充理由。补充理由优先渲染 reason_bits，
 * 任一码缺模板或 reason_bits 为空时整体回退后端中文 reasons[1:]。
 */
export function recommendationLines(
  item: RecommendationItem,
  t: (key: string) => string,
): string[] {
  const bits = item.reason_bits ?? [];
  const bitLines = bits.map((bit) => briefBitLine(bit, t));
  const evidence =
    bits.length > 0 && bitLines.every((line) => line !== null)
      ? (bitLines as string[])
      : item.reasons.slice(1);
  return [briefLine(item, t), ...evidence];
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

/**
 * 赢家标签徽章文案：brief.label.<code> 模板；缺模板（未来新码/旧数据）
 * 回退原始码，永远有内容可显示。
 */
export function briefLabel(code: string, t: (key: string) => string): string {
  if (!code) return "";
  const key = `brief.label.${code}`;
  const label = t(key);
  return label === key ? code : label;
}
