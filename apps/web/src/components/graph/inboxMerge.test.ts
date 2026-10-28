import { describe, expect, it } from "vitest";
import type { ReviewItem } from "@shared";
import {
  clusterMergePairs,
  keyByPairId,
  mergeKeyAction,
  moveFocusIndex,
  paginateClusters,
  pairId,
} from "./inboxMerge";

function makeMergeItem(
  creativeId: string,
  relatedId: string,
  name?: string,
  relatedName?: string,
): ReviewItem {
  return {
    kind: "merge_candidate",
    title: "",
    reason: "",
    creative_id: creativeId,
    creative_name: name ?? null,
    asset_id: null,
    related_creative_id: relatedId,
    related_creative_name: relatedName ?? null,
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
  };
}

describe("clusterMergePairs（连通分量归簇）", () => {
  it("枢纽 creative 的多对归为一簇，hub 取度数最高者", () => {
    const items = [
      makeMergeItem("hub", "a", "枢纽素材", "素材A"),
      makeMergeItem("hub", "b", "枢纽素材", "素材B"),
      makeMergeItem("hub", "c", "枢纽素材", "素材C"),
    ];
    const clusters = clusterMergePairs(items);
    expect(clusters).toHaveLength(1);
    expect(clusters[0].hubId).toBe("hub");
    expect(clusters[0].hubName).toBe("枢纽素材");
    expect(clusters[0].items).toHaveLength(3);
  });

  it("孤立对各自成簇（单对簇正常存在）", () => {
    const items = [
      makeMergeItem("a", "b"),
      makeMergeItem("c", "d"),
    ];
    const clusters = clusterMergePairs(items);
    expect(clusters).toHaveLength(2);
    expect(clusters.every((c) => c.items.length === 1)).toBe(true);
  });

  it("链式连通（A-B、B-C）经传递归为一簇", () => {
    const items = [
      makeMergeItem("a", "b"),
      makeMergeItem("b", "c"),
      makeMergeItem("x", "y"),
    ];
    const clusters = clusterMergePairs(items);
    expect(clusters).toHaveLength(2);
    const chain = clusters.find((c) => c.items.length === 2)!;
    expect(chain.hubId).toBe("b");
  });

  it("大簇排在小簇前面，簇内保持队列原序", () => {
    const items = [
      makeMergeItem("s1", "s2"),
      makeMergeItem("hub", "a"),
      makeMergeItem("hub", "b"),
    ];
    const clusters = clusterMergePairs(items);
    expect(clusters[0].items.map((i) => i.related_creative_id)).toEqual(["a", "b"]);
    expect(clusters[1].items).toHaveLength(1);
  });

  it("枢纽名从 related 侧也能取到（枢纽在右端时）", () => {
    const items = [
      makeMergeItem("a", "hub", "素材A", "右侧枢纽"),
      makeMergeItem("b", "hub", "素材B", "右侧枢纽"),
    ];
    const clusters = clusterMergePairs(items);
    expect(clusters[0].hubId).toBe("hub");
    expect(clusters[0].hubName).toBe("右侧枢纽");
  });

  it("缺少任一端 id 的条目被忽略", () => {
    const items = [
      makeMergeItem("a", "b"),
      { ...makeMergeItem("c", "d"), related_creative_id: null },
    ];
    expect(clusterMergePairs(items)).toHaveLength(1);
  });

  it("空输入返回空数组", () => {
    expect(clusterMergePairs([])).toEqual([]);
  });
});

describe("paginateClusters（按簇分页）", () => {
  const clusterOf = (n: number) => ({
    hubId: `h${n}`,
    hubName: `h${n}`,
    items: Array.from({ length: n }, (_, i) => makeMergeItem(`h${n}`, `m${n}-${i}`)),
  });

  it("每页不超过 pageSize 对，且不拆簇", () => {
    const pages = paginateClusters([clusterOf(12), clusterOf(12), clusterOf(6)], 20);
    expect(pages.map((p) => p.reduce((s, c) => s + c.items.length, 0))).toEqual([12, 18]);
  });

  it("单簇超过 pageSize 时整簇独占一页", () => {
    const pages = paginateClusters([clusterOf(25), clusterOf(3)], 20);
    expect(pages).toHaveLength(2);
    expect(pages[0][0].items).toHaveLength(25);
  });

  it("恰好装满时立即切页", () => {
    const pages = paginateClusters([clusterOf(10), clusterOf(10), clusterOf(5)], 20);
    expect(pages).toHaveLength(2);
  });

  it("空输入返回空数组", () => {
    expect(paginateClusters([], 20)).toEqual([]);
  });
});

describe("moveFocusIndex（键盘焦点移动）", () => {
  it("↓/↑ 正常移动", () => {
    expect(moveFocusIndex(0, 1, 5)).toBe(1);
    expect(moveFocusIndex(2, -1, 5)).toBe(1);
  });

  it("越界钳位不回绕", () => {
    expect(moveFocusIndex(0, -1, 5)).toBe(0);
    expect(moveFocusIndex(4, 1, 5)).toBe(4);
  });

  it("空列表返回 -1", () => {
    expect(moveFocusIndex(0, 1, 0)).toBe(-1);
  });
});

describe("mergeKeyAction（键盘动作映射，P0b 加固）", () => {
  it("↑/↓ 映射为焦点移动", () => {
    expect(mergeKeyAction("ArrowDown")).toBe("focus-down");
    expect(mergeKeyAction("ArrowUp")).toBe("focus-up");
    // j/k 已废弃，不再映射任何动作
    expect(mergeKeyAction("j")).toBeNull();
    expect(mergeKeyAction("k")).toBeNull();
  });

  it("←/→/x 只 arm 不执行（arm 与 confirm 不由同一个键完成）", () => {
    expect(mergeKeyAction("ArrowLeft")).toBe("arm-into-left");
    expect(mergeKeyAction("ArrowRight")).toBe("arm-into-right");
    expect(mergeKeyAction("x")).toBe("arm-not-same");
    // 关键不变量：arm 类动作与 confirm-armed 不共享任何按键
    const armKeys = ["ArrowLeft", "ArrowRight", "x"];
    for (const key of armKeys) {
      expect(mergeKeyAction(key)).not.toBe("confirm-armed");
    }
  });

  it("Enter 确认、Esc 取消", () => {
    expect(mergeKeyAction("Enter")).toBe("confirm-armed");
    expect(mergeKeyAction("Escape")).toBe("cancel-armed");
  });

  it("未映射的按键返回 null（不劫持其他键）", () => {
    expect(mergeKeyAction("a")).toBeNull();
    expect(mergeKeyAction(" ")).toBeNull();
    expect(mergeKeyAction("Tab")).toBeNull();
  });
});

describe("keyByPairId（批量否决 id 映射）", () => {
  it("合成 id <creative_id>:<related_creative_id> 映射到四元组选中 key", () => {
    const item = makeMergeItem("c-1", "c-2");
    const map = keyByPairId([item]);
    expect(map.get("c-1:c-2")).toBe("merge_candidate|c-1|c-2|-");
  });

  it("同一枢纽的多对映射互不串扰", () => {
    const map = keyByPairId([
      makeMergeItem("hub", "a"),
      makeMergeItem("hub", "b"),
    ]);
    expect(map.get("hub:a")).not.toBe(map.get("hub:b"));
  });

  it("缺端点的条目不产生映射", () => {
    const item = { ...makeMergeItem("c-1", "c-2"), related_creative_id: null };
    expect(pairId(item)).toBeNull();
    expect(keyByPairId([item]).size).toBe(0);
  });
});
