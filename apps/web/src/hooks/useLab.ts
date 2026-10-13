import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { CreateDiagnosisRequest } from "@shared";
import { createLabDiagnosis, getLabAnalysis } from "../services/api";

/** GET /lab/analysis/{assetId}：pending/processing 时每 3s 轮询，终态自停。 */
export function useLabAnalysis(assetId: string | null) {
  return useQuery({
    queryKey: ["lab", "analysis", assetId],
    queryFn: () => getLabAnalysis(assetId as string),
    enabled: Boolean(assetId),
    refetchInterval: (query) => {
      const status = query.state.data?.analysis_status;
      return status === "pending" || status === "processing" ? 3000 : false;
    },
  });
}

/** POST /lab/diagnoses：成功后让该 asset 的分析视图失效重拉（带回新诊断）。 */
export function useCreateDiagnosis() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateDiagnosisRequest) => createLabDiagnosis(body),
    onSuccess: (diagnosis) => {
      void queryClient.invalidateQueries({
        queryKey: ["lab", "analysis", diagnosis.asset_id],
      });
    },
  });
}
