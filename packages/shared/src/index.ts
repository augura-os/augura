/**
 * Augura shared types — the single TS source of truth for the API contract
 * defined in docs/AGENT_SPEC.md §3/§4. Pure source package, no build step.
 * Consumed by apps/web via the `@shared` alias (tsconfig paths + vite alias).
 */

// ---------------------------------------------------------------------------
// Envelope (§3): every endpoint returns { success, data, message }
// ---------------------------------------------------------------------------

export interface ApiResponse<T> {
  success: boolean;
  data: T | null;
  message: string;
}

// ---------------------------------------------------------------------------
// Assets (§3)
// ---------------------------------------------------------------------------

export type FileType = "video" | "image" | "excel";

export type AnalysisStatus = "pending" | "processing" | "completed" | "failed" | "none";

/** GET /assets list item. */
export interface AssetListItem {
  id: string;
  filename: string;
  file_type: FileType;
  thumbnail_url: string | null;
  created_at: string;
  analysis_status: AnalysisStatus;
  creative_name: string | null;
  /** AI confidence (0-1); null when no analysis. < 0.7 needs human review. */
  confidence: number | null;
  /** 所属 creative 的生命周期（active/watch/archived）；未归族为 null */
  lifecycle_state: string | null;
}

/** Asset object embedded in GET /assets/{id}. */
export interface Asset {
  id: string;
  filename: string;
  file_type: FileType;
  media_url: string | null;
  thumbnail_url: string | null;
  created_at: string;
  analysis_status: AnalysisStatus;
}

/** Performance row parsed from a Facebook Excel export (§6). */
export interface PerformanceRecord {
  id: string;
  asset_id: string | null;
  creative_name: string | null;
  date: string | null;
  impressions: number | null;
  clicks: number | null;
  spend: number | null;
  installs: number | null;
  /** UA priority metrics (null when the export lacks the column). */
  payers: number | null;
  /** spend / payers. */
  cost_per_payer: number | null;
  /** D1 ROAS as a ratio (0.02 = 2%). */
  d1_roas: number | null;
  /** D3 ROAS as a ratio (0.06 = 6%). */
  d3_roas: number | null;
  /** 次留 (D1 retention) as a ratio (0.35 = 35%). */
  d1_retention: number | null;
  cpi: number | null;
  ipm: number | null;
  raw: Record<string, unknown> | null;
}

/** Creative info embedded in GET /assets/{id}. */
export interface CreativeInfo {
  id: string;
  name: string;
}

/** GET /assets/{id} payload: { asset, analysis, tags, creative, performance }. */
export interface AssetDetail {
  asset: Asset;
  analysis: AnalysisResult | null;
  tags: string[];
  creative: CreativeInfo | null;
  performance: PerformanceRecord[];
  /** Which engine produced the analysis (null when none). */
  engine_version: string | null;
}

// ---------------------------------------------------------------------------
// AI analysis (§4 — strict JSON schema)
// ---------------------------------------------------------------------------

export interface AnalysisResult {
  summary: string;
  hook: string;
  conflict: string;
  gameplay: string;
  reward: string;
  characters: string[];
  environment: string[];
  emotion: string[];
  tags: string[];
  /** Reskin factors (boundary-rules §3.3); decide only the Variant layer. */
  variant_factors: string[];
  creative_name: string;
  confidence: number;
}

// ---------------------------------------------------------------------------
// Graph (§3, §7)
// ---------------------------------------------------------------------------

export type GraphNodeType = "creative" | "variant" | "asset" | "tag" | "dna";

export type GraphEdgeType = "HAS_VARIANT" | "HAS_ASSET" | "HAS_TAG" | "SIMILAR_TO" | "HAS_CREATIVE";

// ---------------------------------------------------------------------------
// Creative recommendations (GET /creatives/recommendations)
// ---------------------------------------------------------------------------

export type RecommendationAction = "KEEP" | "ITERATE" | "PAUSE" | "ARCHIVE";

export interface RecommendationMetrics {
  spend: number;
  payers: number;
  installs: number;
  cpp: number | null;
  roas: number | null;
  cpi: number | null;
  ipm: number | null;
  days_idle: number | null;
  recent_spend: number;
  recent_cpp: number | null;
  variant_count: number;
  /** 可选判定指标（judge_metrics 开启后由后端给出） */
  d3_roas?: number | null;
  d1_retention?: number | null;
}

export interface RecommendationItem {
  creative_id: string;
  creative_name: string;
  dna_code: string | null;
  dna_name: string | null;
  action: RecommendationAction;
  reasons: string[];
  metrics: RecommendationMetrics;
  /** Creative Score 总分（0-100）+ 四要素拆解 */
  score: number | null;
  score_breakdown: Record<string, number>;
  /** 生命周期：active / watch / archived */
  lifecycle_state: string;
}

export interface RecommendationReport {
  generated_at: string;
  date_min: string | null;
  date_max: string | null;
  items: RecommendationItem[];
}

// ---------------------------------------------------------------------------
// Review queue (GET /review/queue)
// ---------------------------------------------------------------------------

export type ReviewKind = "low_confidence" | "dna_unassigned" | "merge_candidate" | "observation_pair" | "pending_verdict" | "derivation_review" | "archive_suggestion" | "market_conflict" | "threshold_calibration" | "market_detect";

export interface ReviewItem {
  kind: ReviewKind;
  title: string;
  reason: string;
  creative_id: string | null;
  creative_name: string | null;
  asset_id: string | null;
  related_creative_id: string | null;
  related_creative_name: string | null;
  derivation_id: string | null;
  /** 短名展示：市场标签 + 文件名尾部区分段（裂变待判定） */
  source_label: string | null;
  target_label: string | null;
  factor: string | null;
  /** LLM 预裁建议（自洽性采样） */
  suggestion: string | null;
  suggestion_votes: number | null;
  suggestion_reason: string | null;
  suggested_dna_id: string | null;
}

export interface JudgeKindStats {
  auto_total: number;
  override_rate: number;
  /** 无已决建议时为 null（没数据 ≠ 0% 采纳） */
  acceptance_rate: number | null;
}

export interface ReviewQueue {
  generated_at: string;
  low_confidence: ReviewItem[];
  dna_unassigned: ReviewItem[];
  merge_candidates: ReviewItem[];
  observation_pairs: ReviewItem[];
  pending_verdicts: ReviewItem[];
  derivation_reviews: ReviewItem[];
  /** 建议归档（score 低 + 长期无消耗；人工确认才归档） */
  archive_suggestions: ReviewItem[];
  /** 市场存疑（文件名前缀 × 分析标签冲突；仅提示人工核对文件名） */
  market_conflicts: ReviewItem[];
  /** 合并阈值校准建议（修正驱动；人工在 Settings 改阈值生效） */
  threshold_calibrations: ReviewItem[];
  /** 检测到的未配置市场前缀（Settings 页一键加入） */
  market_detects: ReviewItem[];
  /** 各建议类别（judge_suggestions.kind）的改判率/采纳率摘要 */
  judge_stats: Record<string, JudgeKindStats>;
}

// ---------------------------------------------------------------------------
// Derivations / evolution (GET /creatives/{id}/evolution, /derivations)
// ---------------------------------------------------------------------------

export type DerivationVerdict = "pending" | "positive" | "negative";
export type DerivationFactor =
  | "intro-sticker"
  | "language-market"
  | "aspect-ratio"
  | "voiceover-copy"
  | "brand-endcard"
  | "character-reskin"
  | "reward-reskin"
  | "live-action-vs-animation"
  | "remake"
  | "unknown";

export interface Derivation {
  id: string;
  source_variant_id: string;
  target_variant_id: string;
  factor: string;
  verdict: DerivationVerdict;
  note: string;
}

export interface VariantBrief {
  variant_id: string;
  name: string;
  filename: string;
  spend: number;
  payers: number;
  cpp: number | null;
  roas: number | null;
}

export interface EvolutionStep {
  derivation: Derivation;
  source: VariantBrief;
  target: VariantBrief;
  cpp_delta: number | null;
  roas_delta: number | null;
}

export interface EvolutionChain {
  creative_id: string;
  steps: EvolutionStep[];
  pending_count: number;
}

// ---------------------------------------------------------------------------
// DNA families (GET /dnas, PUT /creatives/{id}/dna)
// ---------------------------------------------------------------------------

export interface DnaFamily {
  id: string;
  code: string;
  name: string;
}

export interface GraphNodeDTO {
  id: string;
  type: GraphNodeType;
  label: string;
  ref_id: string;
  /** creative 节点的生命周期；其他类型为 null */
  lifecycle_state?: string | null;
}

export interface GraphEdgeDTO {
  id: string;
  source: string;
  target: string;
  type: GraphEdgeType;
}

/** GET /graph payload, rendered by React Flow. */
export interface GraphResponse {
  nodes: GraphNodeDTO[];
  edges: GraphEdgeDTO[];
}

// ---------------------------------------------------------------------------
// Settings (§3)
// ---------------------------------------------------------------------------

export interface SettingsResponse {
  api_key_set: boolean;
  api_key_masked: string;
  base_url: string;
  vision_model: string;
  embedding_model: string;
  telemetry_enabled: boolean;
  telemetry_instance_id: string;
  /** 合并自动执行开关（默认关；LLM 3/3 + pHash 对齐 ≥0.90 才自动合并） */
  merge_auto_enabled: boolean;
  /** 显示指标（顺序即展示顺序）；见 services/settings.DISPLAY_METRICS */
  metric_profile: string[];
  /** 参与 KEEP/ITERATE/PAUSE 判定的指标 */
  judge_metrics: string[];
  /** 判定阈值（cpp_red_line / roas_green_line 等，roas 与留存为比率） */
  metric_thresholds: Record<string, number>;
  /** 分市场阈值覆盖（市场标签 → 阈值键 → 值；空 = 跟随全局/品类档） */
  market_thresholds: Record<string, Record<string, number>>;
  /** 项目品类（"" = 未设置，触发首启引导） */
  project_category: string;
  /** 文件名市场前缀（见 services/markets） */
  market_prefixes: string[];
  /** 规范市场码表（码 → 展示名；Settings 页预置分市场阈值行用） */
  market_codes: Record<string, string>;
  /** 创意评分四要素权重（performance/freshness/evolution/confidence） */
  score_weights: Record<string, number>;
  /** 建议归档阈值：评分低于它且闲置超期 → 进收件箱 */
  archive_score_threshold: number;
  archive_idle_days: number;
  /** 生命周期自动流转总开关（active→watch 自动标记） */
  lifecycle_auto_enabled: boolean;
}

export interface UpdateSettingsRequest {
  api_key?: string;
  base_url?: string;
  vision_model?: string;
  embedding_model?: string;
  telemetry_enabled?: boolean;
  merge_auto_enabled?: boolean;
  metric_profile?: string[];
  judge_metrics?: string[];
  metric_thresholds?: Record<string, number>;
  market_thresholds?: Record<string, Record<string, number>>;
  project_category?: string;
  score_weights?: Record<string, number>;
  archive_score_threshold?: number;
  archive_idle_days?: number;
  lifecycle_auto_enabled?: boolean;
  market_prefixes?: string[];
}

/** GET /settings/ai/models：端点可用模型 id 列表 */
export interface AiModelsInfo {
  models: string[];
}

/** POST /settings/ai/test：连接测试结果（ok=false 时 message 是人话原因） */
export interface AiTestResult {
  ok: boolean;
  message: string;
}

// ---------------------------------------------------------------------------
// Request bodies (§3)
// ---------------------------------------------------------------------------

/** PUT /assets/{id} — editable analysis fields + creative_name. */
export type UpdateAssetRequest = Partial<AnalysisResult>;

/** POST /analysis. */
export interface RunAnalysisRequest {
  asset_id: string;
}

/** POST /graph/merge. */
export interface MergeRequest {
  source_creative_id: string;
  target_creative_id: string;
  /** block 级守卫命中（推翻既定"维持拆分"裁决）时必填的理由 */
  force_reason?: string;
}

/** POST /graph/split. */
export interface SplitRequest {
  creative_id: string;
  variant_ids: string[];
}

/** POST /upload response — the created assets. */
export interface SkippedFile {
  filename: string;
  reason: string;
}

export interface UploadResult {
  uploaded: AssetListItem[];
  skipped: SkippedFile[];
  warnings: string[];
}

export type UploadResponse = UploadResult;
