import { Position, type Edge, type Node } from "reactflow";
import type { GraphEdgeDTO, GraphNodeDTO, GraphNodeType, GraphResponse } from "@shared";

export interface FlowNodeData {
  rawLabel: string;
  nodeType: GraphNodeType;
  refId: string;
  isNew?: boolean;
}

/**
 * Graph density:
 * - "full": all five columns (dna → creative → variant → asset → tag).
 * - "compact": only dna → creative → variant nodes plus the edges between
 *   them (HAS_CREATIVE / HAS_VARIANT / SIMILAR_TO / DERIVED_FROM); asset and
 *   tag nodes are left out of the canvas entirely (details live in the side
 *   panel, which reads from the graph DTO).
 */
export type GraphDensity = "full" | "compact";

const COMPACT_NODE_TYPES: ReadonlySet<GraphNodeType> = new Set(["dna", "creative", "variant"]);
// DERIVED_FROM exists in the backend edge vocabulary but not yet in the
// shared TS union — the set is typed as string so it still matches.
const COMPACT_EDGE_TYPES: ReadonlySet<string> = new Set([
  "HAS_CREATIVE",
  "HAS_VARIANT",
  "SIMILAR_TO",
  "DERIVED_FROM",
]);

export const NODE_DOT_CLASS: Record<GraphNodeType, string> = {
  dna: "bg-rose-500",
  creative: "bg-blue-500",
  variant: "bg-violet-500",
  asset: "bg-emerald-500",
  tag: "bg-amber-500",
};

const COLUMN_X: Record<GraphNodeType, number> = {
  dna: 0,
  creative: 300,
  variant: 600,
  asset: 900,
  tag: 1200,
};

const Y_STEP = 84;

/** Children of a node, optionally filtered by edge type. */
export function childrenOf(
  graph: GraphResponse,
  id: string,
  edgeType?: GraphEdgeDTO["type"],
): GraphNodeDTO[] {
  return graph.edges
    .filter((edge) => edge.source === id && (!edgeType || edge.type === edgeType))
    .map((edge) => graph.nodes.find((node) => node.id === edge.target))
    .filter((node): node is GraphNodeDTO => Boolean(node));
}

/** Parent of a node (first incoming edge of the given type). */
export function parentOf(
  graph: GraphResponse,
  id: string,
  edgeType?: GraphEdgeDTO["type"],
): GraphNodeDTO | undefined {
  const edge = graph.edges.find(
    (candidate) => candidate.target === id && (!edgeType || candidate.type === edgeType),
  );
  return edge ? graph.nodes.find((node) => node.id === edge.source) : undefined;
}

/**
 * Simple client-side layered layout:
 * dna column → creative column → variant column → asset column → tag column.
 * Nodes are visited depth-first from each DNA family (then each unassigned
 * creative) so rows within a column stay grouped next to their parents.
 *
 * Card visuals (label, badges, selection rings) live in GraphCardNode — this
 * builder only emits stable node data so unchanged nodes keep their object
 * identity across React Flow change events.
 */
export function buildFlowGraph(
  graph: GraphResponse,
  recentCreativeIds?: ReadonlySet<string>,
  density: GraphDensity = "full",
  showArchived = false,
): {
  nodes: Node<FlowNodeData>[];
  edges: Edge[];
} {
  const compact = density === "compact";
  const ordered: GraphNodeDTO[] = [];
  const seen = new Set<string>();

  // 已归档 creative 默认整棵子树不进图（creative → variant → asset → tag），
  // 「显示已归档」开关打开时才渲染
  const excluded = new Set<string>();
  if (!showArchived) {
    const archivedCreativeIds = new Set(
      graph.nodes
        .filter((node) => node.type === "creative" && node.lifecycle_state === "archived")
        .map((node) => node.id),
    );
    if (archivedCreativeIds.size > 0) {
      const queue = [...archivedCreativeIds];
      for (const id of archivedCreativeIds) excluded.add(id);
      // 只沿结构边（HAS_VARIANT/HAS_ASSET/HAS_TAG）向下排除；
      // SIMILAR_TO 这类横向边不能拖对方 creative 一起隐藏
      const STRUCTURAL = new Set(["HAS_VARIANT", "HAS_ASSET", "HAS_TAG"]);
      while (queue.length > 0) {
        const current = queue.pop()!;
        for (const edge of graph.edges) {
          if (
            STRUCTURAL.has(edge.type) &&
            edge.source === current &&
            !excluded.has(edge.target)
          ) {
            excluded.add(edge.target);
            queue.push(edge.target);
          }
        }
      }
    }
  }

  const push = (node: GraphNodeDTO) => {
    if (
      !seen.has(node.id) &&
      !excluded.has(node.id) &&
      (!compact || COMPACT_NODE_TYPES.has(node.type))
    ) {
      seen.add(node.id);
      ordered.push(node);
    }
  };

  const visitAsset = (asset: GraphNodeDTO) => {
    push(asset);
    for (const tag of childrenOf(graph, asset.id, "HAS_TAG")) push(tag);
  };

  const visitVariant = (variant: GraphNodeDTO) => {
    push(variant);
    if (compact) return;
    for (const asset of childrenOf(graph, variant.id, "HAS_ASSET")) visitAsset(asset);
  };

  const visitCreative = (creative: GraphNodeDTO) => {
    push(creative);
    for (const variant of childrenOf(graph, creative.id, "HAS_VARIANT")) visitVariant(variant);
  };

  const visitDna = (dna: GraphNodeDTO) => {
    push(dna);
    for (const creative of childrenOf(graph, dna.id, "HAS_CREATIVE")) visitCreative(creative);
  };

  const dnas = graph.nodes
    .filter((node) => node.type === "dna")
    .sort((a, b) => a.label.localeCompare(b.label));
  dnas.forEach(visitDna);

  // Creatives without a DNA assignment form their own roots.
  const creatives = graph.nodes
    .filter((node) => node.type === "creative")
    .sort((a, b) => a.label.localeCompare(b.label));
  creatives.forEach(visitCreative);

  // Orphans (nodes not reachable from any root) appended at the end.
  for (const node of graph.nodes) push(node);

  const columnRow: Record<GraphNodeType, number> = { dna: 0, creative: 0, variant: 0, asset: 0, tag: 0 };

  const nodes: Node<FlowNodeData>[] = ordered.map((node) => {
    const row = columnRow[node.type];
    columnRow[node.type] += 1;
    const isNew =
      node.type === "creative" && recentCreativeIds?.has(node.ref_id) === true;
    return {
      id: node.id,
      // Custom memoized card node (GraphCardNode) — renders label, badges and
      // selection rings internally so dragging/selection never rebuilds
      // sibling node objects.
      type: "card",
      position: { x: COLUMN_X[node.type], y: row * Y_STEP },
      data: {
        rawLabel: node.label,
        nodeType: node.type,
        refId: node.ref_id,
        isNew,
      },
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
      draggable: true,
    };
  });

  const nodeIds = new Set(nodes.map((node) => node.id));
  const edges: Edge[] = graph.edges
    .filter(
      (edge) =>
        !excluded.has(edge.source) &&
        !excluded.has(edge.target) &&
        (!compact ||
          (COMPACT_EDGE_TYPES.has(edge.type) &&
            nodeIds.has(edge.source) &&
            nodeIds.has(edge.target))),
    )
    .map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      // No SVG arrow markers: their rasterization cost explodes with zoom level
      // and made panning janky when zoomed in. Direction is already conveyed
      // by the left-to-right column layout.
      // Observation pairs (SIMILAR_TO) render as dashed amber links between creatives.
      style:
        edge.type === "SIMILAR_TO"
          ? { stroke: "#f59e0b", strokeWidth: 1.5, strokeDasharray: "6 4" }
          : { stroke: "#d4d4d4", strokeWidth: 1 },
    }));

  return { nodes, edges };
}
