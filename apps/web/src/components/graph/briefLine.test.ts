import { describe, expect, it } from "vitest";
import type { RecommendationItem } from "@shared";
import { en } from "../../locales/en";
import { zh } from "../../locales/zh";
import { setLang } from "../../lib/i18n";
import {
  briefBitLine,
  briefLabel,
  briefLine,
  formatDollars,
  priorityText,
  recommendationLines,
} from "./briefLine";

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

  it("renders insufficient_payers in both locales", () => {
    const item = makeItem({
      reason_code: "insufficient_payers",
      reason_params: { spend: 400, payers: 2 },
    });
    expect(briefLine(item, tZh)).toBe(
      "消耗 $400 仅 2 个付费，样本太薄，继续投放积累后再判定",
    );
    expect(briefLine(item, tEn)).toBe(
      "$400 spent but only 2 payers — sample too thin, keep gathering data",
    );
  });

  it("renders factor_exhausted with a localized factor label", () => {
    const item = makeItem({
      reason_code: "factor_exhausted",
      reason_params: { factor: "remake", judged_count: 3 },
    });
    expect(briefLine(item, tZh)).toBe("「重制」维度 3 次裂变全无效，建议换维度迭代");
    expect(briefLine(item, tEn)).toBe(
      "3 iterations on Remake, all ineffective — try another dimension",
    );
  });

  it("renders derivations_exhausted with a localized factor list", () => {
    const item = makeItem({
      action: "ARCHIVE",
      reason_code: "derivations_exhausted",
      reason_params: { judged_count: 4, factors: "aspect-ratio,remake" },
    });
    setLang("zh");
    try {
      expect(briefLine(item, tZh)).toBe("裂变 4 次全部无效（画幅、重制），方向已耗尽");
    } finally {
      setLang("en");
    }
    expect(briefLine(item, tEn)).toBe(
      "4 iterations, all ineffective (Aspect ratio, Remake) — direction exhausted",
    );
  });
});

describe("briefBitLine", () => {
  it("renders trend_cpp_up from the en template", () => {
    const line = briefBitLine(
      { code: "trend_cpp_up", params: { recent_cpp: 140, cpp: 100 } },
      tEn,
    );
    expect(line).toBe("CPP rising over the last 7 days ($140.00 vs $100.00 overall)");
  });

  it("returns null when the code has no template", () => {
    expect(briefBitLine({ code: "future_code", params: {} }, tZh)).toBeNull();
  });
});

describe("recommendationLines", () => {
  it("renders evidence from reason_bits via templates", () => {
    const item = makeItem({
      reason_code: "keep_healthy",
      reasons: ["成本健康", "旧补充理由"],
      reason_bits: [{ code: "variants_compare", params: { variant_count: 3 } }],
    });
    expect(recommendationLines(item, tEn)).toEqual([
      "CPP healthy, ROAS on target — keep the pace",
      "3 variants available for cross-comparison",
    ]);
  });

  it("falls back to legacy reasons when a bit template is missing", () => {
    const item = makeItem({
      reasons: ["成本健康", "旧补充理由"],
      reason_bits: [{ code: "future_code", params: {} }],
    });
    expect(recommendationLines(item, tEn)).toEqual(["成本健康", "旧补充理由"]);
  });

  it("falls back to legacy reasons when reason_bits is empty", () => {
    const item = makeItem({ reasons: ["成本健康", "旧补充理由"] });
    expect(recommendationLines(item, tZh)).toEqual(["成本健康", "旧补充理由"]);
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

describe("briefLabel", () => {
  it("renders winner labels in zh and en", () => {
    expect(briefLabel("proven_winner", tZh)).toBe("验证赢家");
    expect(briefLabel("proven_winner", tEn)).toBe("Proven winner");
    expect(briefLabel("low_click_high_value", tZh)).toBe("低点击高价值");
    expect(briefLabel("low_click_high_value", tEn)).toBe("Low click, high value");
  });

  it("renders every known label code from a template in both locales", () => {
    const codes = [
      "underexplored",
      "proven_winner",
      "saturated",
      "audience_niche_winner",
      "potential_winner",
      "low_click_high_value",
      "high_click_low_value",
    ];
    for (const code of codes) {
      expect(briefLabel(code, tZh)).not.toBe(code);
      expect(briefLabel(code, tEn)).not.toBe(code);
    }
  });

  it("falls back to the raw code when the template is missing", () => {
    expect(briefLabel("future_label", tZh)).toBe("future_label");
    expect(briefLabel("future_label", tEn)).toBe("future_label");
  });

  it("returns empty string for an empty code", () => {
    expect(briefLabel("", tZh)).toBe("");
  });
});
