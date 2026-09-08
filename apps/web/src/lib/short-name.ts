/** Short display labels for asset filenames (market tag + distinguishing tail).
 *
 * Market prefixes come from GET /settings (services/markets on the backend);
 * pass them in from useSettings/useMetricConfig. Defaults are the neutral
 * examples (KS_EN / KS_KR).
 */

export const DEFAULT_MARKET_PREFIXES = ["KS_EN", "KS_KR"];

export const FACTOR_LABEL: Record<string, string> = {
  "intro-sticker": "前贴",
  "language-market": "语言/市场",
  "aspect-ratio": "画幅",
  "voiceover-copy": "口播",
  "brand-endcard": "品牌尾页",
  "character-reskin": "角色换皮",
  "reward-reskin": "奖励换皮",
  "live-action-vs-animation": "真人/动画",
  remake: "重制",
  unknown: "待定",
};

/**
 * "KS_EN-260611-...-屡次失败重开Ai片头V1-竖.mp4" -> "EN …屡次失败重开Ai片头V1-竖"
 * Filenames share a long boilerplate and differ at the end (V1/V2/B版/前贴/画幅).
 */
export function shortAssetLabel(
  filename: string,
  prefixes: string[] = DEFAULT_MARKET_PREFIXES,
): string {
  let stem = filename.replace(/\.[^.]+$/, "");
  let market = "";
  for (const prefix of prefixes) {
    if (stem.toUpperCase().startsWith(prefix.toUpperCase() + "-")) {
      market = prefix.split("_")[1]?.toUpperCase() ?? prefix.toUpperCase();
      stem = stem.slice(prefix.length + 1);
      break;
    }
  }
  const segments = stem.split("-");
  let tail = segments.length >= 2 ? segments.slice(-2).join("-") : stem;
  if (tail.length < 6 && segments.length >= 3) {
    tail = segments.slice(-3).join("-");
  }
  return `${market} …${tail}`.trim();
}
