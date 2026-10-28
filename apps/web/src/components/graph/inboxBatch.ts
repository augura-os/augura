import type { ReviewItem, ReviewKind } from "@shared";
import type { BatchItemReason, BatchItemResult } from "../../services/api";

/**
 * 收件箱批量操作的纯逻辑（契约 §2.5）：
 * - 选中 key = (kind, creative_id, related_creative_id, suggestion_id) 四元组
 *   序列化字符串，null 字段用 "-"（ReviewItem 无自增 id，四元组即业务主键）。
 * - 队列 refetch 后做集合对账：已消失的 key 自动丢弃（防"选中 A 提交 B"）。
 * - 批量提交后按 per-item 结果增量更新：success/skipped 移除，failed 保留标红。
 */

/** uuid 不含 "|"，直接拼接即无歧义 */
export function selectionKey(kind: ReviewKind, item: ReviewItem): string {
  const suggestionId =
    item.family?.suggestion_id ?? item.rule_keyword?.suggestion_id ?? null;
  return [kind, item.creative_id, item.related_creative_id, suggestionId]
    .map((value) => value ?? "-")
    .join("|");
}

/** 集合对账：只保留仍存在于当前队列条目中的选中 key */
export function reconcileSelection(
  selected: ReadonlySet<string>,
  items: ReviewItem[],
): Set<string> {
  const alive = new Set(items.map((item) => selectionKey(item.kind, item)));
  return new Set([...selected].filter((key) => alive.has(key)));
}

/**
 * per-item 结果的 item id → 选中 key 映射。
 * confirm-families 回 suggestion_id，batch-assign-dna 回 creative_id，
 * 两者都在这张表里命中（同一 id 不可能同时是 creative 和 suggestion）。
 */
export function keyByItemId(items: ReviewItem[]): Map<string, string> {
  const map = new Map<string, string>();
  for (const item of items) {
    const id =
      item.creative_id ??
      item.family?.suggestion_id ??
      item.rule_keyword?.suggestion_id ??
      null;
    if (id) map.set(id, selectionKey(item.kind, item));
  }
  return map;
}

export interface BatchFailure {
  reason: BatchItemReason;
  message: string | null;
}

export interface BatchReduction {
  /** success / skipped 的 item id——对应卡片增量移除 */
  removed: string[];
  /** failed 的 item id → 原因——对应卡片保留并标红 */
  failed: Record<string, BatchFailure>;
}

/** 契约 §2.1 per-item 归约：success/skipped 移除、failed 保留并带原因 */
export function reduceBatchResults(results: BatchItemResult[]): BatchReduction {
  const removed: string[] = [];
  const failed: Record<string, BatchFailure> = {};
  for (const result of results) {
    if (result.status === "failed") {
      failed[result.id] = {
        reason: result.reason ?? null,
        message: result.message ?? null,
      };
    } else {
      removed.push(result.id);
    }
  }
  return { removed, failed };
}
