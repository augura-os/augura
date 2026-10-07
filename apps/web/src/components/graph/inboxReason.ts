import type { ReviewItem } from "@shared";
import { factorLabel } from "../../lib/short-name";

/**
 * 收件箱条目文案渲染：后端发 reason_code + reason_params，前端查
 * `inbox.reason.<code>` / `inbox.title.<code>` 模板插值；模板缺失或
 * code 为空时回退后端中文旧文案（reason/title），永远有内容可显示。
 * 模式与 briefLine.ts（brief.line.<code>）一致。
 */

type Params = Record<string, number | string | null>;

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

function pct(value: unknown, digits = 0): string {
  const n = asNumber(value);
  return Number.isFinite(n) ? (n * 100).toFixed(digits) : "-";
}

/** 带符号两位小数（成本差 +1.23 / -0.45）。 */
function signed(value: unknown): string {
  const n = asNumber(value);
  return Number.isFinite(n) ? `${n >= 0 ? "+" : ""}${n.toFixed(2)}` : "-";
}

function money0(value: unknown): string {
  const n = asNumber(value);
  return Number.isFinite(n) ? `$${Math.round(n).toLocaleString("en-US")}` : "-";
}

function money2(value: unknown): string {
  const n = asNumber(value);
  return Number.isFinite(n)
    ? `$${n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
    : "-";
}

function str(value: unknown): string {
  return typeof value === "string" && value ? value : "-";
}

/** 收件箱 reason 行：有 code 且模板存在 → t() 插值；否则回退 item.reason。 */
export function inboxReasonLine(
  item: Pick<ReviewItem, "reason" | "reason_code" | "reason_params">,
  t: (key: string) => string,
): string {
  const code = item.reason_code;
  if (!code) return item.reason;
  const key = `inbox.reason.${code}`;
  const template = t(key);
  if (template === key) return item.reason;
  const p: Params = item.reason_params ?? {};
  const delta =
    p.cpp_delta === null || p.cpp_delta === undefined
      ? t("inbox.reason.insufficientData")
      : t("inbox.reason.cppDelta").replace("{delta}", signed(p.cpp_delta));
  return template
    .replace("{confidence}", num(p.confidence))
    .replace("{threshold}", num(p.threshold))
    .replace("{variant_count}", int(p.variant_count))
    .replace(
      "{market}",
      typeof p.market === "string" && p.market ? p.market : t("inbox.reason.noMarket"),
    )
    .replace("{score}", num(p.score))
    .replace("{source_name}", str(p.source_name))
    .replace("{source_spend}", money0(p.source_spend))
    .replace("{source_cpp}", p.source_cpp === null ? "-" : money2(p.source_cpp))
    .replace("{target_name}", str(p.target_name))
    .replace("{target_spend}", money0(p.target_spend))
    .replace("{target_cpp}", p.target_cpp === null ? "-" : money2(p.target_cpp))
    .replace("{delta}", delta)
    .replace("{reason}", str(p.reason))
    .replace("{factor}", factorLabel(str(p.verdict)))
    .replace("{performance}", int(p.performance))
    .replace("{freshness}", int(p.freshness))
    .replace("{evolution}", int(p.evolution))
    .replace("{days_idle}", int(p.days_idle))
    .replace("{same_pct}", pct(p.same_pair_rate))
    .replace("{cross_pct}", pct(p.cross_pair_rate))
    .replace("{support}", int(p.support));
}

/** 收件箱 title 行：仅少数 kind 的后端 title 是中文常量，按 code 翻译；
 * 模板缺失回退 item.title（创意名/文件名等数据文本原样展示）。 */
export function inboxTitleLine(
  item: Pick<ReviewItem, "title" | "reason_code" | "reason_params">,
  t: (key: string) => string,
): string {
  const code = item.reason_code;
  if (!code) return item.title;
  const key = `inbox.title.${code}`;
  const template = t(key);
  if (template === key) return item.title;
  const p: Params = item.reason_params ?? {};
  const targetCode = str(p.target);
  const targetLabel = t(`inbox.rule.target.${targetCode}`);
  return template
    .replace("{word}", str(p.word))
    .replace("{target}", targetLabel === `inbox.rule.target.${targetCode}` ? targetCode : targetLabel)
    .replace("{prefix}", str(p.prefix));
}
