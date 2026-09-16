import { memo } from "react";
import { Handle, Position, useStore, type NodeProps } from "reactflow";
import { NODE_DOT_CLASS, type FlowNodeData } from "../../lib/graph-layout";
import { useGraphStore } from "../../stores/graphStore";
import { cn } from "../../lib/utils";

// 低于此缩放级别切换为紧凑行（LOD）：大图全景时几百张完整卡片的
// DOM/repaint 面积是拖动卡顿的主要来源，缩略行足够辨认列与相对位置。
const LOD_ZOOM_THRESHOLD = 0.45;

/**
 * Memoized graph node card. Selection rings and the zoom LOD are subscribed
 * inside the component (boolean selectors), so a drag frame or a selection
 * change only re-renders the handful of nodes whose derived state actually
 * flipped — siblings keep their memoized render.
 */
function GraphCardNodeInner({ id, data }: NodeProps<FlowNodeData>) {
  const isSelected = useGraphStore((state) => state.selectedNodeId === id);
  const isMergeSelected = useGraphStore((state) => state.mergeSelection.includes(id));
  // transform[2] is the current zoom; booleanized so the component only
  // re-renders when the LOD threshold is crossed, not on every zoom frame.
  const compact = useStore((state) => state.transform[2] < LOD_ZOOM_THRESHOLD);

  return (
    <div
      title={data.rawLabel}
      className={cn(
        "rounded-lg border border-[#e5e5e5] bg-white transition-shadow",
        isSelected && "ring-1 ring-neutral-900",
        isMergeSelected && "ring-2 ring-blue-500",
        compact ? "px-2 py-1" : "w-[240px] px-2.5 py-2 text-xs",
      )}
    >
      {compact ? (
        <div className="flex items-center gap-1.5 text-left">
          <span className={cn("h-2 w-2 shrink-0 rounded-full", NODE_DOT_CLASS[data.nodeType])} />
          <span className="max-w-[140px] truncate text-[11px] leading-tight text-neutral-800">
            {data.rawLabel}
          </span>
          {data.isNew ? (
            <span className="shrink-0 animate-pulse rounded bg-emerald-100 px-1 py-px text-[9px] font-semibold text-emerald-700">
              NEW
            </span>
          ) : null}
        </div>
      ) : (
        <div className="flex items-center gap-2 text-left">
          <span className={cn("h-2 w-2 shrink-0 rounded-full", NODE_DOT_CLASS[data.nodeType])} />
          <span className="min-w-0">
            <span className="line-clamp-2 block break-all text-xs font-medium leading-tight text-neutral-900">
              {data.rawLabel}
            </span>
            <span className="block text-[10px] uppercase tracking-wide text-neutral-400">
              {data.nodeType}
            </span>
          </span>
          {data.isNew ? (
            <span className="ml-auto shrink-0 animate-pulse rounded bg-emerald-100 px-1.5 py-0.5 text-[9px] font-semibold text-emerald-700">
              NEW
            </span>
          ) : null}
        </div>
      )}
      <Handle type="target" position={Position.Left} isConnectable={false} />
      <Handle type="source" position={Position.Right} isConnectable={false} />
    </div>
  );
}

export const GraphCardNode = memo(GraphCardNodeInner);
