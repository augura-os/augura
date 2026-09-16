import { useQuery } from "@tanstack/react-query";
import { fetchReviewQueue } from "../services/api";

export function useReviewQueue() {
  return useQuery({
    queryKey: ["review-queue"],
    queryFn: fetchReviewQueue,
  });
}
