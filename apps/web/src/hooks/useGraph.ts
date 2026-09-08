import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { MergeRequest, SplitRequest } from "@shared";
import { fetchGraph, mergeCreatives, splitCreative } from "../services/api";

export function useGraph() {
  return useQuery({
    queryKey: ["graph"],
    queryFn: fetchGraph,
  });
}

export function useMergeCreatives() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: MergeRequest) => mergeCreatives(body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["graph"] });
      void queryClient.invalidateQueries({ queryKey: ["assets"] });
    },
  });
}

export function useSplitCreative() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: SplitRequest) => splitCreative(body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["graph"] });
      void queryClient.invalidateQueries({ queryKey: ["assets"] });
    },
  });
}
