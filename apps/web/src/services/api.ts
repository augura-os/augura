import type { AxiosResponse } from "axios";
import type {
  AiModelsInfo,
  AiTestResult,
  AnalysisResult,
  ApiResponse,
  AssetDetail,
  AssetListItem,
  Derivation,
  DerivationFactor,
  DerivationVerdict,
  DnaFamily,
  EvolutionChain,
  GraphResponse,
  MergeRequest,
  PerformanceRecord,
  RecommendationReport,
  ReviewQueue,
  RunAnalysisRequest,
  SettingsResponse,
  SplitRequest,
  UpdateAssetRequest,
  UpdateSettingsRequest,
  UploadResponse,
} from "@shared";
import { apiClient } from "./client";

/** Unwrap the { success, data, message } envelope; throws a readable Error on failure. */
async function unwrap<T>(promise: Promise<AxiosResponse<ApiResponse<T>>>): Promise<T> {
  const res = await promise;
  if (!res.data.success) {
    throw new Error(res.data.message || "Request failed");
  }
  return res.data.data as T;
}

// --- Graph -----------------------------------------------------------------

export function fetchGraph(): Promise<GraphResponse> {
  return unwrap(apiClient.get<ApiResponse<GraphResponse>>("/graph"));
}

/** Creative ids created within `hours` (graph NEW badge). */
export function fetchRecentCreativeIds(hours = 48): Promise<string[]> {
  return unwrap(
    apiClient.get<ApiResponse<string[]>>("/creatives/recent-ids", {
      params: { hours },
    }),
  );
}

export function mergeCreatives(body: MergeRequest): Promise<unknown> {
  return unwrap(apiClient.post<ApiResponse<unknown>>("/graph/merge", body));
}

export function linkSimilar(body: {
  source_creative_id: string;
  target_creative_id: string;
  reason?: string;
}): Promise<unknown> {
  return unwrap(apiClient.post<ApiResponse<unknown>>("/graph/similar", body));
}

export function splitCreative(body: SplitRequest): Promise<unknown> {
  return unwrap(apiClient.post<ApiResponse<unknown>>("/graph/split", body));
}

// --- Creatives ---------------------------------------------------------------

export function fetchCreativePerformance(creativeId: string): Promise<PerformanceRecord[]> {
  return unwrap(
    apiClient.get<ApiResponse<PerformanceRecord[]>>(`/creatives/${creativeId}/performance`),
  );
}

export function fetchRecommendations(): Promise<RecommendationReport> {
  return unwrap(apiClient.get<ApiResponse<RecommendationReport>>("/creatives/recommendations"));
}

export function fetchReviewQueue(): Promise<ReviewQueue> {
  return unwrap(apiClient.get<ApiResponse<ReviewQueue>>("/review/queue"));
}

export function fetchEvolution(creativeId: string): Promise<EvolutionChain> {
  return unwrap(apiClient.get<ApiResponse<EvolutionChain>>(`/creatives/${creativeId}/evolution`));
}

export function updateDerivation(
  derivationId: string,
  body: Partial<{ factor: DerivationFactor; verdict: DerivationVerdict; note: string }>,
): Promise<Derivation> {
  return unwrap(apiClient.put<ApiResponse<Derivation>>(`/derivations/${derivationId}`, body));
}

/** DELETE /derivations/{id} — 解链（确认误链后移除 DERIVED_FROM 边）。 */
export function deleteDerivation(derivationId: string): Promise<null> {
  return unwrap(apiClient.delete<ApiResponse<null>>(`/derivations/${derivationId}`));
}

export function fetchDnas(): Promise<DnaFamily[]> {
  return unwrap(apiClient.get<ApiResponse<DnaFamily[]>>("/dnas"));
}

export function assignCreativeDna(
  creativeId: string,
  dnaId: string | null,
): Promise<DnaFamily | null> {
  return unwrap(
    apiClient.put<ApiResponse<DnaFamily | null>>(`/creatives/${creativeId}/dna`, {
      dna_id: dnaId,
    }),
  );
}

export function closeSimilarPair(body: {
  source_creative_id: string;
  target_creative_id: string;
  reason?: string;
}): Promise<unknown> {
  return unwrap(apiClient.post<ApiResponse<unknown>>("/graph/similar/close", body));
}

// --- Assets ----------------------------------------------------------------

export function fetchAssets(search?: string): Promise<AssetListItem[]> {
  return unwrap(
    apiClient.get<ApiResponse<AssetListItem[]>>("/assets", {
      params: search ? { search } : {},
    }),
  );
}

export function fetchAssetDetail(id: string): Promise<AssetDetail> {
  return unwrap(apiClient.get<ApiResponse<AssetDetail>>(`/assets/${id}`));
}

export function updateAsset(id: string, body: UpdateAssetRequest): Promise<AssetDetail> {
  return unwrap(apiClient.put<ApiResponse<AssetDetail>>(`/assets/${id}`, body));
}

// --- Analysis ----------------------------------------------------------------

export function runAnalysis(assetId: string): Promise<AnalysisResult> {
  const body: RunAnalysisRequest = { asset_id: assetId };
  // Analysis runs synchronously and calls the vision model — allow a long wait.
  return unwrap(apiClient.post<ApiResponse<AnalysisResult>>("/analysis", body, { timeout: 180000 }));
}

// --- Settings ----------------------------------------------------------------

export function fetchSettings(): Promise<SettingsResponse> {
  return unwrap(apiClient.get<ApiResponse<SettingsResponse>>("/settings"));
}

export function updateSettings(body: UpdateSettingsRequest): Promise<SettingsResponse> {
  return unwrap(apiClient.put<ApiResponse<SettingsResponse>>("/settings", body));
}

/** 模型自动发现：拉端点 /models 的模型 id 列表（失败时 Error.message 是人话原因）。 */
export function fetchAiModels(): Promise<string[]> {
  return unwrap(apiClient.get<ApiResponse<AiModelsInfo>>("/settings/ai/models")).then(
    (data) => data.models,
  );
}

/** 连接测试：验证已保存的 key+URL；带 visionModel 时验证它在模型列表中。 */
export function testAiConnection(visionModel?: string): Promise<AiTestResult> {
  return unwrap(
    apiClient.post<ApiResponse<AiTestResult>>("/settings/ai/test", {
      vision_model: visionModel || null,
    }),
  );
}

// --- Upload ------------------------------------------------------------------

export function uploadFile(
  file: File,
  onProgress?: (percent: number) => void,
): Promise<UploadResponse> {
  const form = new FormData();
  form.append("files", file);
  return unwrap(
    apiClient.post<ApiResponse<UploadResponse>>("/upload", form, {
      headers: { "Content-Type": "multipart/form-data" },
      timeout: 300000,
      onUploadProgress: (event) => {
        if (onProgress && event.total && event.total > 0) {
          onProgress(Math.min(100, Math.round((event.loaded / event.total) * 100)));
        }
      },
    }),
  );
}

/** PUT /creatives/{id}/lifecycle — 确认归档 / 保留观察 / 恢复。 */
export function updateLifecycle(
  creativeId: string,
  body: { state: "active" | "watch" | "archived"; reason?: string },
): Promise<{ creative_id: string; lifecycle_state: string }> {
  return unwrap(
    apiClient.put<ApiResponse<{ creative_id: string; lifecycle_state: string }>>(
      `/creatives/${creativeId}/lifecycle`,
      body,
    ),
  );
}
