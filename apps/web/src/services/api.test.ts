import { beforeEach, describe, expect, it, vi } from "vitest";
import type { LabDiagnosis } from "@shared";

const mocks = vi.hoisted(() => ({ post: vi.fn() }));

vi.mock("../services/client", () => ({
  apiClient: { post: mocks.post },
}));

import { setLang } from "../lib/i18n";
import { createLabDiagnosis } from "./api";

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
      { asset_id: "asset-1", lang: "zh" },
      { timeout: 600000 },
    );
  });

  it("follows the UI language when it is English", async () => {
    await createLabDiagnosis({ asset_id: "asset-1" });
    expect(mocks.post).toHaveBeenCalledWith(
      "/lab/diagnoses",
      { asset_id: "asset-1", lang: "en" },
      { timeout: 600000 },
    );
  });

  it("respects an explicit lang passed by the caller", async () => {
    setLang("zh");
    await createLabDiagnosis({ asset_id: "asset-1", lang: "en" });
    expect(mocks.post).toHaveBeenCalledWith(
      "/lab/diagnoses",
      { asset_id: "asset-1", lang: "en" },
      { timeout: 600000 },
    );
  });

  it("keeps the audience field alongside the injected lang", async () => {
    await createLabDiagnosis({ asset_id: "asset-1", audience: { gender: "male" } });
    expect(mocks.post).toHaveBeenCalledWith(
      "/lab/diagnoses",
      { asset_id: "asset-1", audience: { gender: "male" }, lang: "en" },
      { timeout: 600000 },
    );
  });
});
