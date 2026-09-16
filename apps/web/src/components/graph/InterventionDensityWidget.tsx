import type { InterventionDensity } from "@shared";
import { useT } from "../../lib/i18n";
import { cn } from "../../lib/utils";

/** 收件箱顶部：人工介入密度（本周数字 + 近 12 周小柱状，纯 div 不引图表库）。
 *
 * 半RSI 验收指标：每 100 条新素材需要人工裁决的次数，往下走 = 系统在接管。
 * density 为 null 的周（无新素材）画成空槽，不当作 0。
 */
export function InterventionDensityWidget({
  density,
}: {
  density: InterventionDensity;
}) {
  const t = useT();
  const weeks = density.weeks;
  if (weeks.length === 0) return null;

  const per100 = weeks.map((week) =>
    week.density === null ? null : week.density * 100,
  );
  const peak = Math.max(...per100.filter((v): v is number => v !== null), 1);
  const current = density.current;
  const currentPer100 =
    current && current.density !== null ? Math.round(current.density * 100) : null;

  return (
    <div className="mx-5 mb-2 rounded-xl bg-neutral-50 px-3 py-2">
      <div className="flex items-end justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[11px] font-medium text-neutral-500">
            {t("inbox.density.title")}
          </p>
          <p className="mt-0.5 flex items-baseline gap-1.5">
            <span className="text-xl font-semibold tabular-nums text-neutral-900">
              {currentPer100 === null ? "—" : currentPer100}
            </span>
            <span className="text-[10px] leading-snug text-neutral-400">
              {t("inbox.density.per100")}
            </span>
          </p>
        </div>
        <div className="flex h-10 shrink-0 items-end gap-[3px]">
          {weeks.map((week, index) => {
            const value = per100[index];
            const tooltip =
              value === null
                ? `${week.week_start} · ${t("inbox.density.noCreatives")}`
                : `${week.week_start} · ${week.human_rulings}/${week.new_creatives}`;
            return (
              <div
                key={week.week_start}
                title={tooltip}
                className={cn(
                  "w-2 rounded-sm",
                  value === null ? "h-[3px] bg-neutral-200" : "bg-sky-400",
                )}
                style={
                  value === null
                    ? undefined
                    : { height: `${Math.max((value / peak) * 100, 8)}%` }
                }
              />
            );
          })}
        </div>
      </div>
      <p className="mt-1 text-[10px] leading-snug text-neutral-400">
        {t("inbox.density.hint")}
      </p>
    </div>
  );
}
