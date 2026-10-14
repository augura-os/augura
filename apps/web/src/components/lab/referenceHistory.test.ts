import { describe, expect, it } from "vitest";
import type { LabReferenceItem } from "@shared";
import { formatReferenceDate, referenceRowAction, referenceStatusKey } from "./referenceHistory";

function makeItem(overrides: Partial<LabReferenceItem> = {}): LabReferenceItem {
  return {
    id: "asset-1",
    filename: "ref.mp4",
    file_type: "video",
    thumbnail_url: null,
    created_at: "2026-10-01T08:00:00Z",
    analysis_status: "completed",
    creative_name: null,
    confidence: null,
    lifecycle_state: null,
    diagnosis_current_langs: [],
    diagnosis_stale_langs: [],
    ...overrides,
  };
}

describe("referenceRowAction", () => {
  it("returns view-report when a current-version report exists", () => {
    expect(referenceRowAction(makeItem({ diagnosis_current_langs: ["zh"] }))).toBe("view-report");
    // 有报告优先于分析状态——报告步幂等，直接打开即可
    expect(
      referenceRowAction(
        makeItem({ analysis_status: "processing", diagnosis_current_langs: ["en"] }),
      ),
    ).toBe("view-report");
  });

  it("returns continue when analysis is done but no current report exists", () => {
    expect(referenceRowAction(makeItem({ analysis_status: "completed" }))).toBe("continue");
    // 只有旧版本（stale）报告不算「已有报告」——进报告步会给 stale 提示
    expect(
      referenceRowAction(makeItem({ analysis_status: "completed", diagnosis_stale_langs: ["zh"] })),
    ).toBe("continue");
  });

  it("returns none for queued/processing/failed/unanalyzed items", () => {
    for (const status of ["pending", "processing", "failed", "none"] as const) {
      expect(referenceRowAction(makeItem({ analysis_status: status }))).toBe("none");
    }
  });
});

describe("referenceStatusKey", () => {
  it("maps every known status to its i18n key", () => {
    expect(referenceStatusKey("pending")).toBe("lab.reference.history.status.pending");
    expect(referenceStatusKey("processing")).toBe("lab.reference.history.status.processing");
    expect(referenceStatusKey("completed")).toBe("lab.reference.history.status.completed");
    expect(referenceStatusKey("failed")).toBe("lab.reference.history.status.failed");
    expect(referenceStatusKey("none")).toBe("lab.reference.history.status.none");
  });

  it("falls back to the none key for unknown statuses", () => {
    expect(referenceStatusKey("uploading" as unknown as never)).toBe(
      "lab.reference.history.status.none",
    );
  });
});

describe("formatReferenceDate", () => {
  it("keeps the raw string when the date is invalid", () => {
    expect(formatReferenceDate("not-a-date")).toBe("not-a-date");
  });
});
