import { useEffect, useRef, useState } from "react";
import type { EmbeddingBackend } from "@shared";
import { useReviewQueue } from "../hooks/useReviewQueue";
import { useSettings, useUpdateSettings } from "../hooks/useSettings";
import { fetchAiModels, testAiConnection } from "../services/api";
import {
  DEFAULT_THRESHOLDS,
  JUDGEABLE_METRICS,
  METRIC_KEYS,
  THRESHOLD_KEYS,
  metricLabel,
  thresholdLabel,
} from "../hooks/useMetricConfig";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/card";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { Skeleton } from "../components/ui/skeleton";
import { useLanguage, useT, type Lang } from "../lib/i18n";
import { cn } from "../lib/utils";

/** 一键预设：填好 base_url + vision_model + embedding_model。
 *  注意两类 Kimi key 不通用：开放平台 key 在 platform.moonshot.cn 创建；
 *  编程套餐 key（sk-kimi- 开头）在 kimi.com 创建，只能走 coding 端点。 */
const PROVIDER_PRESETS = [
  {
    id: "openai",
    base_url: "https://api.openai.com/v1",
    vision_model: "gpt-4o",
    embedding_model: "text-embedding-3-small",
    embedding_backend: "provider",
  },
  {
    id: "kimi",
    base_url: "https://api.moonshot.cn/v1",
    vision_model: "kimi-k2.5",
    embedding_model: "",
    embedding_backend: "local",
  },
  {
    id: "kimi-coding",
    base_url: "https://api.kimi.com/coding/v1",
    vision_model: "kimi-for-coding",
    embedding_model: "",
    embedding_backend: "local",
  },
] as const;

/** 视觉特征关键词：模型列表排序时优先展示，降低误选纯文本/embedding 模型的概率。 */
const VISION_HINT = /vision|gpt-4o|gpt-4\.1|kimi|moonshot-v1|gemini|claude|qwen-vl|\bvl\b/i;

const sortVisionFirst = (models: string[]): string[] =>
  [...models].sort((a, b) => Number(VISION_HINT.test(b)) - Number(VISION_HINT.test(a)));

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

/** 左侧 section 导航（右侧内容区只渲染当前选中的一节）。 */
const SECTIONS = [
  { id: "general", labelKey: "settings.nav.general" },
  { id: "ai", labelKey: "settings.nav.ai" },
  { id: "metrics", labelKey: "settings.nav.metrics" },
  { id: "scoring", labelKey: "settings.nav.scoring" },
  { id: "autojudge", labelKey: "settings.nav.autojudge" },
  { id: "telemetry", labelKey: "settings.nav.telemetry" },
] as const;
type SectionId = (typeof SECTIONS)[number]["id"];

/** 通用 section：界面语言切换（English / 中文 segmented）。 */
function GeneralSection() {
  const t = useT();
  const { lang, setLang } = useLanguage();
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("settings.general.title")}</CardTitle>
        <CardDescription>{t("settings.general.description")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-1.5">
          <Label>{t("settings.general.language")}</Label>
          <div className="inline-flex rounded-full bg-neutral-100 p-0.5">
            {(
              [
                ["en", "English"],
                ["zh", "中文"],
              ] as Array<[Lang, string]>
            ).map(([value, label]) => (
              <button
                key={value}
                type="button"
                onClick={() => setLang(value)}
                className={cn(
                  "rounded-full px-3 py-1 text-xs font-medium transition",
                  lang === value
                    ? "bg-neutral-900 text-white"
                    : "text-neutral-500 hover:text-neutral-800",
                )}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

export default function SettingsPage() {
  const t = useT();
  const { data, isLoading, isError, error } = useSettings();
  const updateMutation = useUpdateSettings();
  const [section, setSection] = useState<SectionId>("general");
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [visionModel, setVisionModel] = useState("");
  // datalist 会按输入内容做子串过滤：输入框已有完整模型名时，聚焦先临时
  // 清空让全量列表显示；未做选择直接失焦则恢复原值。
  const visionStash = useRef<string | null>(null);
  const [embeddingModel, setEmbeddingModel] = useState("");
  const [embeddingBackend, setEmbeddingBackend] = useState<EmbeddingBackend>("local");
  const [judgeTemperature, setJudgeTemperature] = useState("0.7");
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
      setEmbeddingBackend(data.embedding_backend ?? "local");
      setJudgeTemperature(String(data.judge_temperature ?? 0.7));
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

  // 已配置 key+base_url 时自动拉一次模型列表（静默失败：端点不支持 /models 时保持手填）
  useEffect(() => {
    if (!data?.api_key_set || !data.base_url) return;
    fetchAiModels()
      .then((list) => setModelOptions(sortVisionFirst(list)))
      .catch(() => {});
  }, [data?.api_key_set, data?.base_url]);

  const applyPreset = (preset: (typeof PROVIDER_PRESETS)[number]) => {
    setBaseUrl(preset.base_url);
    setVisionModel(preset.vision_model);
    setEmbeddingModel(preset.embedding_model);
    setEmbeddingBackend(preset.embedding_backend);
  };

  // 拉取模型列表：成功 → Vision model 输入框获得下拉提示（仍可手填）；
  // 失败 → 显示人话原因（端点不支持 /models 时提示手填）。
  // silent=true 用于自动拉取：失败静默降级，不打扰用户。
  const onFetchModels = (silent = false) => {
    setModelsLoading(true);
    if (!silent) setModelsError("");
    fetchAiModels()
      .then((list) => setModelOptions(sortVisionFirst(list)))
      .catch((err) => {
        if (!silent) {
          setModelsError(err instanceof Error ? err.message : t("settings.modelsFetchFailed"));
        }
      })
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
          message: err instanceof Error ? err.message : t("settings.testFailed"),
        }),
      )
      .finally(() => setTesting(false));
  };

  const onSave = () => {
    const model = visionModel.trim();
    const url = baseUrl.trim().replace(/\/+$/, "");
    // Base URL 没有任何路径段（如 https://api.moonshot.cn）时分析必报 404 url.not_found
    if (
      url &&
      /^https?:\/\/[^/]+$/.test(url) &&
      !window.confirm(t("settings.confirmBaseUrl"))
    ) {
      return;
    }
    // 已拿到端点模型列表时校验拼写：不在列表中需用户确认，防止打错后跑分析才报错
    if (
      modelOptions &&
      model &&
      !modelOptions.includes(model) &&
      !window.confirm(t("settings.confirmModel").replace("{model}", model))
    ) {
      return;
    }
    updateMutation.mutate(
      {
        ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
        base_url: baseUrl.trim(),
        vision_model: model,
        embedding_model: embeddingModel.trim(),
        embedding_backend: embeddingBackend,
        ...(Number.isFinite(Number(judgeTemperature)) && judgeTemperature.trim() !== ""
          ? { judge_temperature: Number(judgeTemperature) }
          : {}),
      },
      {
        onSuccess: () => {
          setApiKey("");
          setFeedback(t("common.saved"));
          setTimeout(() => setFeedback(""), 3000);
          // 保存成功后自动刷新模型列表（key/URL 可能刚改），失败静默降级
          if (baseUrl.trim()) onFetchModels(true);
        },
        onError: (mutationError) => {
          setFeedback(mutationError instanceof Error ? mutationError.message : t("common.saveFailed"));
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
          setScoreFeedback(t("common.saved"));
          setTimeout(() => setScoreFeedback(""), 3000);
        },
        onError: (mutationError) => {
          setScoreFeedback(mutationError instanceof Error ? mutationError.message : t("common.saveFailed"));
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
          setMetricFeedback(t("common.saved"));
          setTimeout(() => setMetricFeedback(""), 3000);
        },
        onError: (mutationError) => {
          setMetricFeedback(mutationError instanceof Error ? mutationError.message : t("common.saveFailed"));
        },
      },
    );
  };

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-4xl p-8">
        <header>
          <h1 className="text-lg font-semibold text-neutral-900">{t("settings.title")}</h1>
          <p className="mt-1 text-sm text-neutral-500">
            {t("settings.subtitle")}
          </p>
        </header>

        <div className="mt-6 flex items-start gap-6">
          <nav className="w-48 shrink-0 space-y-0.5">
            {SECTIONS.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => setSection(item.id)}
                className={cn(
                  "w-full rounded-md px-3 py-1.5 text-left text-sm transition",
                  section === item.id
                    ? "bg-neutral-100 font-medium text-neutral-900"
                    : "text-neutral-500 hover:bg-neutral-50 hover:text-neutral-800",
                )}
              >
                {t(item.labelKey)}
              </button>
            ))}
          </nav>

          <div className="min-w-0 flex-1">
        {section === "general" ? <GeneralSection /> : null}

        {section === "ai" ? (
        <Card>
          <CardHeader>
            <CardTitle>{t("settings.aiProvider.title")}</CardTitle>
            <CardDescription>
              {t("settings.aiProvider.description")}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {isLoading ? (
              <Skeleton className="h-9 w-full" />
            ) : isError ? (
              <p className="text-sm text-red-600">
                {error instanceof Error ? error.message : t("settings.loadFailed")}
              </p>
            ) : (
              <div className="flex items-center gap-2 text-sm">
                <span className="text-neutral-500">{t("settings.apiKey")}</span>
                {data?.api_key_set ? (
                  <>
                    <code className="rounded bg-neutral-100 px-1.5 py-0.5 text-xs text-neutral-800">
                      {data.api_key_masked}
                    </code>
                    <Badge variant="success">{t("settings.configured")}</Badge>
                  </>
                ) : (
                  <Badge variant="secondary">{t("settings.notConfigured")}</Badge>
                )}
              </div>
            )}

            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <Label htmlFor="base_url">Base URL</Label>
                <div className="flex items-center gap-2.5">
                  {PROVIDER_PRESETS.map((preset) => (
                    <button
                      key={preset.id}
                      type="button"
                      title={t(`settings.preset.${preset.id}.note`) || preset.base_url}
                      onClick={() => applyPreset(preset)}
                      className="text-xs text-neutral-500 underline-offset-2 hover:text-neutral-900 hover:underline"
                    >
                      {t(`settings.preset.${preset.id}.label`)}
                    </button>
                  ))}
                </div>
              </div>
              <Input
                id="base_url"
                placeholder="https://api.openai.com/v1"
                value={baseUrl}
                onChange={(event) => setBaseUrl(event.target.value)}
                autoComplete="off"
              />
              <p className="text-xs text-neutral-400">
                {t("settings.baseUrlHint")}
              </p>
            </div>

            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <Label htmlFor="vision_model">{t("settings.visionModel")}</Label>
                {data?.api_key_set && baseUrl.trim() ? (
                  <button
                    type="button"
                    onClick={() => onFetchModels()}
                    disabled={modelsLoading}
                    className="text-xs text-neutral-500 underline-offset-2 hover:text-neutral-900 hover:underline disabled:opacity-40"
                  >
                    {modelsLoading ? t("settings.loadingModels") : t("settings.refreshModels")}
                  </button>
                ) : null}
              </div>
              <Input
                id="vision_model"
                placeholder="gpt-4o"
                value={visionModel}
                onChange={(event) => setVisionModel(event.target.value)}
                onFocus={() => {
                  if (modelOptions?.includes(visionModel.trim())) {
                    visionStash.current = visionModel;
                    setVisionModel("");
                  }
                }}
                onBlur={() => {
                  if (visionStash.current !== null && visionModel.trim() === "") {
                    setVisionModel(visionStash.current);
                  }
                  visionStash.current = null;
                }}
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
                  {t("settings.modelsLoaded").replace("{n}", String(modelOptions.length))}
                </p>
              ) : (
                <p className="text-xs text-neutral-400">
                  {t("settings.modelsHint")}
                </p>
              )}
            </div>

            <div className="space-y-1.5">
              <Label>{t("settings.embeddingBackend")}</Label>
              <div className="inline-flex rounded-full bg-neutral-100 p-0.5">
                {(
                  [
                    ["local", t("settings.embeddingBackend.local")],
                    ["provider", t("settings.embeddingBackend.provider")],
                    ["off", t("settings.embeddingBackend.off")],
                  ] as Array<[EmbeddingBackend, string]>
                ).map(([value, label]) => (
                  <button
                    key={value}
                    type="button"
                    onClick={() => setEmbeddingBackend(value)}
                    className={cn(
                      "rounded-full px-3 py-1 text-xs font-medium transition",
                      embeddingBackend === value
                        ? "bg-neutral-900 text-white"
                        : "text-neutral-500 hover:text-neutral-800",
                    )}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <p className="text-xs text-neutral-400">
                {t(`settings.embeddingBackendHint.${embeddingBackend}`)}
              </p>
            </div>

            {embeddingBackend === "provider" ? (
              <div className="space-y-1.5">
                <Label htmlFor="embedding_model">{t("settings.embeddingModel")}</Label>
                <Input
                  id="embedding_model"
                  placeholder="text-embedding-3-small"
                  value={embeddingModel}
                  onChange={(event) => setEmbeddingModel(event.target.value)}
                  autoComplete="off"
                />
                <p className="text-xs text-neutral-400">
                  {t("settings.embeddingHint")}
                </p>
              </div>
            ) : null}

            <div className="space-y-1.5">
              <Label htmlFor="judge_temperature">{t("settings.judgeTemperature")}</Label>
              <Input
                id="judge_temperature"
                type="number"
                min={0}
                max={1}
                step={0.1}
                value={judgeTemperature}
                onChange={(event) => setJudgeTemperature(event.target.value)}
                autoComplete="off"
              />
              <p className="text-xs text-neutral-400">
                {t("settings.judgeTemperatureHint")}
              </p>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="api_key">{data?.api_key_set ? t("settings.replaceApiKey") : "API key"}</Label>
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
                {updateMutation.isPending ? t("common.saving") : t("settings.save")}
              </Button>
              {data?.api_key_set && baseUrl.trim() ? (
                <Button
                  variant="outline"
                  onClick={onTestConnection}
                  disabled={testing}
                >
                  {testing ? t("settings.testing") : t("settings.testConnection")}
                </Button>
              ) : null}
              {feedback ? (
                <span
                  className={
                    feedback === t("common.saved") ? "text-sm text-emerald-600" : "text-sm text-red-600"
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
                {t("settings.savedNote")}
              </p>
            ) : null}
          </CardContent>
        </Card>
        ) : null}

        {section === "metrics" ? (
        <Card>
          <CardHeader>
            <CardTitle>{t("settings.metrics.title")}</CardTitle>
            <CardDescription>
              {t("settings.metrics.description")}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-1.5">
              <Label>{t("settings.metrics.tracked")}</Label>
              <div className="grid grid-cols-3 gap-1.5">
                {METRIC_KEYS.map((key) => (
                  <label key={key} className="flex items-center gap-1.5 text-sm text-neutral-800">
                    <input
                      type="checkbox"
                      className="h-3.5 w-3.5 accent-neutral-900"
                      checked={profile.includes(key)}
                      onChange={() =>
                        setProfile((prev) => toggleInList(prev, key, [...METRIC_KEYS]))
                      }
                    />
                    {metricLabel(key)}
                  </label>
                ))}
              </div>
            </div>

            <div className="space-y-1.5">
              <Label>{t("settings.metrics.judging")}</Label>
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
                    {metricLabel(key)}
                  </label>
                ))}
              </div>
            </div>

            <div className="space-y-1.5">
              <Label>{t("settings.metrics.thresholds")}</Label>
              <div className="grid grid-cols-2 gap-x-4 gap-y-2">
                {THRESHOLD_KEYS.map((key) => (
                  <div key={key} className="space-y-1">
                    <span className="text-xs text-neutral-500">{thresholdLabel(key)}</span>
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
              <Label>{t("settings.metrics.overrides")}</Label>
              <p className="text-xs text-neutral-400">
                {t("settings.metrics.marketHint")}
              </p>
              {detectedPrefixes.length > 0 ? (
                <div className="flex flex-wrap items-center gap-1.5 rounded-md border border-cyan-200 bg-cyan-50 px-2 py-1.5">
                  <span className="text-[11px] text-cyan-800">{t("settings.metrics.detected")}</span>
                  {detectedPrefixes.map((item) => (
                    <button
                      key={item.title}
                      type="button"
                      // title 由后端 review.py 生成为「市场前缀 {code}」，仍是中文；
                      // 此处按原文剥离前缀。后端文案翻译时必须同步改为错误码/结构化字段。
                      title={t("settings.metrics.addPrefixTitle").replace("{reason}", item.reason)}
                      disabled={updateMutation.isPending}
                      onClick={() => addDetectedPrefix(item.title.replace("市场前缀 ", ""))}
                      className="rounded bg-white px-1.5 py-0.5 text-[11px] font-medium text-cyan-700 ring-1 ring-cyan-300 transition hover:bg-cyan-100"
                    >
                      {item.title.replace("市场前缀 ", "")} {t("settings.metrics.addPrefix")}
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
                        {code} ({data?.market_codes?.[code] ?? t("settings.metrics.customMarket")})
                        {linkedPrefixes.length > 0 ? (
                          <span className="ml-1 font-normal text-neutral-400">
                            {t("settings.metrics.prefixes")} {linkedPrefixes.join(" / ")}
                          </span>
                        ) : null}
                      </span>
                      <span className="text-[11px] text-neutral-400">
                        {covered > 0
                          ? t("settings.metrics.overridden").replace("{n}", String(covered))
                          : t("settings.metrics.followGlobal")}
                      </span>
                    </button>
                    {expanded ? (
                      <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-2">
                        {THRESHOLD_KEYS.map((key) => (
                          <div key={key} className="space-y-1">
                            <span className="text-xs text-neutral-500">{thresholdLabel(key)}</span>
                            <Input
                              type="number"
                              step="any"
                              placeholder={t("settings.metrics.followGlobalValue").replace(
                                "{value}",
                                String(thresholds[key] ?? ""),
                              )}
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
                {updateMutation.isPending ? t("common.saving") : t("settings.metrics.save")}
              </Button>
              {metricFeedback ? (
                <span
                  className={
                    metricFeedback === t("common.saved") ? "text-sm text-emerald-600" : "text-sm text-red-600"
                  }
                >
                  {metricFeedback}
                </span>
              ) : null}
            </div>
          </CardContent>
        </Card>
        ) : null}

        {section === "scoring" ? (
        <Card>
          <CardHeader>
            <CardTitle>{t("settings.scoring.title")}</CardTitle>
            <CardDescription>
              {t("settings.scoring.description")}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-1.5">
              <Label>{t("settings.scoring.weights")}</Label>
              <div className="grid grid-cols-2 gap-x-4 gap-y-2">
                {(
                  [
                    ["performance", t("settings.scoring.weightPerformance")],
                    ["freshness", t("settings.scoring.weightFreshness")],
                    ["evolution", t("settings.scoring.weightEvolution")],
                    ["confidence", t("settings.scoring.weightConfidence")],
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
                <span className="text-xs text-neutral-500">{t("settings.scoring.archiveThreshold")}</span>
                <Input
                  type="number"
                  step="any"
                  value={archiveScoreThreshold}
                  onChange={(event) => setArchiveScoreThreshold(Number(event.target.value))}
                />
              </div>
              <div className="space-y-1">
                <span className="text-xs text-neutral-500">{t("settings.scoring.idleDays")}</span>
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
              {t("settings.scoring.autoWatch")}
            </label>

            <div className="flex items-center gap-3">
              <Button onClick={onSaveScore} disabled={updateMutation.isPending}>
                {updateMutation.isPending ? t("common.saving") : t("settings.scoring.save")}
              </Button>
              {scoreFeedback ? (
                <span
                  className={
                    scoreFeedback === t("common.saved") ? "text-sm text-emerald-600" : "text-sm text-red-600"
                  }
                >
                  {scoreFeedback}
                </span>
              ) : null}
            </div>
          </CardContent>
        </Card>
        ) : null}

        {section === "autojudge" ? (
        <Card>
          <CardHeader>
            <CardTitle>{t("settings.autojudge.title")}</CardTitle>
            <CardDescription>
              {t("settings.autojudge.description")}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <label className="flex items-center gap-2 text-sm text-neutral-800">
              <input
                type="checkbox"
                className="h-4 w-4 accent-neutral-900"
                checked={data?.merge_auto_enabled ?? true}
                disabled={updateMutation.isPending}
                onChange={(event) => onToggleMergeAuto(event.target.checked)}
              />
              {t("settings.autojudge.allowMerge")}
            </label>
          </CardContent>
        </Card>
        ) : null}

        {section === "telemetry" ? (
        <Card>
          <CardHeader>
            <CardTitle>{t("settings.telemetry.title")}</CardTitle>
            <CardDescription>
              {t("settings.telemetry.desc1")}
              <span className="font-medium text-neutral-700">
                {t("settings.telemetry.descBold")}
              </span>
              {t("settings.telemetry.desc2")}
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
              {t("settings.telemetry.join")}
            </label>
            <div className="rounded-md bg-neutral-50 px-3 py-2 text-xs text-neutral-500">
              <span className="font-medium text-neutral-700">{t("settings.telemetry.opBold")}</span>
              {t("settings.telemetry.opText")}
            </div>
            {data?.telemetry_instance_id ? (
              <div className="space-y-2">
                <p className="text-xs text-neutral-400">
                  {t("settings.telemetry.instanceId")}
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
                    {showFullId ? t("settings.telemetry.hide") : t("settings.telemetry.showFull")}
                  </button>
                </p>
              </div>
            ) : null}
          </CardContent>
        </Card>
        ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}
