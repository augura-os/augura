import type { ReviewItem } from "@shared";
import { selectionKey } from "./inboxBatch";

/**
 * 收件箱合并提效（P0b）纯逻辑：
 * - 按连通分量把 merge_candidates 归簇（枢纽 creative 的多对一簇裁一次）；
 * - 簇分页（每页 ≤ pageSize 对，不拆簇，单簇超页才整簇独占分页）；
 * - 键盘焦点移动（↑/↓，越界钳位）；
 * - 批量否决的 per-item id（"<creative_id>:<related_creative_id>"）→ 选中 key 映射。
 */

export interface MergeCluster {
  /** 簇内度数最高的 creative（枢纽）；并列时取先出现的 */
  hubId: string;
  hubName: string;
  /** 簇内候选对，保持原队列顺序 */
  items: ReviewItem[];
}

function pairEnds(item: ReviewItem): [string, string] | null {
  if (!item.creative_id || !item.related_creative_id) return null;
  return [item.creative_id, item.related_creative_id];
}

/**
 * 连通分量归簇：以 (creative_id, related_creative_id) 为无向边构图，
 * 同一连通分量的对归为一簇。簇按"最大簇优先、同规模按首对出现顺序"排列，
 * 簇内保持队列原序（后端已按确定性排序给出）。
 */
export function clusterMergePairs(items: ReviewItem[]): MergeCluster[] {
  const parent = new Map<string, string>();
  const find = (id: string): string => {
    let root = parent.get(id) ?? id;
    if (root !== id) {
      root = find(root);
      parent.set(id, root);
    }
    return root;
  };
  const union = (a: string, b: string) => {
    const ra = find(a);
    const rb = find(b);
    if (ra !== rb) parent.set(ra, rb);
  };

  const edges: Array<{ item: ReviewItem; a: string; b: string }> = [];
  for (const item of items) {
    const ends = pairEnds(item);
    if (!ends) continue;
    parent.set(ends[0], find(ends[0]));
    parent.set(ends[1], find(ends[1]));
    union(ends[0], ends[1]);
    edges.push({ item, a: ends[0], b: ends[1] });
  }

  const byRoot = new Map<string, typeof edges>();
  for (const edge of edges) {
    const root = find(edge.a);
    const list = byRoot.get(root) ?? [];
    list.push(edge);
    byRoot.set(root, list);
  }

  const clusters: MergeCluster[] = [];
  for (const group of byRoot.values()) {
    const degree = new Map<string, number>();
    for (const { a, b } of group) {
      degree.set(a, (degree.get(a) ?? 0) + 1);
      degree.set(b, (degree.get(b) ?? 0) + 1);
    }
    let hubId = "";
    let hubDegree = -1;
    for (const { a, b } of group) {
      for (const id of [a, b]) {
        const d = degree.get(id) ?? 0;
        if (d > hubDegree) {
          hubId = id;
          hubDegree = d;
        }
      }
    }
    let hubName = hubId;
    for (const { item, a, b } of group) {
      const name =
        a === hubId ? item.creative_name : b === hubId ? item.related_creative_name : null;
      if (name) {
        hubName = name;
        break;
      }
    }
    clusters.push({ hubId, hubName, items: group.map(({ item }) => item) });
  }
  // 大簇（枢纽多对）排前面，评审先处理一簇一次的判断；同规模保持出现顺序
  clusters.sort((a, b) => b.items.length - a.items.length);
  return clusters;
}

/**
 * 簇分页：每页最多 pageSize 对，不拆簇；单簇超过 pageSize 时整簇独占一页。
 * 返回页数组，每页是簇的列表。
 */
export function paginateClusters(
  clusters: MergeCluster[],
  pageSize: number,
): MergeCluster[][] {
  const pages: MergeCluster[][] = [];
  let current: MergeCluster[] = [];
  let count = 0;
  for (const cluster of clusters) {
    if (count > 0 && count + cluster.items.length > pageSize) {
      pages.push(current);
      current = [];
      count = 0;
    }
    current.push(cluster);
    count += cluster.items.length;
    if (count >= pageSize) {
      pages.push(current);
      current = [];
      count = 0;
    }
  }
  if (current.length > 0) pages.push(current);
  return pages;
}

/**
 * 键盘动作映射（P0b 加固）：←/→/x 只 arm（预备），Enter 才 confirm 执行——
 * arm 与 confirm 不由同一个键完成，按住/key repeat 产生的重复 keydown
 * 无法走完 arm+confirm 两步（repeat 事件在面板层被整体忽略）。
 */
export type MergeKeyAction =
  | "focus-down"
  | "focus-up"
  | "arm-into-left"
  | "arm-into-right"
  | "arm-not-same"
  | "confirm-armed"
  | "cancel-armed";

export function mergeKeyAction(key: string): MergeKeyAction | null {
  switch (key) {
    case "ArrowDown":
      return "focus-down";
    case "ArrowUp":
      return "focus-up";
    case "ArrowLeft":
      return "arm-into-left";
    case "ArrowRight":
      return "arm-into-right";
    case "x":
      return "arm-not-same";
    case "Enter":
      return "confirm-armed";
    case "Escape":
      return "cancel-armed";
    default:
      return null;
  }
}

/** 键盘焦点移动：↓ = +1 / ↑ = -1，越界钳位（不回绕，顶/底再按不动） */
export function moveFocusIndex(index: number, delta: number, length: number): number {
  if (length <= 0) return -1;
  const next = index + delta;
  if (next < 0) return 0;
  if (next >= length) return length - 1;
  return next;
}

/** 批量否决（POST /graph/similar/bulk）的 per-item id */
export function pairId(item: ReviewItem): string | null {
  if (!item.creative_id || !item.related_creative_id) return null;
  return `${item.creative_id}:${item.related_creative_id}`;
}

/**
 * 批量否决结果的 item id（"<creative_id>:<related_creative_id>"）→ 选中 key。
 * 与 keyByItemId 互补：后者按单 creative 映射，merge 对必须按合成 id，
 * 否则同一枢纽 creative 的多对会互相串扰。
 */
export function keyByPairId(items: ReviewItem[]): Map<string, string> {
  const map = new Map<string, string>();
  for (const item of items) {
    const id = pairId(item);
    if (id) map.set(id, selectionKey(item.kind, item));
  }
  return map;
}
