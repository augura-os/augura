import { describe, expect, it } from "vitest";
import { en } from "../../locales/en";
import { zh } from "../../locales/zh";
import { inboxReasonLine, inboxTitleLine } from "./inboxReason";

const tZh = (key: string) => zh[key] ?? key;
const tEn = (key: string) => en[key] ?? key;

describe("inboxReasonLine", () => {
  it("renders the zh merge_similarity line from code + params", () => {
    const line = inboxReasonLine(
      {
        reason: "相似度 0.27，未达自动合并阈值 0.34，疑似同创意",
        reason_code: "merge_similarity",
        reason_params: { score: 0.27, threshold: 0.34 },
      },
      tZh,
    );
    expect(line).toBe("相似度 0.27，未达自动合并阈值 0.34，疑似同创意");
  });

  it("renders the en dna_unassigned line", () => {
    const line = inboxReasonLine(
      {
        reason: "未归族 DNA（3 个 Variant），需人工确认家族",
        reason_code: "dna_unassigned",
        reason_params: { variant_count: 3 },
      },
      tEn,
    );
    expect(line).toBe("Not in any DNA family (3 variants) — confirm the family");
  });

  it("renders observation_open with formatted money in en", () => {
    const line = inboxReasonLine(
      {
        reason: "观察对未结案：…",
        reason_code: "observation_open",
        reason_params: {
          source_name: "alpha",
          source_spend: 1234.5,
          source_cpp: 41.7,
          target_name: "beta",
          target_spend: 20,
          target_cpp: null,
        },
      },
      tEn,
    );
    expect(line).toBe(
      "Observation pair still open: alpha (spend $1,235 / CPP $41.70) " +
        "vs beta (spend $20 / CPP -)",
    );
  });

  it("renders derivation_pending_judge with signed delta and no-data branch", () => {
    const withDelta = inboxReasonLine(
      {
        reason: "裂变实验待判定（成本差 +1.23）",
        reason_code: "derivation_pending_judge",
        reason_params: { cpp_delta: 1.234 },
      },
      tZh,
    );
    expect(withDelta).toBe("裂变实验待判定（成本差 +1.23）");
    const noData = inboxReasonLine(
      {
        reason: "裂变实验待判定（数据不足）",
        reason_code: "derivation_pending_judge",
        reason_params: { cpp_delta: null },
      },
      tEn,
    );
    expect(noData).toBe("Iteration experiment awaiting verdict (insufficient data)");
  });

  it("localizes the suggested factor via factor.* labels", () => {
    const line = inboxReasonLine(
      {
        reason: "因子建议改为 language-market：证据x",
        reason_code: "factor_suggestion",
        reason_params: { verdict: "language-market", reason: "证据x" },
      },
      tEn,
    );
    expect(line).toBe("Suggest changing the factor to Language / market: 证据x");
  });

  it("falls back to the legacy reason when the code has no template", () => {
    const line = inboxReasonLine(
      {
        reason: "旧中文理由",
        reason_code: "future_unknown_code",
        reason_params: {},
      },
      tZh,
    );
    expect(line).toBe("旧中文理由");
  });

  it("falls back to the legacy reason when reason_code is missing", () => {
    expect(
      inboxReasonLine({ reason: "库存建议文本", reason_code: null }, tEn),
    ).toBe("库存建议文本");
  });
});

describe("inboxTitleLine", () => {
  it("renders market_detect and threshold_calibration titles in en", () => {
    expect(
      inboxTitleLine(
        {
          title: "市场前缀 BR",
          reason_code: "market_detect",
          reason_params: { prefix: "BR" },
        },
        tEn,
      ),
    ).toBe("Market prefix BR");
    expect(
      inboxTitleLine(
        { title: "合并阈值校准", reason_code: "threshold_calibration", reason_params: {} },
        tEn,
      ),
    ).toBe("Merge threshold calibration");
  });

  it("renders the rule_keyword title with a localized target label", () => {
    expect(
      inboxTitleLine(
        {
          title: "alpha（机制词）",
          reason_code: "rule_keyword",
          reason_params: { word: "alpha", target: "mechanic" },
        },
        tZh,
      ),
    ).toBe("alpha（机制词）");
  });

  it("falls back to the raw title when no title template exists", () => {
    expect(
      inboxTitleLine(
        { title: "alpha ↔ beta", reason_code: "merge_similarity", reason_params: {} },
        tZh,
      ),
    ).toBe("alpha ↔ beta");
  });
});
