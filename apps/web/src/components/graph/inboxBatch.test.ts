import { describe, expect, it } from "vitest";
import type { ReviewItem } from "@shared";
import type { BatchItemResult } from "../../services/api";
import {
  keyByItemId,
  reconcileSelection,
  reduceBatchResults,
  selectionKey,
} from "./inboxBatch";

function makeItem(partial: Partial<ReviewItem>): ReviewItem {
  return {
    kind: "dna_unassigned",
    title: "",
    reason: "",
    creative_id: null,
    creative_name: null,
    asset_id: null,
    related_creative_id: null,
    related_creative_name: null,
    preview_asset_id: null,
    related_preview_asset_id: null,
    derivation_id: null,
    source_label: null,
    target_label: null,
    factor: null,
    suggestion: null,
    suggestion_votes: null,
    suggestion_reason: null,
    suggested_dna_id: null,
    family: null,
    rule_keyword: null,
    ...partial,
  };
}

function makeFamilyItem(suggestionId: string, creativeId: string | null = null): ReviewItem {
  return makeItem({
    kind: "family_bootstrap",
    creative_id: creativeId,
    family: {
      suggestion_id: suggestionId,
      name: "测试家族",
      core_mechanic: "",
      hook_prototype: "",
      narrative_structure: "",
      keywords: [],
      members: [],
      existing_dna_code: null,
    },
  });
}

describe("selectionKey（契约 §2.5 四元组）", () => {
  it("serializes (kind, creative_id, related_creative_id, suggestion_id) with '-' for nulls", () => {
    const item = makeItem({ kind: "dna_unassigned", creative_id: "c-1" });
    expect(selectionKey("dna_unassigned", item)).toBe("dna_unassigned|c-1|-|-");
  });

  it("uses family.suggestion_id as the fourth tuple field", () => {
    const item = makeFamilyItem("s-9");
    expect(selectionKey("family_bootstrap", item)).toBe("family_bootstrap|-|-|s-9");
  });

  it("distinguishes items that share creative_id via related_creative_id", () => {
    const left = makeItem({ kind: "merge_candidate", creative_id: "c-1", related_creative_id: "c-2" });
    const right = makeItem({ kind: "merge_candidate", creative_id: "c-1", related_creative_id: "c-3" });
    expect(selectionKey("merge_candidate", left)).not.toBe(selectionKey("merge_candidate", right));
  });

  it("distinguishes kinds for the same creative", () => {
    const item = makeItem({ creative_id: "c-1" });
    expect(selectionKey("dna_unassigned", item)).not.toBe(
      selectionKey("low_confidence", item),
    );
  });
});

describe("reconcileSelection（refetch 集合对账）", () => {
  it("drops keys whose items vanished from the queue", () => {
    const alive = makeItem({ kind: "dna_unassigned", creative_id: "c-1" });
    const selected = new Set([
      selectionKey("dna_unassigned", alive),
      "dna_unassigned|c-gone|-|-",
    ]);
    const next = reconcileSelection(selected, [alive]);
    expect([...next]).toEqual([selectionKey("dna_unassigned", alive)]);
  });

  it("keeps everything when the queue is unchanged", () => {
    const items = [
      makeItem({ kind: "dna_unassigned", creative_id: "c-1" }),
      makeFamilyItem("s-1"),
    ];
    const selected = new Set(items.map((item) => selectionKey(item.kind, item)));
    const next = reconcileSelection(selected, items);
    expect(next).toEqual(selected);
  });

  it("empties the selection when the queue drains", () => {
    const selected = new Set(["dna_unassigned|c-1|-|-"]);
    expect(reconcileSelection(selected, []).size).toBe(0);
  });
});

describe("keyByItemId", () => {
  it("maps both creative_id and family suggestion_id to their selection keys", () => {
    const creative = makeItem({ kind: "dna_unassigned", creative_id: "c-1" });
    const family = makeFamilyItem("s-1");
    const map = keyByItemId([creative, family]);
    expect(map.get("c-1")).toBe("dna_unassigned|c-1|-|-");
    expect(map.get("s-1")).toBe("family_bootstrap|-|-|s-1");
  });
});

describe("reduceBatchResults（per-item 归约）", () => {
  it("success and skipped go to removed, failed keeps reason and message", () => {
    const results: BatchItemResult[] = [
      { id: "a", status: "success", reason: null, message: null },
      { id: "b", status: "skipped", reason: "already_done", message: null },
      { id: "c", status: "failed", reason: "conflict", message: "已被合并家族占用" },
    ];
    const { removed, failed } = reduceBatchResults(results);
    expect(removed).toEqual(["a", "b"]);
    expect(failed).toEqual({ c: { reason: "conflict", message: "已被合并家族占用" } });
  });

  it("defaults missing reason/message to null on failed items", () => {
    const { failed } = reduceBatchResults([
      { id: "x", status: "failed", reason: null },
    ]);
    expect(failed.x).toEqual({ reason: null, message: null });
  });

  it("all-success batch leaves failed empty", () => {
    const { removed, failed } = reduceBatchResults([
      { id: "a", status: "success", reason: null },
      { id: "b", status: "success", reason: null },
    ]);
    expect(removed).toEqual(["a", "b"]);
    expect(Object.keys(failed)).toHaveLength(0);
  });

  it("empty_family skipped items are removed, not marked failed", () => {
    const { removed, failed } = reduceBatchResults([
      { id: "s-1", status: "skipped", reason: "empty_family", message: "空族" },
    ]);
    expect(removed).toEqual(["s-1"]);
    expect(failed).toEqual({});
  });
});
