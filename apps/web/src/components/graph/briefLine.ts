import type { RecommendationItem } from "@shared";

/** 金额展示：大数取整，小数保精度（决策卡片空间紧凑）。 */
export function formatDollars(value: number): string {
  if (!Number.isFinite(value)) return "-";
  if (value >= 100) return Math.round(value).toLocaleString("en-US");
  if (value >= 10) return value.toFixed(1);
  return value.toFixed(2);
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
