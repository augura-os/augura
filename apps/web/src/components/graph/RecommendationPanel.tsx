import { useMemo, useState } from "react";
import { ChevronRight, Lightbulb, X } from "lucide-react";
import type { RecommendationAction, RecommendationItem } from "@shared";
import { useRecommendations } from "../../hooks/useRecommendations";
import { useMetricConfig } from "../../hooks/useMetricConfig";
import { useT } from "../../lib/i18n";
import { cn } from "../../lib/utils";
import { briefLine, formatDollars, priorityText } from "./briefLine";

type Group = "urgent" | "optimize" | "healthy";

const GROUP_META: Record<
  Group,
  { bar: string; text: string; badge: string; actions: RecommendationAction[] }
> = {
  urgent: {
    bar: "bg-red-500",
    text: "text-red-600",
    badge: "bg-red-50 text-red-600",
    actions: ["PAUSE", "ARCHIVE"],
  },
  optimize: {
    bar: "bg-amber-500",
    text: "text-amber-600",
    badge: "bg-amber-50 text-amber-600",
    actions: ["ITERATE"],
  },
  healthy: {
    bar: "bg-emerald-500",
    text: "text-emerald-600",
    badge: "bg-emerald-50 text-emerald-600",
    actions: ["KEEP"],
  },
};
const GROUP_ORDER: Group[] = ["urgent", "optimize", "healthy"];
const TOP_N = 3;

const GROUP_COLLAPSE_KEY = "augura-rec-panel-collapse-v1";
const PANEL_COLLAPSE_KEY = "augura-rec-panel-v2";

function loadGroupsCollapsed(): Record<Group, boolean> {
  try {
    return { optimize: true, healthy: true, urgent: false, ...JSON.parse(localStorage.getItem(GROUP_COLLAPSE_KEY) ?? "{}") };
  } catch {
    return { urgent: false, optimize: true, healthy: true };
  }
}

function loadPanelCollapsed(): boolean {
  // 默认展开（决策简报要一眼可见）；用户手动收起后记住选择
  try {
    return localStorage.getItem(PANEL_COLLAPSE_KEY) === "1";
  } catch {
    return false;
  }
}

function fmtCpp(
  item: RecommendationItem,
  cppRedLine: number,
  zeroPayers: string,
): { text: string; tone: string } {
  const { cpp, payers } = item.metrics;
  if (payers === 0 && item.metrics.spend >= 50) return { text: zeroPayers, tone: "text-red-600" };
  if (cpp === null) return { text: "—", tone: "text-neutral-400" };
  if (cpp >= cppRedLine) return { text: `$${cpp.toFixed(0)}`, tone: "text-red-600 font-semibold" };
  return { text: `$${cpp.toFixed(0)}`, tone: "text-neutral-500" };
}

function ItemCard({
  item,
  group,
  cppRedLine,
  onSelect,
}: {
  item: RecommendationItem;
  group: Group;
  cppRedLine: number;
  onSelect: (id: string) => void;
}) {
  const t = useT();
  const [showEvidence, setShowEvidence] = useState(false);
  const priority = priorityText(item, t);
  const evidence = item.reasons.slice(1);
  const cpp = fmtCpp(item, cppRedLine, t("brief.zeroPayers"));
  const tooltip = [
    item.creative_name,
    ...item.reasons,
    t("brief.tooltipBase")
      .replace("{spend}", item.metrics.spend.toLocaleString())
      .replace("{payers}", String(item.metrics.payers)) +
      (item.metrics.roas !== null
        ? t("brief.tooltipRoas").replace("{roas}", (item.metrics.roas * 100).toFixed(1))
        : ""),
  ].join("\n");
  return (
    <div className="rounded-lg px-2 py-1.5 transition hover:bg-black/[0.04]">
      <div className="flex items-center gap-2">
        <span className={cn("h-6 w-0.5 shrink-0 rounded-full", GROUP_META[group].bar)} />
        <button
          onClick={() => onSelect(`creative:${item.creative_id}`)}
          className="min-w-0 flex-1 text-left"
          title={tooltip}
        >
          <span className="flex items-center gap-1.5">
            <span
              className={cn(
                "shrink-0 rounded px-1 py-px text-[10px] font-semibold",
                GROUP_META[group].badge,
              )}
            >
              {t(`brief.action.${item.action}`)}
            </span>
            <span className="min-w-0 truncate text-[13px] font-medium text-neutral-800">
              {item.creative_name}
            </span>
          </span>
        </button>
        <span className={cn("shrink-0 text-[11px] tabular-nums", cpp.tone)}>{cpp.text}</span>
        {priority ? (
          <span className="shrink-0 text-[11px] tabular-nums text-neutral-500">{priority}</span>
        ) : null}
      </div>
      <div className="mt-0.5 flex items-start gap-2 pl-2.5">
        <p className="min-w-0 flex-1 text-[12px] leading-snug text-neutral-600">
          {briefLine(item, t)}
        </p>
        {evidence.length > 0 ? (
          <button
            onClick={() => setShowEvidence((v) => !v)}
            className="flex shrink-0 items-center gap-0.5 text-[11px] text-neutral-400 transition hover:text-neutral-600"
            aria-label={t("brief.evidence")}
          >
            <ChevronRight className={cn("h-3 w-3 transition-transform", showEvidence && "rotate-90")} />
            {t("brief.evidence")}
          </button>
        ) : null}
      </div>
      {showEvidence && evidence.length > 0 ? (
        <ul className="ml-2.5 mt-1 list-disc space-y-0.5 pl-3 text-[11px] leading-snug text-neutral-500">
          {evidence.map((reason, index) => (
            <li key={index}>{reason}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export function RecommendationPanel({
  onSelect,
}: {
  onSelect: (creativeNodeId: string) => void;
}) {
  const t = useT();
  const { data } = useRecommendations();
  const { thresholds } = useMetricConfig();
  const cppRedLine = thresholds.cpp_red_line;
  const [collapsed, setCollapsed] = useState(loadPanelCollapsed);
  const [groupsCollapsed, setGroupsCollapsed] = useState<Record<Group, boolean>>(loadGroupsCollapsed);
  const [showAll, setShowAll] = useState<Record<Group, boolean>>({ urgent: false, optimize: false, healthy: false });

  const groups = useMemo(() => {
    const map = new Map<Group, RecommendationItem[]>();
    for (const group of GROUP_ORDER) map.set(group, []);
    for (const item of data?.items ?? []) {
      const group = GROUP_ORDER.find((g) => GROUP_META[g].actions.includes(item.action));
      if (group) map.get(group)?.push(item);
    }
    return map;
  }, [data]);

  const urgentDollars = useMemo(
    () => (groups.get("urgent") ?? []).reduce((sum, item) => sum + item.priority_dollars, 0),
    [groups],
  );

  if (!data || data.items.length === 0) return null;

  const togglePanel = (next: boolean) => {
    setCollapsed(next);
    try {
      localStorage.setItem(PANEL_COLLAPSE_KEY, next ? "1" : "0");
    } catch {
      // 持久化失败仅本次会话生效
    }
  };

  const toggleGroup = (group: Group) => {
    setGroupsCollapsed((prev) => {
      const next = { ...prev, [group]: !prev[group] };
      try {
        localStorage.setItem(GROUP_COLLAPSE_KEY, JSON.stringify(next));
      } catch {
        // 持久化失败仅本次会话生效
      }
      return next;
    });
  };

  const countOf = (group: Group) => groups.get(group)?.length ?? 0;

  const summary = t("brief.summary")
    .replace("{urgent}", String(countOf("urgent")))
    .replace("{optimize}", String(countOf("optimize")))
    .replace("{healthy}", String(countOf("healthy")))
    + (urgentDollars >= 1
      ? t("brief.summaryRisk").replace("{dollars}", formatDollars(urgentDollars))
      : "");

  if (collapsed) {
    return (
      <button
        onClick={() => togglePanel(false)}
        className="pointer-events-auto flex items-center gap-2 rounded-full bg-white/85 px-4 py-2 shadow-lg ring-1 ring-black/5 backdrop-blur-xl transition hover:bg-white"
      >
        <Lightbulb className="h-4 w-4 text-amber-500" />
        <span className="text-sm font-medium text-neutral-800">{t("brief.title")}</span>
        {countOf("urgent") > 0 ? (
          <span className="rounded-full bg-red-500 px-1.5 text-[11px] font-semibold text-white">
            {countOf("urgent")}
          </span>
        ) : null}
      </button>
    );
  }

  return (
    <div className="pointer-events-auto flex min-h-0 w-[360px] flex-col overflow-hidden rounded-2xl bg-white/85 shadow-lg ring-1 ring-black/5 backdrop-blur-xl">
      {/* Header + summary */}
      <div className="flex items-start justify-between px-4 pb-2 pt-3.5">
        <div>
          <h2 className="flex items-center gap-1.5 text-[15px] font-semibold tracking-tight text-neutral-900">
            <Lightbulb className="h-4 w-4 text-amber-500" />
            {t("brief.title")}
          </h2>
          <p className="mt-0.5 text-[11px] text-neutral-500">{summary}</p>
          <p className="mt-0.5 text-[11px] text-neutral-400">
            {data.date_min} ~ {data.date_max}
          </p>
        </div>
        <button
          onClick={() => togglePanel(true)}
          className="rounded-full p-1 text-neutral-400 transition hover:bg-black/5 hover:text-neutral-600"
          aria-label={t("common.collapse")}
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* Sections */}
      <div className="min-h-0 flex-1 overflow-y-auto border-t border-black/5 px-2 py-1.5">
        {GROUP_ORDER.map((group) => {
          const items = groups.get(group) ?? [];
          if (items.length === 0) return null;
          const isCollapsed = groupsCollapsed[group];
          const expanded = showAll[group];
          const visible = expanded ? items : items.slice(0, TOP_N);
          return (
            <div key={group} className="mb-1">
              <button
                onClick={() => toggleGroup(group)}
                className="flex w-full items-center gap-1 rounded-md px-2 py-1 text-left transition hover:bg-black/[0.03]"
              >
                <ChevronRight
                  className={cn(
                    "h-3 w-3 text-neutral-400 transition-transform",
                    !isCollapsed && "rotate-90",
                  )}
                />
                <span className={cn("text-[11px] font-semibold", GROUP_META[group].text)}>
                  {t(`brief.group.${group}`)}
                </span>
                <span className="text-[11px] text-neutral-400">{items.length}</span>
              </button>
              {!isCollapsed ? (
                <>
                  {visible.map((item) => (
                    <ItemCard key={item.creative_id} item={item} group={group} cppRedLine={cppRedLine} onSelect={onSelect} />
                  ))}
                  {items.length > TOP_N ? (
                    <button
                      onClick={() => setShowAll((prev) => ({ ...prev, [group]: !prev[group] }))}
                      className="w-full rounded-md px-2 py-1 text-left text-[11px] text-neutral-400 transition hover:bg-black/[0.03] hover:text-neutral-600"
                    >
                      {expanded
                        ? t("brief.showLess")
                        : t("brief.showAll").replace("{n}", String(items.length))}
                    </button>
                  ) : null}
                </>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}
