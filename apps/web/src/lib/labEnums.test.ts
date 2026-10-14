import { afterEach, describe, expect, it } from "vitest";
import { setLang } from "./i18n";
import { labEnumLabel } from "./labEnums";

describe("labEnumLabel", () => {
  afterEach(() => {
    setLang("en");
  });

  it("localizes choice-chain ids per language", () => {
    setLang("zh");
    expect(labEnumLabel("choice", "正解")).toBe("正解");
    expect(labEnumLabel("choice", "作死")).toBe("作死");
    setLang("en");
    expect(labEnumLabel("choice", "正解")).toBe("Correct");
    expect(labEnumLabel("choice", "作死")).toBe("Fatal");
  });

  it("falls back to the raw id when no template exists", () => {
    expect(labEnumLabel("choice", "未收录值")).toBe("未收录值");
  });

  it("returns empty string for nullish ids", () => {
    expect(labEnumLabel("choice", null)).toBe("");
    expect(labEnumLabel("choice", undefined)).toBe("");
  });
});
