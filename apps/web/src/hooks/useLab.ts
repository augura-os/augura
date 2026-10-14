import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { CreateDiagnosisRequest } from "@shared";
import type { Lang } from "../lib/i18n";
import { createLabDiagnosis, getLabAnalysis, getLabVocabGaps } from "../services/api";

/** GET /lab/analysis/{assetId}?lang=：pending/processing 时每 3s 轮询，终态自停。
 *  lang 进 queryKey——语言切换后调用方重渲染传入新 lang 即自动按新语言 refetch。 */
export function useLabAnalysis(assetId: string | null, lang: Lang) {
  return useQuery({
    queryKey: ["lab", "analysis", assetId, lang],
    queryFn: () => getLabAnalysis(assetId as string, lang),
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

/** GET /lab/vocab-gaps：Settings「词表缺口」区块用（items 由后端按 count 降序）。 */
export function useLabVocabGaps() {
  return useQuery({
    queryKey: ["lab", "vocab-gaps"],
    queryFn: getLabVocabGaps,
  });
}
