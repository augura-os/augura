import { useQuery } from "@tanstack/react-query";
import { fetchRecommendations, refreshRecommendations } from "../services/api";

export function useRecommendations() {
  return useQuery({
    queryKey: ["recommendations"],
    // GET 已纯读化：先 fire 一次有状态刷新（评分 → active→watch 自动流转），
    // 再拉报表——保持"打开看板即见最新 watch 状态"；刷新失败不挡看板
    queryFn: async () => {
      await refreshRecommendations().catch(() => null);
      return fetchRecommendations();
    },
  });
}
