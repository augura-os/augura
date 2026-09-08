import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { UpdateAssetRequest } from "@shared";
import { fetchAssetDetail, runAnalysis, updateAsset } from "../services/api";

export function useAssetDetail(id: string | undefined) {
  return useQuery({
    queryKey: ["assets", "detail", id],
    queryFn: () => fetchAssetDetail(id as string),
    enabled: Boolean(id),
  });
}

export function useUpdateAsset(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: UpdateAssetRequest) => updateAsset(id, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["assets"] });
      void queryClient.invalidateQueries({ queryKey: ["graph"] });
    },
  });
}

export function useRunAnalysis(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => runAnalysis(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["assets"] });
      void queryClient.invalidateQueries({ queryKey: ["graph"] });
    },
  });
}
