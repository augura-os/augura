import { describe, expect, it } from "vitest";
import type { RecommendationItem } from "@shared";
import { en } from "../../locales/en";
import { zh } from "../../locales/zh";
import { formatDollars, priorityText } from "./briefLine";

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
    priority_dollars: 0,
    confidence: 0,
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
