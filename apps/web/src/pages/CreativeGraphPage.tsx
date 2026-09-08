import { useCallback, useEffect, useState } from "react";
import ReactFlow, {
  Background,
  Controls,
  applyEdgeChanges,
  applyNodeChanges,
  type Edge,
  type EdgeChange,
  type Node,
  type NodeChange,
  type NodeMouseHandler,
} from "reactflow";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useGraph } from "../hooks/useGraph";
import { fetchRecentCreativeIds } from "../services/api";
import {
  buildFlowGraph,
  NODE_DOT_CLASS,
  type FlowNodeData,
  type GraphDensity,
} from "../lib/graph-layout";
import { useGraphStore } from "../stores/graphStore";
import { GraphCardNode } from "../components/graph/GraphCardNode";
import { GraphNodePanel } from "../components/graph/GraphNodePanel";
import { MergeBar } from "../components/graph/MergeBar";
import { RecommendationPanel } from "../components/graph/RecommendationPanel";
import { ReviewInboxPanel } from "../components/graph/ReviewInboxPanel";
import { Skeleton } from "../components/ui/skeleton";
import { cn } from "../lib/utils";
import type { GraphNodeType } from "@shared";

const LEGEND: Array<{ type: GraphNodeType; label: string }> = [
  { type: "dna", label: "DNA" },
  { type: "creative", label: "Creative" },
  { type: "variant", label: "Variant" },
  { type: "asset", label: "Asset" },
  { type: "tag", label: "Tag" },
];

// 超过这个节点数认定为大图：不再 fitView 全图（会缩成细条），用固定初始视口
const LARGE_GRAPH_NODE_COUNT = 60;

// 节点总数超过该阈值时默认进紧凑密度（asset/tag 列不进图）
const AUTO_COMPACT_NODE_THRESHOLD = 800;

// 用户手动切换密度后持久化，之后不再随节点数自动切换
const DENSITY_STORAGE_KEY = "augura.graph.density";

// 模块级常量，保证 React Flow 的 nodeTypes 引用稳定（每次渲染新建会导致
// 所有节点重挂载，抵消 memo 的收益）
const nodeTypes = { card: GraphCardNode };

function readStoredDensity(): GraphDensity | null {
  try {
    const stored = localStorage.getItem(DENSITY_STORAGE_KEY);
    return stored === "full" || stored === "compact" ? stored : null;
  } catch {
    return null;
  }
}

export default function CreativeGraphPage() {
  const { data, isLoading, isError, error } = useGraph();
  const selectedNodeId = useGraphStore((state) => state.selectedNodeId);
  const selectNode = useGraphStore((state) => state.selectNode);
  const toggleMergeSelection = useGraphStore((state) => state.toggleMergeSelection);

  // Nodes/edges live in local state so React Flow change events (drag,
  // select) apply incrementally instead of rebuilding all 160+ node objects
  // on every render — the previous controlled-without-onNodesChange setup
  // re-synced the whole array per render and made panning/dragging janky.
  const [flowNodes, setFlowNodes] = useState<Node<FlowNodeData>[]>([]);
  const [flowEdges, setFlowEdges] = useState<Edge[]>([]);

  // 密度：完整（五列）/ 紧凑（DNA→Creative→Variant 三列）。
  // 用户手动选择优先（并持久化）；未手动选择时大图自动进紧凑。
  const [densityOverride, setDensityOverride] = useState<GraphDensity | null>(
    readStoredDensity,
  );
  // 已归档 creative 默认不进图；打开开关才显示
  const [showArchived, setShowArchived] = useState(false);
  const autoCompact = (data?.nodes.length ?? 0) > AUTO_COMPACT_NODE_THRESHOLD;
  const density: GraphDensity = densityOverride ?? (autoCompact ? "compact" : "full");

  const chooseDensity = (next: GraphDensity) => {
    setDensityOverride(next);
    try {
      localStorage.setItem(DENSITY_STORAGE_KEY, next);
    } catch {
      // localStorage 不可用时仅本次会话生效
    }
  };

  // 近 48h 新建的 Creative 打 NEW 徽章（上传后能在图谱上被看见）
  const { data: recentIds } = useQuery({
    queryKey: ["recent-creative-ids"],
    queryFn: () => fetchRecentCreativeIds(48),
  });

  useEffect(() => {
    if (!data) {
      setFlowNodes([]);
      setFlowEdges([]);
      return;
    }
    const built = buildFlowGraph(data, new Set(recentIds ?? []), density, showArchived);
    setFlowNodes(built.nodes);
    setFlowEdges(built.edges);
  }, [data, recentIds, density, showArchived]);

  const onNodesChange = useCallback(
    (changes: NodeChange[]) =>
      setFlowNodes((current) => applyNodeChanges(changes, current)),
    [],
  );
  const onEdgesChange = useCallback(
    (changes: EdgeChange[]) =>
      setFlowEdges((current) => applyEdgeChanges(changes, current)),
    [],
  );

  // Selection rings render inside GraphCardNode (per-id store subscription),
  // so nodes flow straight through — dragging only replaces the dragged
  // node's object identity and memo keeps every other card untouched.
  const selectedDto = data?.nodes.find((node) => node.id === selectedNodeId);

  const onNodeClick: NodeMouseHandler = (event, node) => {
    if (event.shiftKey && node.data.nodeType === "creative") {
      toggleMergeSelection(node.id);
      return;
    }
    selectNode(node.id);
  };

  return (
    <div className="flex h-full">
      <div className="relative flex-1">
        {isLoading ? (
          <div className="space-y-3 p-8">
            <Skeleton className="h-5 w-48" />
            <Skeleton className="h-[480px] w-full" />
          </div>
        ) : isError ? (
          <div className="flex h-full items-center justify-center">
            <p className="text-sm text-red-600">
              {error instanceof Error ? error.message : "Failed to load graph"}
            </p>
          </div>
        ) : !data || data.nodes.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-2">
            <p className="text-sm text-neutral-500">No creatives yet.</p>
            <Link
              to="/upload"
              className="text-sm font-medium text-neutral-900 underline underline-offset-2 hover:text-neutral-600"
            >
              Upload your first asset →
            </Link>
          </div>
        ) : (
          <ReactFlow
            nodes={flowNodes}
            edges={flowEdges}
            nodeTypes={nodeTypes}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onNodeClick={onNodeClick}
            onPaneClick={() => selectNode(null)}
            {...(flowNodes.length > LARGE_GRAPH_NODE_COUNT
              ? // 大图（几百节点、画布近万像素高）：fitView 会把缩放压到 0.1
                // 缩成细条。改用固定初始视口——左上角 75% 缩放直接可读，
                // 用户用左下角 fit 按钮自行全览。
                { defaultViewport: { x: 60, y: 24, zoom: 0.75 } }
              : { fitView: true, fitViewOptions: { padding: 0.2 } })}
            minZoom={0.2}
            onlyRenderVisibleElements
            proOptions={{ hideAttribution: true }}
          >
            <Background gap={24} color="#f5f5f5" />
            <Controls showInteractive={false} />
            {/* MiniMap removed: it re-rendered all nodes every pan/zoom frame. */}
          </ReactFlow>
        )}

        {/* Legend + density toggle + hint；与今日建议同容器纵向排列，永不重叠 */}
        <div className="pointer-events-none absolute left-4 top-4 flex flex-col items-start gap-2">
          <div className="rounded-lg border border-[#e5e5e5] bg-white px-3 py-2">
          <div className="flex items-center gap-3">
            {LEGEND.map(({ type, label }) => (
              <span key={type} className="flex items-center gap-1.5 text-xs text-neutral-600">
                <span className={cn("h-2 w-2 rounded-full", NODE_DOT_CLASS[type])} />
                {label}
              </span>
            ))}
          </div>
          <div className="pointer-events-auto mt-2 flex items-center gap-1.5 border-t border-[#f0f0f0] pt-2">
            <span className="text-[11px] text-neutral-400">密度</span>
            {(["full", "compact"] as const).map((option) => (
              <button
                key={option}
                type="button"
                onClick={() => chooseDensity(option)}
                className={cn(
                  "rounded px-1.5 py-0.5 text-[11px]",
                  density === option
                    ? "bg-neutral-900 font-medium text-white"
                    : "text-neutral-500 hover:bg-neutral-100",
                )}
              >
                {option === "full" ? "完整" : "紧凑"}
              </button>
            ))}
            {autoCompact && densityOverride === null ? (
              <span className="text-[10px] text-neutral-400">节点多，已自动切紧凑</span>
            ) : null}
            <span className="mx-1 h-3 w-px bg-[#f0f0f0]" />
            <button
              type="button"
              onClick={() => setShowArchived((current) => !current)}
              className={cn(
                "rounded px-1.5 py-0.5 text-[11px]",
                showArchived
                  ? "bg-neutral-900 font-medium text-white"
                  : "text-neutral-500 hover:bg-neutral-100",
              )}
            >
              显示已归档
            </button>
          </div>
          <p className="mt-1 text-[11px] text-neutral-400">
            Click a node to inspect · Shift-click creatives to select for merge
          </p>
          </div>
          {/* 今日建议（与图例同列，flex 排列不重叠） */}
          <RecommendationPanel onSelect={selectNode} />
        </div>

        {/* Merge bar */}
        <div className="pointer-events-none absolute inset-x-0 top-4 flex justify-center">
          {data ? <MergeBar graph={data} /> : null}
        </div>

        {/* Human review inbox (GPT dialogue vol.7 review queue) */}
        <ReviewInboxPanel onSelectCreative={selectNode} />
      </div>

      {selectedDto && data ? (
        <GraphNodePanel node={selectedDto} graph={data} onClose={() => selectNode(null)} />
      ) : null}
    </div>
  );
}
