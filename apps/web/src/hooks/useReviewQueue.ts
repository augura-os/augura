import { useQuery } from "@tanstack/react-query";
import { fetchReviewQueue } from "../services/api";

export function useReviewQueue() {
  return useQuery({
    queryKey: ["review-queue"],
    queryFn: fetchReviewQueue,
    // 15s 轮询：队列内容被后台持续改写（自动判定/周期巩固/智能建族提案），
    // 只靠操作后的单次 invalidate 时，refetch 一慢/一超时 UI 就永久停在旧
    // 数据（react-query 静默保留 stale 缓存）——合并卡片"卡住要刷新才消失"
    // 的根因。无活跃 observer 时 react-query 自动不轮询，折叠/离页零成本。
    refetchInterval: 15000,
  });
}
