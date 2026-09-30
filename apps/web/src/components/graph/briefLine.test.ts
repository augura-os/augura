import { describe, expect, it } from "vitest";
import type { RecommendationItem } from "@shared";
import { en } from "../../locales/en";
import { zh } from "../../locales/zh";
import { briefLine, formatDollars, priorityText } from "./briefLine";

const tZh = (key: string) => zh[key] ?? key;
const tEn = (key: string) => en[key] ?? key;

function makeItem(overrides: Partial<RecommendationItem>): RecommendationItem {
  return {
    creative_id: "c1",
    creative_name: "test-creative",
    dna_code: "D01",
    dna_name: "测试",
    action: "ITERATE",
    reasons: ["旧中文理由"],
    reason_code: "",
    reason_params: {},
    priority_dollars: 0,
    confidence: 0,
    metrics: {
      spend: 0,
      payers: 0,
      installs: 0,
      cpp: null,
      roas: null,
      cpi: null,
      ipm: null,
      days_idle: null,
      recent_spend: 0,
      recent_cpp: null,
      variant_count: 1,
    },
    score: null,
    score_breakdown: {},
    lifecycle_state: "active",
    ...overrides,
  };
}

describe("formatDollars", () => {
  it("formats large values as grouped integers", () => {
    expect(formatDollars(2400)).toBe("2,400");
  });
  it("keeps one decimal for mid values", () => {
    expect(formatDollars(83.4)).toBe("83.4");
  });
  it("keeps two decimals for small values", () => {
    expect(formatDollars(5.23)).toBe("5.23");
  });
});

describe("briefLine", () => {
  it("renders the zh one-liner from reason_code + params", () => {
    const item = makeItem({
      reason_code: "efficient_not_scaled",
      reason_params: { cpp: 41.76, spend: 752 },
    });
    const line = briefLine(item, tZh);
    expect(line).toContain("41.76");
    expect(line).toContain("752");
    expect(line).toContain("加注");
  });

  it("renders the en one-liner from reason_code + params", () => {
    const item = makeItem({
      action: "PAUSE",
      reason_code: "zero_payers",
      reason_params: { spend: 2400 },
    });
    const line = briefLine(item, tEn);
    expect(line).toBe("$2,400 spent, 0 payers — pause");
  });

  it("falls back to the legacy reason when the code has no template", () => {
    const item = makeItem({ reason_code: "unknown_future_code" });
    expect(briefLine(item, tZh)).toBe("旧中文理由");
  });

  it("renders the R0 insufficient_data one-liner in both locales", () => {
    const item = makeItem({
      reason_code: "insufficient_data",
      reason_params: {
        spend: 3.2,
        spend_min: 10,
        impressions: 800,
        impressions_min: 5000,
      },
    });
    expect(briefLine(item, tZh)).toBe(
      "观察期：消耗 $3 未达 $10 且曝光 800 未达 5,000，继续投放积累数据",
    );
    expect(briefLine(item, tEn)).toBe(
      "Observation: spend $3 below $10, 800 impressions below 5,000 — keep gathering data",
    );
  });
});

describe("priorityText", () => {
  it("renders dollars per day with confidence", () => {
    const item = makeItem({ priority_dollars: 83.4, confidence: 0.78 });
    expect(priorityText(item, tZh)).toBe("≈ $83.4/天 · 78% 把握");
  });

  it("renders the en copy", () => {
    const item = makeItem({ priority_dollars: 2400, confidence: 0.5 });
    expect(priorityText(item, tEn)).toBe("≈ $2,400/day · 50% confidence");
  });

  it("returns null for near-zero dollars", () => {
    expect(priorityText(makeItem({ priority_dollars: 0 }), tZh)).toBeNull();
  });
});
