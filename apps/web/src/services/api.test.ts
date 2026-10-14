import { beforeEach, describe, expect, it, vi } from "vitest";
import type { LabDiagnosis } from "@shared";

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));

vi.mock("../services/client", () => ({
  apiClient: { get: mocks.get, post: mocks.post },
}));

import { setLang } from "../lib/i18n";
import { createLabDiagnosis, getLabAnalysis } from "./api";

function okEnvelope(data: Partial<LabDiagnosis> = {}) {
  return {
    data: {
      success: true,
      message: "",
      data: { asset_id: "asset-1", lang: "zh", ...data },
    },
  };
}

describe("createLabDiagnosis", () => {
  beforeEach(() => {
    mocks.post.mockReset();
    mocks.post.mockResolvedValue(okEnvelope());
    setLang("en");
  });

  it("injects the current UI language into the request body", async () => {
    setLang("zh");
    await createLabDiagnosis({ asset_id: "asset-1" });
    expect(mocks.post).toHaveBeenCalledWith(
      "/lab/diagnoses",
      { asset_id: "asset-1", lang: "zh", force_regenerate: false },
      { timeout: 600000 },
    );
  });

  it("follows the UI language when it is English", async () => {
    await createLabDiagnosis({ asset_id: "asset-1" });
    expect(mocks.post).toHaveBeenCalledWith(
      "/lab/diagnoses",
      { asset_id: "asset-1", lang: "en", force_regenerate: false },
      { timeout: 600000 },
    );
  });

  it("respects an explicit lang passed by the caller", async () => {
    setLang("zh");
    await createLabDiagnosis({ asset_id: "asset-1", lang: "en" });
    expect(mocks.post).toHaveBeenCalledWith(
      "/lab/diagnoses",
      { asset_id: "asset-1", lang: "en", force_regenerate: false },
      { timeout: 600000 },
    );
  });

  it("keeps the audience field alongside the injected lang", async () => {
    await createLabDiagnosis({ asset_id: "asset-1", audience: { gender: "male" } });
    expect(mocks.post).toHaveBeenCalledWith(
      "/lab/diagnoses",
      { asset_id: "asset-1", audience: { gender: "male" }, lang: "en", force_regenerate: false },
      { timeout: 600000 },
    );
  });

  it("passes force_regenerate through when the caller asks for a full rerun", async () => {
    await createLabDiagnosis({ asset_id: "asset-1", force_regenerate: true });
    expect(mocks.post).toHaveBeenCalledWith(
      "/lab/diagnoses",
      { asset_id: "asset-1", lang: "en", force_regenerate: true },
      { timeout: 600000 },
    );
  });
});

describe("getLabAnalysis", () => {
  beforeEach(() => {
    mocks.get.mockReset();
    mocks.get.mockResolvedValue({
      data: { success: true, message: "", data: { analysis_status: "completed" } },
    });
  });

  it("passes lang as a query param so the diagnosis matches the UI language", async () => {
    await getLabAnalysis("asset-1", "en");
    expect(mocks.get).toHaveBeenCalledWith("/lab/analysis/asset-1", { params: { lang: "en" } });

    await getLabAnalysis("asset-1", "zh");
    expect(mocks.get).toHaveBeenCalledWith("/lab/analysis/asset-1", { params: { lang: "zh" } });
  });
});
