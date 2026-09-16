import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { RefreshCw } from "lucide-react";
import type { UpdateAssetRequest } from "@shared";
import { useAssetDetail, useRunAnalysis, useUpdateAsset } from "../hooks/useAssetDetail";
import { StatusBadge } from "../components/assets/StatusBadge";
import { PerformanceSummary, PerformanceTable } from "../components/assets/PerformanceTable";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { Skeleton } from "../components/ui/skeleton";
import { Textarea } from "../components/ui/textarea";
import { useT } from "../lib/i18n";

interface FormState {
  hook: string;
  conflict: string;
  gameplay: string;
  reward: string;
  summary: string;
  characters: string;
  environment: string;
  variant_factors: string;
  tags: string;
  creative_name: string;
}

/** Split a comma / newline separated text field back into a string array. */
function parseList(value: string): string[] {
  return value
    .split(/[,，、\n]/)
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
}

export default function AssetDetailPage() {
  const t = useT();
  const { id } = useParams<{ id: string }>();
  const { data, isLoading, isError, error } = useAssetDetail(id);
  const updateMutation = useUpdateAsset(id ?? "");
  const analysisMutation = useRunAnalysis(id ?? "");

  const [form, setForm] = useState<FormState | null>(null);
  const [feedback, setFeedback] = useState<string>("");

  // Sync the editable form whenever fresh detail data arrives.
  useEffect(() => {
    if (!data) return;
    const analysis = data.analysis;
    setForm({
      hook: analysis?.hook ?? "",
      conflict: analysis?.conflict ?? "",
      gameplay: analysis?.gameplay ?? "",
      reward: analysis?.reward ?? "",
      summary: analysis?.summary ?? "",
      characters: (analysis?.characters ?? []).join(", "),
      environment: (analysis?.environment ?? []).join(", "),
      variant_factors: (analysis?.variant_factors ?? []).join(", "),
      tags: (analysis?.tags ?? []).join(", "),
      creative_name: analysis?.creative_name ?? data.creative?.name ?? "",
    });
  }, [data]);

  // Auto-clear the "Saved" feedback.
  useEffect(() => {
    if (!feedback) return;
    const timer = setTimeout(() => setFeedback(""), 3000);
    return () => clearTimeout(timer);
  }, [feedback]);

  if (!id) return null;

  if (isLoading) {
    return (
      <div className="grid h-full grid-cols-1 gap-6 overflow-y-auto p-8 lg:grid-cols-2">
        <Skeleton className="h-[360px] w-full" />
        <div className="space-y-4">
          <Skeleton className="h-8 w-1/2" />
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      </div>
    );
  }

  if (isError || !data) {
    return (
      <div className="flex h-full items-center justify-center">
        <p className="text-sm text-red-600">
          {error instanceof Error ? error.message : t("assets.detail.loadFailed")}
        </p>
      </div>
    );
  }

  const { asset, performance } = data;
  const set = (key: keyof FormState) => (value: string) =>
    setForm((prev) => (prev ? { ...prev, [key]: value } : prev));

  const onSave = () => {
    if (!form) return;
    const payload: UpdateAssetRequest = {
      hook: form.hook,
      conflict: form.conflict,
      gameplay: form.gameplay,
      reward: form.reward,
      summary: form.summary,
      characters: parseList(form.characters),
      environment: parseList(form.environment),
      // emotion/confidence are part of the schema but have no form field (§9) —
      // pass them through unchanged so a save never clobbers them.
      emotion: data.analysis?.emotion ?? [],
      confidence: data.analysis?.confidence ?? 0,
      variant_factors: parseList(form.variant_factors),
      tags: parseList(form.tags),
      creative_name: form.creative_name,
    };
    updateMutation.mutate(payload, {
      onSuccess: () => setFeedback(t("common.saved")),
      onError: (mutationError) =>
        setFeedback(
          mutationError instanceof Error ? mutationError.message : t("common.saveFailed"),
        ),
    });
  };

  return (
    <div className="h-full overflow-y-auto">
      <div className="grid grid-cols-1 gap-8 p-8 lg:grid-cols-2">
        {/* Left: media preview */}
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h1 className="max-w-[70%] truncate text-lg font-semibold text-neutral-900">
              {asset.filename}
            </h1>
            <StatusBadge status={asset.analysis_status} />
          </div>

          <div className="overflow-hidden rounded-lg border border-[#e5e5e5] bg-neutral-50">
            {asset.file_type === "video" && asset.media_url ? (
              <video controls className="w-full" src={asset.media_url} />
            ) : asset.file_type === "image" && (asset.media_url ?? asset.thumbnail_url) ? (
              <img
                className="w-full object-contain"
                src={asset.media_url ?? asset.thumbnail_url ?? ""}
                alt={asset.filename}
              />
            ) : asset.file_type === "excel" ? (
              <div className="flex h-48 items-center justify-center p-6 text-center text-sm text-neutral-500">
                {t("assets.detail.excelNote")}
              </div>
            ) : (
              <div className="flex h-48 items-center justify-center text-sm text-neutral-400">
                {t("assets.detail.noPreview")}
              </div>
            )}
          </div>

          {data.analysis ? (
            <p className="text-xs text-neutral-400">
              {t("assets.detail.confidence").replace(
                "{pct}",
                (data.analysis.confidence * 100).toFixed(0),
              )}
              {data.engine_version
                ? t("assets.detail.engine").replace("{version}", data.engine_version)
                : ""}
            </p>
          ) : null}

          {performance.length > 0 ? (
            <div>
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-neutral-500">
                {t("assets.detail.performance")}
              </h3>
              <div className="mb-3">
                <PerformanceSummary rows={performance} />
              </div>
              <div className="overflow-x-auto rounded-lg border border-[#e5e5e5] px-3 py-1">
                <PerformanceTable rows={performance} showName={asset.file_type === "excel"} />
              </div>
            </div>
          ) : null}
        </div>

        {/* Right: editable analysis form */}
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-neutral-900">{t("assets.detail.aiAnalysis")}</h2>
            <Button
              size="sm"
              variant="outline"
              disabled={analysisMutation.isPending || asset.file_type === "excel"}
              onClick={() => analysisMutation.mutate()}
            >
              <RefreshCw
                className={analysisMutation.isPending ? "h-3.5 w-3.5 animate-spin" : "h-3.5 w-3.5"}
              />
              {analysisMutation.isPending ? t("assets.detail.analyzing") : t("assets.detail.rerun")}
            </Button>
          </div>

          {analysisMutation.isError ? (
            <p className="text-xs text-red-600">
              {analysisMutation.error instanceof Error
                ? analysisMutation.error.message
                : t("assets.detail.analysisFailed")}
            </p>
          ) : null}
          {analysisMutation.isSuccess ? (
            <p className="text-xs text-emerald-600">{t("assets.detail.analysisDone")}</p>
          ) : null}

          {form ? (
            <div className="space-y-3">
              <div>
                <Label htmlFor="creative_name">{t("assets.detail.creativeName")}</Label>
                <Input
                  id="creative_name"
                  value={form.creative_name}
                  onChange={(event) => set("creative_name")(event.target.value)}
                />
              </div>
              <div>
                <Label htmlFor="hook">{t("analysis.hook")}</Label>
                <Textarea
                  id="hook"
                  value={form.hook}
                  onChange={(event) => set("hook")(event.target.value)}
                />
              </div>
              <div>
                <Label htmlFor="conflict">{t("analysis.conflict")}</Label>
                <Textarea
                  id="conflict"
                  value={form.conflict}
                  onChange={(event) => set("conflict")(event.target.value)}
                />
              </div>
              <div>
                <Label htmlFor="gameplay">{t("analysis.gameplay")}</Label>
                <Textarea
                  id="gameplay"
                  value={form.gameplay}
                  onChange={(event) => set("gameplay")(event.target.value)}
                />
              </div>
              <div>
                <Label htmlFor="reward">{t("analysis.reward")}</Label>
                <Textarea
                  id="reward"
                  value={form.reward}
                  onChange={(event) => set("reward")(event.target.value)}
                />
              </div>
              <div>
                <Label htmlFor="characters">
                  {t("analysis.characters")} {t("common.commaSeparated")}
                </Label>
                <Input
                  id="characters"
                  value={form.characters}
                  onChange={(event) => set("characters")(event.target.value)}
                />
              </div>
              <div>
                <Label htmlFor="environment">
                  {t("analysis.environment")} {t("common.commaSeparated")}
                </Label>
                <Input
                  id="environment"
                  value={form.environment}
                  onChange={(event) => set("environment")(event.target.value)}
                />
              </div>
              <div>
                <Label htmlFor="variant_factors">
                  {t("graph.node.variant")} factors {t("common.commaSeparated")}
                </Label>
                <Input
                  id="variant_factors"
                  value={form.variant_factors}
                  onChange={(event) => set("variant_factors")(event.target.value)}
                />
              </div>
              <div>
                <Label htmlFor="tags">
                  {t("graph.node.tags")} {t("common.commaSeparated")}
                </Label>
                <Input
                  id="tags"
                  value={form.tags}
                  onChange={(event) => set("tags")(event.target.value)}
                />
              </div>
              <div>
                <Label htmlFor="summary">{t("analysis.summary")}</Label>
                <Textarea
                  id="summary"
                  className="min-h-[120px]"
                  value={form.summary}
                  onChange={(event) => set("summary")(event.target.value)}
                />
              </div>

              <div className="flex items-center gap-3 pt-1">
                <Button onClick={onSave} disabled={updateMutation.isPending}>
                  {updateMutation.isPending ? t("common.saving") : t("assets.detail.save")}
                </Button>
                {feedback ? (
                  <span
                    className={
                      feedback === t("common.saved")
                        ? "text-sm text-emerald-600"
                        : "text-sm text-red-600"
                    }
                  >
                    {feedback}
                  </span>
                ) : null}
              </div>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
