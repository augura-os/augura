/** Short display labels for asset filenames (market tag + distinguishing tail).
 *
 * Market prefixes come from GET /settings (services/markets on the backend);
 * pass them in from useSettings/useMetricConfig. Defaults are the neutral
 * examples (KS_EN / KS_KR).
 */

import { translate } from "./i18n";

export const DEFAULT_MARKET_PREFIXES = ["KS_EN", "KS_KR"];

/** 裂变因子键（与后端 DerivationFactor 一致）。 */
export const FACTOR_KEYS = [
  "intro-sticker",
  "language-market",
  "aspect-ratio",
  "voiceover-copy",
  "brand-endcard",
  "character-reskin",
  "reward-reskin",
  "live-action-vs-animation",
  "remake",
  "unknown",
] as const;

/** 裂变因子显示名（随界面语言切换）；未知键原样返回。 */
export function factorLabel(factor: string): string {
  return (FACTOR_KEYS as readonly string[]).includes(factor)
    ? translate(`factor.${factor}`)
    : factor;
}

/**
 * "KS_EN-260104-...-反复挑战重试Ai片头V1-竖.mp4" -> "EN …反复挑战重试Ai片头V1-竖"
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
