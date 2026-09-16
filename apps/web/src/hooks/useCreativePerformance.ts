import { useQuery } from "@tanstack/react-query";
import { fetchCreativePerformance } from "../services/api";

/** Aggregated Facebook delivery rows of all assets under one creative. */
export function useCreativePerformance(creativeId: string | undefined) {
  return useQuery({
    queryKey: ["creatives", "performance", creativeId],
    queryFn: () => fetchCreativePerformance(creativeId as string),
    enabled: Boolean(creativeId),
  });
}
