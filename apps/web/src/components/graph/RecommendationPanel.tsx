import { useMemo, useState } from "react";
import { ChevronRight, Lightbulb, X } from "lucide-react";
import type { RecommendationAction, RecommendationItem } from "@shared";
import { useRecommendations } from "../../hooks/useRecommendations";
import { useMetricConfig } from "../../hooks/useMetricConfig";
import { cn } from "../../lib/utils";

type Group = "urgent" | "optimize" | "healthy";

const GROUP_META: Record<
  Group,
  { label: string; bar: string; text: string; actions: RecommendationAction[] }
> = {
  urgent: {
    label: "需要处理",
    bar: "bg-red-500",
    text: "text-red-600",
    actions: ["PAUSE", "ARCHIVE"],
  },
  optimize: {
    label: "可优化",
    bar: "bg-amber-500",
    text: "text-amber-600",
    actions: ["ITERATE"],
  },
  healthy: {
    label: "健康",
    bar: "bg-emerald-500",
    text: "text-emerald-600",
    actions: ["KEEP"],
  },
};
const GROUP_ORDER: Group[] = ["urgent", "optimize", "healthy"];

const COLLAPSE_KEY = "augura-rec-panel-collapse-v1";

function loadCollapsed(): Record<Group, boolean> {
  try {
    return { optimize: true, healthy: true, urgent: false, ...JSON.parse(localStorage.getItem(COLLAPSE_KEY) ?? "{}") };
  } catch {
    return { urgent: false, optimize: true, healthy: true };
  }
}

function fmtCpp(
  item: RecommendationItem,
  cppRedLine: number,
): { text: string; tone: string } {
  const { cpp, payers } = item.metrics;
  if (payers === 0 && item.metrics.spend >= 50) return { text: "0付费", tone: "text-red-600" };
  if (cpp === null) return { text: "—", tone: "text-neutral-400" };
  if (cpp >= cppRedLine) return { text: `$${cpp.toFixed(0)}`, tone: "text-red-600 font-semibold" };
  return { text: `$${cpp.toFixed(0)}`, tone: "text-neutral-500" };
}

function ItemRow({
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
  const cpp = fmtCpp(item, cppRedLine);
  const tooltip = [
    ...item.reasons,
    `消耗 $${item.metrics.spend.toLocaleString()} · 付费 ${item.metrics.payers}` +
      (item.metrics.roas !== null ? ` · D1 Roas ${(item.metrics.roas * 100).toFixed(1)}%` : ""),
  ].join("\n");
  return (
    <button
      onClick={() => onSelect(`creative:${item.creative_id}`)}
      title={tooltip}
      className="group flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left transition hover:bg-black/[0.04]"
    >
      <span className={cn("h-6 w-0.5 shrink-0 rounded-full", GROUP_META[group].bar)} />
      <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-neutral-800">
        {item.creative_name}
      </span>
      <span className={cn("shrink-0 text-xs tabular-nums", cpp.tone)}>{cpp.text}</span>
    </button>
  );
}

export function RecommendationPanel({
  onSelect,
}: {
  onSelect: (creativeNodeId: string) => void;
}) {
  const { data } = useRecommendations();
  const { thresholds } = useMetricConfig();
  const cppRedLine = thresholds.cpp_red_line;
  const [collapsed, setCollapsed] = useState(true);
  const [groupsCollapsed, setGroupsCollapsed] = useState<Record<Group, boolean>>(loadCollapsed);

  const groups = useMemo(() => {
    const map = new Map<Group, RecommendationItem[]>();
    for (const group of GROUP_ORDER) map.set(group, []);
    for (const item of data?.items ?? []) {
      const group = GROUP_ORDER.find((g) => GROUP_META[g].actions.includes(item.action));
      if (group) map.get(group)?.push(item);
    }
    return map;
  }, [data]);

  if (!data || data.items.length === 0) return null;

  const toggleGroup = (group: Group) => {
    setGroupsCollapsed((prev) => {
      const next = { ...prev, [group]: !prev[group] };
      localStorage.setItem(COLLAPSE_KEY, JSON.stringify(next));
      return next;
    });
  };

  const countOf = (group: Group) => groups.get(group)?.length ?? 0;

  if (collapsed) {
    return (
      <button
        onClick={() => setCollapsed(false)}
        className="pointer-events-auto flex items-center gap-2 rounded-full bg-white/85 px-4 py-2 shadow-lg ring-1 ring-black/5 backdrop-blur-xl transition hover:bg-white"
      >
        <Lightbulb className="h-4 w-4 text-amber-500" />
        <span className="text-sm font-medium text-neutral-800">今日建议</span>
        {countOf("urgent") > 0 ? (
          <span className="rounded-full bg-red-500 px-1.5 text-[11px] font-semibold text-white">
            {countOf("urgent")}
          </span>
        ) : null}
      </button>
    );
  }

  return (
    <div className="pointer-events-auto flex max-h-[calc(100%-7rem)] w-[340px] flex-col overflow-hidden rounded-2xl bg-white/85 shadow-lg ring-1 ring-black/5 backdrop-blur-xl">
      {/* Header + pulse */}
      <div className="flex items-start justify-between px-4 pb-2 pt-3.5">
        <div>
          <h2 className="flex items-center gap-1.5 text-[15px] font-semibold tracking-tight text-neutral-900">
            <Lightbulb className="h-4 w-4 text-amber-500" />
            今日建议
          </h2>
          <p className="mt-0.5 text-[11px] text-neutral-400">
            {data.date_min} ~ {data.date_max}
          </p>
        </div>
        <button
          onClick={() => setCollapsed(true)}
          className="rounded-full p-1 text-neutral-400 transition hover:bg-black/5 hover:text-neutral-600"
          aria-label="收起"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
      <div className="flex gap-3 border-y border-black/5 bg-white/40 px-4 py-2">
        {GROUP_ORDER.map((group) => (
          <button
            key={group}
            onClick={() => toggleGroup(group)}
            className="flex items-baseline gap-1 transition hover:opacity-80"
            title={`展开/折叠「${GROUP_META[group].label}」`}
          >
            <span className={cn("text-base font-semibold tabular-nums", GROUP_META[group].text)}>
              {countOf(group)}
            </span>
            <span className="text-[11px] text-neutral-500">{GROUP_META[group].label}</span>
          </button>
        ))}
      </div>

      {/* Sections */}
      <div className="flex-1 overflow-y-auto px-2 py-1.5">
        {GROUP_ORDER.map((group) => {
          const items = groups.get(group) ?? [];
          if (items.length === 0) return null;
          const isCollapsed = groupsCollapsed[group];
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
                  {GROUP_META[group].label}
                </span>
                <span className="text-[11px] text-neutral-400">{items.length}</span>
              </button>
              {!isCollapsed
                ? items.map((item) => (
                    <ItemRow key={item.creative_id} item={item} group={group} cppRedLine={cppRedLine} onSelect={onSelect} />
                  ))
                : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}
