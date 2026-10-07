import type { AiTestResult } from "@shared";
import { apiErrorCode, apiErrorParams } from "../services/client";
import { getLang } from "./i18n";

/** 字节数 → 上限展示串（与后端 legacy 文案同口径：≥1GiB 用 GB，否则 MB）。 */
function formatLimitBytes(value: unknown): string {
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n) || n <= 0) return "-";
  if (n >= 1024 ** 3) return `${parseFloat((n / 1024 ** 3).toPrecision(3))} GB`;
  return `${parseFloat((n / 1024 ** 2).toPrecision(3))} MB`;
}

/**
 * 把 API 错误渲染成本地化文案：有 code 且 `error.<code>` 模板存在时
 * 用 params 插值；否则回退后端 message（旧后端/未知码的中文兜底）。
 */
export function apiErrorText(error: unknown, t: (key: string) => string): string {
  const fallback = error instanceof Error ? error.message : "";
  const code = apiErrorCode(error);
  if (!code) return fallback;
  const key = `error.${code}`;
  const template = t(key);
  if (template === key) return fallback;
  const params: Record<string, unknown> = { ...apiErrorParams(error) };
  if ("limit_bytes" in params) params.limit = formatLimitBytes(params.limit_bytes);
  if (code === "analysis_failed") {
    const detail = typeof params.detail === "string" ? params.detail : "";
    params.detail = detail ? `${getLang() === "zh" ? "：" : ": "}${detail}` : "";
  }
  return template.replace(/\{(\w+)\}/g, (raw, name: string) =>
    name in params ? String(params[name]) : raw,
  );
}

// 连接测试自有码 → settings.aiTest.* 模板；其余码（列表失败的透传）走 error.*
const AI_TEST_KEYS: Record<string, string> = {
  ai_test_ok: "settings.aiTest.ok",
  ai_test_model_missing: "settings.aiTest.modelMissing",
};

/**
 * 连接测试结果渲染：优先按 code 查模板插值；无码/缺模板回退后端 message
 * （旧后端的中文兜底）。
 */
export function aiTestResultText(
  result: AiTestResult,
  t: (key: string) => string,
): string {
  if (!result.code) return result.message;
  const key = AI_TEST_KEYS[result.code] ?? `error.${result.code}`;
  const template = t(key);
  if (template === key) return result.message;
  const params = result.params ?? {};
  return template.replace(/\{(\w+)\}/g, (raw, name: string) =>
    name in params ? String(params[name]) : raw,
  );
}
