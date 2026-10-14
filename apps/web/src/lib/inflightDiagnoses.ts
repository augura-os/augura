import { useSyncExternalStore } from "react";

/**
 * 在途诊断（key = assetId:lang）可订阅 store：模块级状态存活于组件 remount 之外——
 * 生成中切走再回来时 mutation 实例状态已丢，但 key 仍在这里，配合
 * useInflightDiagnosis 订阅让「生成中」面板与秒表连续恢复（listeners +
 * useSyncExternalStore 模式与 lib/i18n.tsx 一致）。
 *
 * 10 分钟驻留兜底：与诊断端点 600s 超时对齐。请求僵死（连接被中间层掐断、
 * 组件卸载后 mutation 回调不再触发）导致 key 无人释放时自动清除，
 * 防面板永远卡在「生成中」。
 */
const TTL_MS = 10 * 60 * 1000;

interface InflightEntry {
  /** 发起时刻（epoch ms）；秒表起点用，remount 后保持连续 */
  startedAt: number;
  timer: ReturnType<typeof setTimeout>;
}

const entries = new Map<string, InflightEntry>();
const listeners = new Set<() => void>();

function notify(): void {
  listeners.forEach((listener) => listener());
}

/** 标记诊断在途并记起点；key 已存在时刷新驻留兜底但保留原起点（防重由调用方 has 守卫）。 */
export function add(key: string): void {
  const existing = entries.get(key);
  if (existing) {
    clearTimeout(existing.timer);
    existing.timer = setTimeout(() => remove(key), TTL_MS);
    return;
  }
  entries.set(key, {
    startedAt: Date.now(),
    timer: setTimeout(() => remove(key), TTL_MS),
  });
  notify();
}

/** 释放 key（失败重试放行 / 诊断随 refetch 到达 / 驻留兜底）。 */
export function remove(key: string): void {
  const entry = entries.get(key);
  if (!entry) return;
  clearTimeout(entry.timer);
  entries.delete(key);
  notify();
}

export function has(key: string): boolean {
  return entries.has(key);
}

/** 在途起点（epoch ms）；不在途返回 null。 */
export function startedAtFor(key: string): number | null {
  return entries.get(key)?.startedAt ?? null;
}

/** 订阅 store 变更（useInflightDiagnosis 内部用；导出供测试直接验证通知）。 */
export function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** 组件订阅某 key 的在途状态：add/remove（含驻留兜底）触发订阅组件重渲染。 */
export function useInflightDiagnosis(key: string): boolean {
  return useSyncExternalStore(subscribe, () => has(key));
}
