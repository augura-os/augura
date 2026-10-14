import { afterEach, describe, expect, it, vi } from "vitest";
import { add, has, remove, startedAtFor, subscribe } from "./inflightDiagnoses";

// 模块级 store 跨用例存续：各用例用专属 key，互不污染；统一清理兜底
const USED_KEYS = ["asset-1:zh", "asset-1:en", "asset-2:zh", "stuck:zh", "clock:zh", "dup:zh"];

afterEach(() => {
  for (const key of USED_KEYS) remove(key);
});

describe("inflightDiagnoses store", () => {
  it("add/has/remove lifecycle", () => {
    const key = "asset-1:zh";
    expect(has(key)).toBe(false);
    expect(startedAtFor(key)).toBeNull();
    add(key);
    expect(has(key)).toBe(true);
    remove(key);
    expect(has(key)).toBe(false);
    expect(startedAtFor(key)).toBeNull();
  });

  it("tracks keys independently per assetId:lang", () => {
    add("asset-1:zh");
    expect(has("asset-1:zh")).toBe(true);
    expect(has("asset-1:en")).toBe(false);
    expect(has("asset-2:zh")).toBe(false);
    remove("asset-1:zh");
    expect(has("asset-1:zh")).toBe(false);
  });

  it("notifies subscribers on add and remove, and stops after unsubscribe", () => {
    const listener = vi.fn();
    const unsubscribe = subscribe(listener);
    add("asset-1:zh");
    remove("asset-1:zh");
    expect(listener).toHaveBeenCalledTimes(2);
    unsubscribe();
    add("asset-1:zh");
    remove("asset-1:zh");
    expect(listener).toHaveBeenCalledTimes(2);
  });

  it("does not notify when removing a key that is not inflight", () => {
    const listener = vi.fn();
    const unsubscribe = subscribe(listener);
    remove("asset-2:zh");
    expect(listener).not.toHaveBeenCalled();
    unsubscribe();
  });

  it("re-add keeps the original startedAt (stopwatch stays continuous)", () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(new Date("2026-10-14T09:00:00Z"));
      add("dup:zh");
      const startedAt = startedAtFor("dup:zh");
      vi.advanceTimersByTime(30_000);
      add("dup:zh");
      expect(startedAtFor("dup:zh")).toBe(startedAt);
    } finally {
      vi.useRealTimers();
    }
  });

  it("records startedAt as the add-time timestamp", () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(new Date("2026-10-14T09:00:00Z"));
      add("clock:zh");
      expect(startedAtFor("clock:zh")).toBe(Date.now());
      vi.advanceTimersByTime(45_000);
      expect(startedAtFor("clock:zh")).toBe(Date.now() - 45_000);
    } finally {
      vi.useRealTimers();
    }
  });

  it("auto-clears a stuck key after the 10-minute residency fallback", () => {
    vi.useFakeTimers();
    try {
      const listener = vi.fn();
      const unsubscribe = subscribe(listener);
      add("stuck:zh");
      expect(has("stuck:zh")).toBe(true);
      listener.mockClear();
      // 9 分 59 秒仍在；越过 10 分钟驻留上限自动释放并通知订阅方
      vi.advanceTimersByTime(9 * 60 * 1000 + 59_000);
      expect(has("stuck:zh")).toBe(true);
      vi.advanceTimersByTime(1_000);
      expect(has("stuck:zh")).toBe(false);
      expect(listener).toHaveBeenCalledTimes(1);
      expect(startedAtFor("stuck:zh")).toBeNull();
      unsubscribe();
    } finally {
      vi.useRealTimers();
    }
  });

  it("re-add refreshes the residency fallback timer", () => {
    vi.useFakeTimers();
    try {
      add("dup:zh");
      vi.advanceTimersByTime(9 * 60 * 1000);
      add("dup:zh");
      // 兜底从重新 add 起重新计时：再过 9 分钟仍不清除
      vi.advanceTimersByTime(9 * 60 * 1000);
      expect(has("dup:zh")).toBe(true);
      vi.advanceTimersByTime(60 * 1000 + 1);
      expect(has("dup:zh")).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });
});
