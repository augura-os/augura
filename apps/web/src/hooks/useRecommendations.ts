import { useQuery } from "@tanstack/react-query";
import { fetchRecommendations } from "../services/api";

export function useRecommendations() {
  return useQuery({
    queryKey: ["recommendations"],
    queryFn: fetchRecommendations,
  });
}
