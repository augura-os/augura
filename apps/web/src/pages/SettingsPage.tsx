import { useEffect, useState } from "react";
import { useReviewQueue } from "../hooks/useReviewQueue";
import { useSettings, useUpdateSettings } from "../hooks/useSettings";
import { fetchAiModels, testAiConnection } from "../services/api";
import {
  DEFAULT_THRESHOLDS,
  JUDGEABLE_METRICS,
  METRIC_LABEL,
  THRESHOLD_LABEL,
} from "../hooks/useMetricConfig";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/card";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { Skeleton } from "../components/ui/skeleton";

const KIMI_PRESET = {
  base_url: "https://api.moonshot.cn/v1",
  vision_model: "kimi-k2.5",
  embedding_model: "",
};

/** 市场前缀 → 规范市场码（与后端 markets.prefix_to_market_code 同规则：
 *  最长后缀命中码表 → 取码，EN 归一为 US；未命中 → 整段原样）。 */
const marketCodeOf = (prefix: string, codes: string[]): string => {
  const raw = prefix.trim().toUpperCase();
  const known = new Set([...codes, "EN"]);
  for (let size = raw.length; size >= 2; size--) {
    const suffix = raw.slice(-size);
    if (known.has(suffix)) return suffix === "EN" ? "US" : suffix;
  }
  return raw;
};

export default function SettingsPage() {
  const { data, isLoading, isError, error } = useSettings();
  const updateMutation = useUpdateSettings();
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [visionModel, setVisionModel] = useState("");
  const [embeddingModel, setEmbeddingModel] = useState("");
  const [feedback, setFeedback] = useState<string>("");
  // 模型自动发现 + 连接测试（/settings/ai/*）
  const [modelOptions, setModelOptions] = useState<string[] | null>(null);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [modelsError, setModelsError] = useState("");
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);
  // 素材指标配置（v0.11）
  const [profile, setProfile] = useState<string[]>([]);
  const [judgeMetrics, setJudgeMetrics] = useState<string[]>([]);
  const [thresholds, setThresholds] = useState<Record<string, number>>(DEFAULT_THRESHOLDS);
  // 分市场阈值覆盖：字符串态（空串 = 跟随全局/品类档，不覆盖）
  const [marketThresholds, setMarketThresholds] = useState<Record<string, Record<string, string>>>({});
  // 分市场行的展开状态（已配置覆盖的市场默认展开）
  const [expandedMarkets, setExpandedMarkets] = useState<Set<string>>(new Set());
  const [metricFeedback, setMetricFeedback] = useState<string>("");
  // 遥测实例 ID 掩码（默认只显示尾 4 位，防截图泄露）
  const [showFullId, setShowFullId] = useState(false);
  const { data: reviewQueue } = useReviewQueue();
  const detectedPrefixes = reviewQueue?.market_detects ?? [];
  // 创意评分与生命周期（v0.13）
  const [scoreWeights, setScoreWeights] = useState<Record<string, number>>({});
  const [archiveScoreThreshold, setArchiveScoreThreshold] = useState<number>(30);
  const [archiveIdleDays, setArchiveIdleDays] = useState<number>(7);
  const [scoreFeedback, setScoreFeedback] = useState<string>("");

  useEffect(() => {
    if (data) {
      setBaseUrl(data.base_url);
      setVisionModel(data.vision_model);
      setEmbeddingModel(data.embedding_model);
      setProfile(data.metric_profile ?? []);
      setJudgeMetrics(data.judge_metrics ?? []);
      setThresholds({ ...DEFAULT_THRESHOLDS, ...(data.metric_thresholds ?? {}) });
      // 分市场覆盖：数值转字符串进输入框（"" = 不覆盖）
      setMarketThresholds(
        Object.fromEntries(
          Object.entries(data.market_thresholds ?? {}).map(([market, entries]) => [
            market,
            Object.fromEntries(
              Object.entries(entries).map(([key, value]) => [key, String(value)]),
            ),
          ]),
        ),
      );
      setScoreWeights(data.score_weights ?? {});
      setArchiveScoreThreshold(data.archive_score_threshold ?? 30);
      setArchiveIdleDays(data.archive_idle_days ?? 7);
    }
  }, [data]);

  const applyKimiPreset = () => {
    setBaseUrl(KIMI_PRESET.base_url);
    setVisionModel(KIMI_PRESET.vision_model);
    setEmbeddingModel(KIMI_PRESET.embedding_model);
  };

  // 拉取模型列表：成功 → Vision model 输入框获得下拉提示（仍可手填）；
  // 失败 → 显示人话原因（端点不支持 /models 时提示手填）
  const onFetchModels = () => {
    setModelsLoading(true);
    setModelsError("");
    fetchAiModels()
      .then((list) => setModelOptions(list))
      .catch((err) =>
        setModelsError(err instanceof Error ? err.message : "获取模型列表失败"),
      )
      .finally(() => setModelsLoading(false));
  };

  // 连接测试：验证的是已保存的 Base URL + Key（改动未保存时不生效）
  const onTestConnection = () => {
    setTesting(true);
    setTestResult(null);
    testAiConnection(visionModel.trim() || undefined)
      .then((result) => setTestResult(result))
      .catch((err) =>
        setTestResult({
          ok: false,
          message: err instanceof Error ? err.message : "测试失败",
        }),
      )
      .finally(() => setTesting(false));
  };

  const onSave = () => {
    updateMutation.mutate(
      {
        ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
        base_url: baseUrl.trim(),
        vision_model: visionModel.trim(),
        embedding_model: embeddingModel.trim(),
      },
      {
        onSuccess: () => {
          setApiKey("");
          setFeedback("Saved ✓");
          setTimeout(() => setFeedback(""), 3000);
        },
        onError: (mutationError) => {
          setFeedback(mutationError instanceof Error ? mutationError.message : "Save failed");
        },
      },
    );
  };

  const addDetectedPrefix = (prefix: string) => {
    const current = data?.market_prefixes ?? [];
    if (current.includes(prefix)) return;
    updateMutation.mutate({ market_prefixes: [...current, prefix] });
  };

  const onToggleTelemetry = (enabled: boolean) => {
    updateMutation.mutate({ telemetry_enabled: enabled });
  };

  const onToggleMergeAuto = (enabled: boolean) => {
    updateMutation.mutate({ merge_auto_enabled: enabled });
  };

  const onToggleLifecycleAuto = (enabled: boolean) => {
    updateMutation.mutate({ lifecycle_auto_enabled: enabled });
  };

  const onSaveScore = () => {
    updateMutation.mutate(
      {
        score_weights: scoreWeights,
        archive_score_threshold: archiveScoreThreshold,
        archive_idle_days: archiveIdleDays,
      },
      {
        onSuccess: () => {
          setScoreFeedback("Saved ✓");
          setTimeout(() => setScoreFeedback(""), 3000);
        },
        onError: (mutationError) => {
          setScoreFeedback(mutationError instanceof Error ? mutationError.message : "Save failed");
        },
      },
    );
  };

  const toggleInList = (list: string[], key: string, order: string[]): string[] =>
    list.includes(key)
      ? list.filter((item) => item !== key)
      : order.filter((item) => list.includes(item) || item === key);

  // 分市场阈值行：预置常见市场码 + 已配置但不在码表内的自定义市场
  const presetCodes = Object.keys(data?.market_codes ?? {});
  const marketRows = [
    ...presetCodes,
    ...Object.keys(marketThresholds).filter((code) => !presetCodes.includes(code)),
  ];

  const onSaveMetrics = () => {
    // 分市场覆盖：丢弃空串/非正数，空市场整行不提交
    const marketPayload: Record<string, Record<string, number>> = {};
    for (const [market, entries] of Object.entries(marketThresholds)) {
      const valid = Object.fromEntries(
        Object.entries(entries)
          .filter(([, value]) => value.trim() !== "" && Number(value) > 0)
          .map(([key, value]) => [key, Number(value)]),
      );
      if (Object.keys(valid).length > 0) {
        marketPayload[market] = valid;
      }
    }
    updateMutation.mutate(
      {
        metric_profile: profile,
        judge_metrics: judgeMetrics,
        metric_thresholds: thresholds,
        market_thresholds: marketPayload,
      },
      {
        onSuccess: () => {
          setMetricFeedback("Saved ✓");
          setTimeout(() => setMetricFeedback(""), 3000);
        },
        onError: (mutationError) => {
          setMetricFeedback(mutationError instanceof Error ? mutationError.message : "Save failed");
        },
      },
    );
  };

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-xl space-y-6 p-8">
        <header>
          <h1 className="text-lg font-semibold text-neutral-900">Settings</h1>
          <p className="mt-1 text-sm text-neutral-500">
            Configure the AI provider used for creative analysis. Any OpenAI-compatible
            endpoint works (OpenAI, Kimi, etc.).
          </p>
        </header>

        <Card>
          <CardHeader>
            <CardTitle>AI Provider</CardTitle>
            <CardDescription>
              Stored locally in the Augura database. Required before AI analysis can run.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {isLoading ? (
              <Skeleton className="h-9 w-full" />
            ) : isError ? (
              <p className="text-sm text-red-600">
                {error instanceof Error ? error.message : "Failed to load settings"}
              </p>
            ) : (
              <div className="flex items-center gap-2 text-sm">
                <span className="text-neutral-500">API key:</span>
                {data?.api_key_set ? (
                  <>
                    <code className="rounded bg-neutral-100 px-1.5 py-0.5 text-xs text-neutral-800">
                      {data.api_key_masked}
                    </code>
                    <Badge variant="success">Configured</Badge>
                  </>
                ) : (
                  <Badge variant="secondary">Not configured</Badge>
                )}
              </div>
            )}

            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <Label htmlFor="base_url">Base URL</Label>
                <button
                  type="button"
                  onClick={applyKimiPreset}
                  className="text-xs text-neutral-500 underline-offset-2 hover:text-neutral-900 hover:underline"
                >
                  Use Kimi preset
                </button>
              </div>
              <Input
                id="base_url"
                placeholder="https://api.openai.com/v1"
                value={baseUrl}
                onChange={(event) => setBaseUrl(event.target.value)}
                autoComplete="off"
              />
              <p className="text-xs text-neutral-400">
                Kimi: https://api.moonshot.cn/v1 · OpenAI: https://api.openai.com/v1
              </p>
            </div>

            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <Label htmlFor="vision_model">Vision model</Label>
                {data?.api_key_set && baseUrl.trim() ? (
                  <button
                    type="button"
                    onClick={onFetchModels}
                    disabled={modelsLoading}
                    className="text-xs text-neutral-500 underline-offset-2 hover:text-neutral-900 hover:underline disabled:opacity-40"
                  >
                    {modelsLoading ? "获取中…" : "获取模型列表"}
                  </button>
                ) : null}
              </div>
              <Input
                id="vision_model"
                placeholder="gpt-4o"
                value={visionModel}
                onChange={(event) => setVisionModel(event.target.value)}
                autoComplete="off"
                list={modelOptions ? "ai-model-options" : undefined}
              />
              {modelOptions ? (
                <datalist id="ai-model-options">
                  {modelOptions.map((model) => (
                    <option key={model} value={model} />
                  ))}
                </datalist>
              ) : null}
              {modelsError ? (
                <p className="text-xs text-red-600">{modelsError}</p>
              ) : modelOptions ? (
                <p className="text-xs text-neutral-400">
                  已加载 {modelOptions.length} 个可用模型，可直接选择或手填
                </p>
              ) : (
                <p className="text-xs text-neutral-400">
                  Kimi: kimi-k2.5 / moonshot-v1-8k-vision-preview · OpenAI: gpt-4o
                </p>
              )}
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="embedding_model">Embedding model (optional)</Label>
              <Input
                id="embedding_model"
                placeholder="text-embedding-3-small"
                value={embeddingModel}
                onChange={(event) => setEmbeddingModel(event.target.value)}
                autoComplete="off"
              />
              <p className="text-xs text-neutral-400">
                Used for creative clustering. Leave empty when the provider has no embeddings
                endpoint (e.g. Kimi) — clustering falls back to local text similarity.
              </p>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="api_key">{data?.api_key_set ? "Replace API key" : "API key"}</Label>
              <Input
                id="api_key"
                type="password"
                placeholder="sk-..."
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
                autoComplete="off"
              />
            </div>

            <div className="flex items-center gap-3">
              <Button onClick={onSave} disabled={updateMutation.isPending}>
                {updateMutation.isPending ? "Saving…" : "Save"}
              </Button>
              {data?.api_key_set && baseUrl.trim() ? (
                <Button
                  variant="outline"
                  onClick={onTestConnection}
                  disabled={testing}
                >
                  {testing ? "测试中…" : "测试连接"}
                </Button>
              ) : null}
              {feedback ? (
                <span
                  className={
                    feedback.startsWith("Saved") ? "text-sm text-emerald-600" : "text-sm text-red-600"
                  }
                >
                  {feedback}
                </span>
              ) : null}
              {testResult ? (
                <span
                  className={testResult.ok ? "text-sm text-emerald-600" : "text-sm text-red-600"}
                >
                  {testResult.message}
                </span>
              ) : null}
            </div>
            {data?.api_key_set ? (
              <p className="text-xs text-neutral-400">
                连接测试与模型列表基于已保存的 Base URL 和 Key——改过请先 Save。
              </p>
            ) : null}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>素材指标</CardTitle>
            <CardDescription>
              选择你关注的指标（看板与详情按此显示），以及哪些指标参与 KEEP / ITERATE / PAUSE 判定和对应阈值。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-1.5">
              <Label>关注指标（显示）</Label>
              <div className="grid grid-cols-3 gap-1.5">
                {Object.entries(METRIC_LABEL).map(([key, label]) => (
                  <label key={key} className="flex items-center gap-1.5 text-sm text-neutral-800">
                    <input
                      type="checkbox"
                      className="h-3.5 w-3.5 accent-neutral-900"
                      checked={profile.includes(key)}
                      onChange={() =>
                        setProfile((prev) => toggleInList(prev, key, Object.keys(METRIC_LABEL)))
                      }
                    />
                    {label}
                  </label>
                ))}
              </div>
            </div>

            <div className="space-y-1.5">
              <Label>判定指标（参与今日建议判定）</Label>
              <div className="grid grid-cols-3 gap-1.5">
                {JUDGEABLE_METRICS.map((key) => (
                  <label key={key} className="flex items-center gap-1.5 text-sm text-neutral-800">
                    <input
                      type="checkbox"
                      className="h-3.5 w-3.5 accent-neutral-900"
                      checked={judgeMetrics.includes(key)}
                      onChange={() =>
                        setJudgeMetrics((prev) =>
                          toggleInList(prev, key, [...JUDGEABLE_METRICS]),
                        )
                      }
                    />
                    {METRIC_LABEL[key] ?? key}
                  </label>
                ))}
              </div>
            </div>

            <div className="space-y-1.5">
              <Label>判定阈值</Label>
              <div className="grid grid-cols-2 gap-x-4 gap-y-2">
                {Object.entries(THRESHOLD_LABEL).map(([key, label]) => (
                  <div key={key} className="space-y-1">
                    <span className="text-xs text-neutral-500">{label}</span>
                    <Input
                      type="number"
                      step="any"
                      value={thresholds[key] ?? ""}
                      onChange={(event) =>
                        setThresholds((prev) => ({
                          ...prev,
                          [key]: Number(event.target.value),
                        }))
                      }
                    />
                  </div>
                ))}
              </div>
            </div>

            <div className="space-y-1.5">
              <Label>分市场阈值覆盖（可选，按市场码）</Label>
              <p className="text-xs text-neutral-400">
                市场 = 语言区（PT/ES/US…），文件名混合前缀自动归一到码。
                不同市场 CPP/ROAS 量级不同（如美国 CPM 是巴西的数倍）。未配置的市场
                显示「跟随全局」，点击展开编辑。跨市场裂变判定会自动改用目标市场内相对口径。
              </p>
              {detectedPrefixes.length > 0 ? (
                <div className="flex flex-wrap items-center gap-1.5 rounded-md border border-cyan-200 bg-cyan-50 px-2 py-1.5">
                  <span className="text-[11px] text-cyan-800">检测到未配置前缀：</span>
                  {detectedPrefixes.map((item) => (
                    <button
                      key={item.title}
                      type="button"
                      title={`${item.reason}——点击加入市场前缀配置`}
                      disabled={updateMutation.isPending}
                      onClick={() => addDetectedPrefix(item.title.replace("市场前缀 ", ""))}
                      className="rounded bg-white px-1.5 py-0.5 text-[11px] font-medium text-cyan-700 ring-1 ring-cyan-300 transition hover:bg-cyan-100"
                    >
                      {item.title.replace("市场前缀 ", "")}＋加入
                    </button>
                  ))}
                </div>
              ) : null}
              {marketRows.map((code) => {
                const entries = marketThresholds[code] ?? {};
                const covered = Object.values(entries).filter((v) => v.trim() !== "").length;
                const expanded = expandedMarkets.has(code) || covered > 0;
                const linkedPrefixes = (data?.market_prefixes ?? []).filter(
                  (prefix) => marketCodeOf(prefix, presetCodes) === code,
                );
                return (
                  <div key={code} className="rounded-md border border-neutral-200 p-3">
                    <button
                      type="button"
                      onClick={() =>
                        setExpandedMarkets((prev) => {
                          const next = new Set(prev);
                          if (next.has(code)) next.delete(code);
                          else next.add(code);
                          return next;
                        })
                      }
                      className="flex w-full items-center justify-between text-left"
                    >
                      <span className="text-xs font-medium text-neutral-700">
                        {code}（{data?.market_codes?.[code] ?? "自定义市场"}）
                        {linkedPrefixes.length > 0 ? (
                          <span className="ml-1 font-normal text-neutral-400">
                            前缀 {linkedPrefixes.join(" / ")}
                          </span>
                        ) : null}
                      </span>
                      <span className="text-[11px] text-neutral-400">
                        {covered > 0 ? `已覆盖 ${covered} 项` : "跟随全局"}
                      </span>
                    </button>
                    {expanded ? (
                      <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-2">
                        {Object.entries(THRESHOLD_LABEL).map(([key, label]) => (
                          <div key={key} className="space-y-1">
                            <span className="text-xs text-neutral-500">{label}</span>
                            <Input
                              type="number"
                              step="any"
                              placeholder={`跟随全局 ${thresholds[key] ?? ""}`}
                              value={entries[key] ?? ""}
                              onChange={(event) =>
                                setMarketThresholds((prev) => ({
                                  ...prev,
                                  [code]: {
                                    ...(prev[code] ?? {}),
                                    [key]: event.target.value,
                                  },
                                }))
                              }
                            />
                          </div>
                        ))}
                      </div>
                    ) : null}
                  </div>
                );
              })}
            </div>

            <div className="flex items-center gap-3">
              <Button onClick={onSaveMetrics} disabled={updateMutation.isPending || profile.length === 0}>
                {updateMutation.isPending ? "Saving…" : "保存指标配置"}
              </Button>
              {metricFeedback ? (
                <span
                  className={
                    metricFeedback.startsWith("Saved") ? "text-sm text-emerald-600" : "text-sm text-red-600"
                  }
                >
                  {metricFeedback}
                </span>
              ) : null}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>创意评分与归档</CardTitle>
            <CardDescription>
              四要素加权出 0-100 分（管可见性，不影响推荐判定）。评分低于阈值且长期无消耗的
              创意进收件箱「建议归档」，人工确认才归档；归档不删数据，随时可恢复。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-1.5">
              <Label>评分权重（不按 100 归一也没关系，保存时按比例生效）</Label>
              <div className="grid grid-cols-2 gap-x-4 gap-y-2">
                {(
                  [
                    ["performance", "效果（CPP/ROAS）"],
                    ["freshness", "新鲜度（闲置衰减）"],
                    ["evolution", "演化力（裂变有效率）"],
                    ["confidence", "置信度（AI 分析）"],
                  ] as const
                ).map(([key, label]) => (
                  <div key={key} className="space-y-1">
                    <span className="text-xs text-neutral-500">{label}</span>
                    <Input
                      type="number"
                      step="any"
                      value={scoreWeights[key] ?? ""}
                      onChange={(event) =>
                        setScoreWeights((prev) => ({
                          ...prev,
                          [key]: Number(event.target.value),
                        }))
                      }
                    />
                  </div>
                ))}
              </div>
            </div>

            <div className="grid grid-cols-2 gap-x-4 gap-y-2">
              <div className="space-y-1">
                <span className="text-xs text-neutral-500">建议归档评分阈值（低于此分）</span>
                <Input
                  type="number"
                  step="any"
                  value={archiveScoreThreshold}
                  onChange={(event) => setArchiveScoreThreshold(Number(event.target.value))}
                />
              </div>
              <div className="space-y-1">
                <span className="text-xs text-neutral-500">闲置天数（超过才建议归档）</span>
                <Input
                  type="number"
                  value={archiveIdleDays}
                  onChange={(event) => setArchiveIdleDays(Number(event.target.value))}
                />
              </div>
            </div>

            <label className="flex items-center gap-2 text-sm text-neutral-800">
              <input
                type="checkbox"
                className="h-4 w-4 accent-neutral-900"
                checked={data?.lifecycle_auto_enabled ?? true}
                disabled={updateMutation.isPending}
                onChange={(event) => onToggleLifecycleAuto(event.target.checked)}
              />
              低分自动标记为「观察」（归档永远人工确认，不自动）
            </label>

            <div className="flex items-center gap-3">
              <Button onClick={onSaveScore} disabled={updateMutation.isPending}>
                {updateMutation.isPending ? "Saving…" : "保存评分配置"}
              </Button>
              {scoreFeedback ? (
                <span
                  className={
                    scoreFeedback.startsWith("Saved") ? "text-sm text-emerald-600" : "text-sm text-red-600"
                  }
                >
                  {scoreFeedback}
                </span>
              ) : null}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>自动判定</CardTitle>
            <CardDescription>
              分析完成后自动跑 DNA 归族与合并预裁。开启自动合并后，仅当 LLM 3/3 判同一创意
              且双视频 pHash 帧对齐率 ≥ 90%（且无既定拆分裁决）时才直接执行合并，
              其余一律只进收件箱建议——自动执行会在操作日志标注 auto: 前缀。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <label className="flex items-center gap-2 text-sm text-neutral-800">
              <input
                type="checkbox"
                className="h-4 w-4 accent-neutral-900"
                checked={data?.merge_auto_enabled ?? false}
                disabled={updateMutation.isPending}
                onChange={(event) => onToggleMergeAuto(event.target.checked)}
              />
              允许自动合并候选 Creative（默认关闭）
            </label>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>创意基因共建计划</CardTitle>
            <CardDescription>
              你的每一次判断都在训练一个更懂创意的系统——判定建议更准、品类基准更全。
              回传白名单字段（功能点击 / 修正行为 / 品类聚合桶），素材名哈希脱敏，
              **绝不回传素材内容与投放明细**。默认开启，可随时关闭。
              详见 PRIVACY.md 与 apps/api/telemetry/（全部可审计）。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <label className="flex items-center gap-2 text-sm text-neutral-800">
              <input
                type="checkbox"
                className="h-4 w-4 accent-neutral-900"
                checked={data?.telemetry_enabled ?? true}
                disabled={updateMutation.isPending}
                onChange={(event) => onToggleTelemetry(event.target.checked)}
              />
              加入创意基因共建计划（你的判断让基因库更懂创意）
            </label>
            <div className="rounded-md bg-neutral-50 px-3 py-2 text-xs text-neutral-500">
              <span className="font-medium text-neutral-700">运行保障数据</span>
              （报错与版本信息，用于兼容性支持）为运行所必需，始终开启——
              不含任何素材、投放数据或可识别身份的信息。
            </div>
            {data?.telemetry_instance_id ? (
              <div className="space-y-2">
                <p className="text-xs text-neutral-400">
                  匿名实例 ID（删除数据时提供给我们；默认掩码防截图泄露）：
                  <code className="ml-1 rounded bg-neutral-100 px-1.5 py-0.5 text-[11px] text-neutral-600">
                    {showFullId
                      ? data.telemetry_instance_id
                      : `••••••••${data.telemetry_instance_id.slice(-4)}`}
                  </code>
                  <button
                    type="button"
                    onClick={() => setShowFullId((v) => !v)}
                    className="ml-2 text-[11px] text-neutral-500 underline underline-offset-2 hover:text-neutral-900"
                  >
                    {showFullId ? "隐藏" : "显示完整"}
                  </button>
                </p>
              </div>
            ) : null}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
