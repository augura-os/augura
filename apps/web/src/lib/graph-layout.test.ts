import { describe, expect, it } from "vitest";
import type { GraphResponse } from "@shared";
import { buildFlowGraph, childrenOf, parentOf } from "./graph-layout";

const graph: GraphResponse = {
  nodes: [
    { id: "dna:d1", type: "dna", label: "D01 test", ref_id: "d1" },
    { id: "creative:c1", type: "creative", label: "c1", ref_id: "c1" },
    { id: "variant:v1", type: "variant", label: "v1", ref_id: "v1" },
    { id: "asset:a1", type: "asset", label: "a1", ref_id: "a1" },
    { id: "tag:t1", type: "tag", label: "t1", ref_id: "t1" },
    { id: "creative:c2", type: "creative", label: "c2", ref_id: "c2" },
    { id: "tag:orphan", type: "tag", label: "orphan", ref_id: "orphan" },
  ],
  edges: [
    { id: "e0", source: "dna:d1", target: "creative:c1", type: "HAS_CREATIVE" },
    { id: "e1", source: "creative:c1", target: "variant:v1", type: "HAS_VARIANT" },
    { id: "e2", source: "variant:v1", target: "asset:a1", type: "HAS_ASSET" },
    { id: "e3", source: "asset:a1", target: "tag:t1", type: "HAS_TAG" },
    { id: "e4", source: "creative:c1", target: "creative:c2", type: "SIMILAR_TO" },
  ],
};

describe("childrenOf", () => {
  it("filters by edge type", () => {
    expect(childrenOf(graph, "creative:c1", "HAS_VARIANT").map((n) => n.id)).toEqual([
      "variant:v1",
    ]);
  });

  it("returns all children without a type filter", () => {
    expect(childrenOf(graph, "creative:c1").map((n) => n.id)).toEqual([
      "variant:v1",
      "creative:c2",
    ]);
  });

  it("returns empty for leaf nodes", () => {
    expect(childrenOf(graph, "tag:t1")).toEqual([]);
  });
});

describe("parentOf", () => {
  it("finds the first incoming edge of the type", () => {
    expect(parentOf(graph, "variant:v1", "HAS_VARIANT")?.id).toBe("creative:c1");
  });

  it("returns undefined without a matching parent", () => {
    expect(parentOf(graph, "creative:c1", "HAS_VARIANT")).toBeUndefined();
  });
});

describe("buildFlowGraph", () => {
  const { nodes, edges } = buildFlowGraph(graph);

  it("keeps every node, appending orphans", () => {
    expect(nodes).toHaveLength(7);
    expect(nodes.map((n) => n.id)).toContain("tag:orphan");
  });

  it("lays out columns dna → creative → variant → asset → tag", () => {
    const xOf = (id: string) => nodes.find((n) => n.id === id)?.position.x;
    expect(xOf("dna:d1")).toBe(0);
    expect(xOf("creative:c1")).toBe(300);
    expect(xOf("variant:v1")).toBe(600);
    expect(xOf("asset:a1")).toBe(900);
    expect(xOf("tag:t1")).toBe(1200);
  });

  it("visits a dna subtree before the next root", () => {
    const order = nodes.map((n) => n.id);
    expect(order.indexOf("dna:d1")).toBeLessThan(order.indexOf("tag:t1"));
    expect(order.indexOf("tag:t1")).toBeLessThan(order.indexOf("creative:c2"));
  });

  it("does not visit a creative twice via SIMILAR_TO", () => {
    // c2 is reachable from c1's SIMILAR_TO edge but must still be laid out
    // as its own creative-row subtree, exactly once.
    expect(nodes.filter((n) => n.id === "creative:c2")).toHaveLength(1);
  });

  it("styles SIMILAR_TO edges dashed amber, structural edges plain gray", () => {
    const similar = edges.find((e) => e.id === "e4");
    const structural = edges.find((e) => e.id === "e1");
    expect(similar?.style).toMatchObject({ stroke: "#f59e0b", strokeDasharray: "6 4" });
    expect(structural?.style).toMatchObject({ stroke: "#d4d4d4" });
    // No SVG markers anywhere (zoom raster cost — see perf commit).
    for (const edge of edges) expect(edge.markerEnd).toBeUndefined();
  });
});

describe("buildFlowGraph density", () => {
  // Extended fixture with a second variant and a DERIVED_FROM edge so the
  // compact edge filter is exercised against every kept/dropped type.
  const derivedGraph: GraphResponse = {
    nodes: [...graph.nodes, { id: "variant:v2", type: "variant", label: "v2", ref_id: "v2" }],
    edges: [
      ...graph.edges,
      { id: "e5", source: "creative:c1", target: "variant:v2", type: "HAS_VARIANT" },
      // DERIVED_FROM is emitted by the backend but missing from the shared
      // TS union — cast through unknown to model production data.
      {
        id: "e6",
        source: "variant:v1",
        target: "variant:v2",
        type: "DERIVED_FROM",
      } as unknown as GraphResponse["edges"][number],
    ],
  };

  it("full density keeps every node and edge (default)", () => {
    const full = buildFlowGraph(derivedGraph, undefined, "full");
    expect(full.nodes).toHaveLength(8);
    expect(full.edges).toHaveLength(7);
  });

  it("compact density keeps only dna/creative/variant nodes", () => {
    const compact = buildFlowGraph(graph, undefined, "compact");
    expect(compact.nodes.map((n) => n.id).sort()).toEqual([
      "creative:c1",
      "creative:c2",
      "dna:d1",
      "variant:v1",
    ]);
    // asset/tag columns are gone; creatives keep their column position
    expect(compact.nodes.find((n) => n.id === "creative:c1")?.position.x).toBe(300);
  });

  it("compact density keeps only inter-column edges with both endpoints present", () => {
    const compact = buildFlowGraph(derivedGraph, undefined, "compact");
    expect(compact.edges.map((e) => e.id).sort()).toEqual(["e0", "e1", "e4", "e5", "e6"]);
    // HAS_ASSET / HAS_TAG edges never enter compact mode
    expect(compact.edges.find((e) => e.id === "e2")).toBeUndefined();
    expect(compact.edges.find((e) => e.id === "e3")).toBeUndefined();
  });
});
