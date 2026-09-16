import type { HubSkew } from "@shared";
import { useT } from "../../lib/i18n";

/** 收件箱顶部：hub 偏斜监控（embedding 设计 §3.5）。
 *
 * 自动 attach 次数按族的分布——最大族占比（top_share）突然变大 =
 * 均值代表向量的 hub 引力在作祟（大族"像所有东西"吸走新素材）。
 * 无自动归入记录时不渲染。
 */
export function HubSkewWidget({ skew }: { skew: HubSkew }) {
  const t = useT();
  if (skew.attach_total === 0) return null;

  return (
    <div className="mx-5 mb-2 rounded-xl bg-neutral-50 px-3 py-2">
      <p className="text-[11px] font-medium text-neutral-500">
        {t("inbox.hubskew.title")}
      </p>
      <p className="mt-0.5 text-[11px] leading-snug text-neutral-600">
        {t("inbox.hubskew.line")
          .replace("{name}", skew.top_creative_name ?? "—")
          .replace("{share}", String(Math.round(skew.top_share * 100)))
          .replace("{top}", String(skew.top_count))
          .replace("{total}", String(skew.attach_total))}
      </p>
      <p className="mt-1 text-[10px] leading-snug text-neutral-400">
        {t("inbox.hubskew.hint")}
      </p>
    </div>
  );
}
