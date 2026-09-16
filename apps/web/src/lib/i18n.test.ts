import { describe, expect, it } from "vitest";
import { en } from "../locales/en";
import { zh } from "../locales/zh";
import { getLang, setLang, translate } from "./i18n";

describe("i18n", () => {
  it("defaults to English", () => {
    expect(getLang()).toBe("en");
    expect(translate("settings.title")).toBe("Settings");
  });

  it("returns Chinese after setLang", () => {
    setLang("zh");
    expect(getLang()).toBe("zh");
    expect(translate("settings.title")).toBe("设置");
    setLang("en");
    expect(translate("settings.title")).toBe("Settings");
  });

  it("keeps en/zh dictionary key sets identical", () => {
    const enKeys = Object.keys(en).sort();
    const zhKeys = Object.keys(zh).sort();
    expect(zhKeys).toEqual(enKeys);
  });

  it("falls back to the en dictionary when the current language misses a key", () => {
    const key = "test.only-in-en";
    en[key] = "english only";
    try {
      setLang("zh");
      expect(translate(key)).toBe("english only");
    } finally {
      delete en[key];
      setLang("en");
    }
  });

  it("returns the key itself and warns when both dictionaries miss it", () => {
    const warnings: string[] = [];
    const original = console.warn;
    console.warn = (msg: string) => warnings.push(msg);
    try {
      expect(translate("test.missing-everywhere")).toBe("test.missing-everywhere");
    } finally {
      console.warn = original;
    }
    expect(warnings.some((msg) => msg.includes("test.missing-everywhere"))).toBe(true);
  });
});
