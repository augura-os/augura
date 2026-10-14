import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ChevronDown, ChevronUp, Loader2 } from "lucide-react";
import type { LabObservation } from "@shared";
import { useCreateDiagnosis, useLabAnalysis } from "../../hooks/useLab";
import { apiErrorCode } from "../../services/client";
import { apiErrorText } from "../../lib/apiErrorText";
import { getLang, useT } from "../../lib/i18n";
import { Button } from "../ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../ui/card";
import { Skeleton } from "../ui/skeleton";
import { DiagnosisReport } from "./DiagnosisReport";
import type { LabSession } from "./labSession";

interface ReportStepProps {
  session: LabSession;
}

/** 自动诊断防重集（按 assetId:lang）：StrictMode dev 双发 effect、切步来回导致
 *  remount 时 mutation 状态会重置，模块级 key 保证同一 asset 同一语言无重复在途诊断；
 *  key 含 lang 才能允许「zh 生成中切 en 触发 en」。 */
const inflightDiagnoses = new Set<string>();

/** 报告步：轮询观察层 → 触发/重跑定性诊断 → 渲染六要素报告。
 *  证据诚实区护栏：graph_evidence 为 null 只给降级文案（不出数字）；
 *  reference_only 顶部横幅；product_facts 为空给 Settings 引导卡。 */
export function ReportStep({ session }: ReportStepProps) {
  const t = useT();
  // lang 进 queryKey：语言切换 → useT 重渲染 → queryKey 变 → 自动按新语言拉取
  const { data, isLoading, isError, error } = useLabAnalysis(session.assetId, getLang());
  const diagnosisMutation = useCreateDiagnosis();

  /** 触发诊断（自动/手动共用同一 mutate，body 一致）。仅失败时放行重试——
   *  成功则保留 key 直到 invalidate 后的 refetch 带回诊断，否则缓存陈旧
   *  窗口内（含 remount）会重复发起一次昂贵的 LLM 调用。 */
  const runDiagnosis = () => {
    const key = `${session.assetId}:${getLang()}`;
    if (inflightDiagnoses.has(key)) return;
    inflightDiagnoses.add(key);
    diagnosisMutation.mutate(
      { asset_id: session.assetId, audience: session.audience },
      {
        onSettled: (_diagnosis, mutationError) => {
          if (mutationError) inflightDiagnoses.delete(key);
        },
      },
    );
  };

  // 观察层就绪且无诊断时自动生成一次报告（stale 属于已有诊断，不自动触发，
  // 保持手动「重新诊断」）；失败不自动重试，由错误文案 + 手动按钮兜底。
  const shouldAutoDiagnose =
    data?.analysis_status === "completed" &&
    !data.diagnosis &&
    !data.diagnosis_stale &&
    !diagnosisMutation.isPending &&
    !diagnosisMutation.isError;

  useEffect(() => {
    if (!shouldAutoDiagnose) return;
    runDiagnosis();
  }, [shouldAutoDiagnose, runDiagnosis]);

  if (isLoading) {
    return <Skeleton className="h-48 w-full" />;
  }
  if (isError || !data) {
    return (
      <Card>
        <CardContent className="p-6">
          <p className="text-sm text-red-600">
            {apiErrorText(error, t) || t("lab.report.loadFailed")}
          </p>
        </CardContent>
      </Card>
    );
  }

  // 观察层未就绪：显示真实阶段文案（无假进度条），react-query 3s 轮询自动推进
  if (data.analysis_status !== "completed") {
    if (data.analysis_status === "failed") {
      return (
        <Card>
          <CardHeader>
            <CardTitle>{t("lab.report.failed")}</CardTitle>
            <CardDescription>{t("lab.report.failedHint")}</CardDescription>
          </CardHeader>
          <CardContent>
            <Link
              to={`/assets/${session.assetId}`}
              className="text-sm font-medium text-neutral-900 underline underline-offset-2 hover:text-neutral-600"
            >
              {t("lab.report.openAsset")}
            </Link>
          </CardContent>
        </Card>
      );
    }
    return (
      <Card>
        <CardContent className="flex items-center gap-3 p-6">
          <Loader2 className="h-5 w-5 animate-spin text-neutral-400" />
          <div>
            <p className="text-sm font-medium text-neutral-900">{t("lab.report.analyzing")}</p>
            <p className="mt-0.5 text-xs text-neutral-500">{t("lab.report.analyzingHint")}</p>
          </div>
        </CardContent>
      </Card>
    );
  }

  const diagnosis = data.diagnosis;
  const factsEmpty = Object.keys(data.product_facts ?? {}).length === 0;
  const diagnosisError = diagnosisMutation.isError ? diagnosisMutation.error : null;
  const keyMissing = apiErrorCode(diagnosisError) === "ai_key_not_configured";

  const diagnosisActions = (
    <div className="flex flex-wrap items-center gap-3">
      <Button onClick={runDiagnosis} disabled={diagnosisMutation.isPending}>
        {diagnosisMutation.isPending ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
        ) : null}
        {diagnosisMutation.isPending
          ? t("lab.report.generating")
          : diagnosis
            ? t("lab.report.rerun")
            : t("lab.report.generate")}
      </Button>
      {diagnosisError ? (
        <span className="text-sm text-red-600">{apiErrorText(diagnosisError, t)}</span>
      ) : null}
      {keyMissing ? (
        <Link
          to="/settings"
          className="text-sm font-medium text-neutral-900 underline underline-offset-2 hover:text-neutral-600"
        >
          {t("lab.report.goSettings")}
        </Link>
      ) : null}
    </div>
  );

  return (
    <div className="space-y-4">
      {diagnosis?.evidence_label === "reference_only" ? (
        <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          {t("lab.report.referenceOnly")}
        </div>
      ) : null}

      {data.diagnosis_stale ? (
        <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          {t("lab.report.stale")}
        </div>
      ) : null}

      {/* 证据诚实区：null 只给降级文案（不出数字），非 null 显示真实相似数 */}
      {data.graph_evidence ? (
        <div className="rounded-md border border-[#e5e5e5] bg-neutral-50 px-3 py-2 text-xs text-neutral-600">
          {t("lab.report.graphEvidence")
            .replace("{n}", String(data.graph_evidence.similar_count))
            .replace("{threshold}", String(data.graph_evidence.threshold))}
        </div>
      ) : (
        <div className="rounded-md border border-[#e5e5e5] bg-neutral-50 px-3 py-2 text-xs text-neutral-600">
          <p className="font-medium text-neutral-800">{t("lab.report.graphNoneTitle")}</p>
          <p className="mt-0.5">{t("lab.report.graphNoneDesc")}</p>
        </div>
      )}

      {factsEmpty ? (
        <div className="rounded-md border border-[#e5e5e5] bg-white px-3 py-2 text-xs text-neutral-600">
          <p className="font-medium text-neutral-800">{t("lab.report.factsEmptyTitle")}</p>
          <p className="mt-0.5">{t("lab.report.factsEmptyDesc")}</p>
          <Link
            to="/settings?section=product-facts"
            className="mt-1 inline-block font-medium text-neutral-900 underline underline-offset-2 hover:text-neutral-600"
          >
            {t("lab.report.factsEmptyLink")}
          </Link>
        </div>
      ) : null}

      {diagnosis ? (
        <>
          {data.diagnosis_stale ? diagnosisActions : null}
          <DiagnosisReport diagnosis={diagnosis} />
        </>
      ) : (
        <Card>
          <CardHeader>
            <CardTitle>{t("lab.report.readyTitle")}</CardTitle>
            <CardDescription>
              {diagnosisMutation.isPending ? t("lab.report.generating") : t("lab.report.readyDesc")}
            </CardDescription>
          </CardHeader>
          <CardContent>{diagnosisActions}</CardContent>
        </Card>
      )}

      <ObservationToggle observation={data.observation} />
    </div>
  );
}

/** 观察层（AI 分析原文）折叠卡：报告的证据底座，默认收起。 */
function ObservationToggle({ observation }: { observation: LabObservation | null }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  if (!observation) return null;
  const rows: Array<[string, string]> = [
    [t("analysis.summary"), observation.summary ?? ""],
    [t("analysis.hook"), observation.hook ?? ""],
    [t("analysis.conflict"), observation.conflict ?? ""],
    [t("analysis.gameplay"), observation.gameplay ?? ""],
    [t("analysis.reward"), observation.reward ?? ""],
  ];
  return (
    <div className="rounded-lg border border-[#e5e5e5] bg-white">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center justify-between px-4 py-2.5 text-xs font-medium text-neutral-600 hover:text-neutral-900"
      >
        {t("lab.report.observation")}
        {open ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
      </button>
      {open ? (
        <div className="space-y-2.5 border-t border-[#e5e5e5] px-4 py-3">
          {rows.map(([label, value]) =>
            value ? (
              <div key={label}>
                <p className="text-[11px] font-medium uppercase tracking-wide text-neutral-400">
                  {label}
                </p>
                <p className="mt-0.5 whitespace-pre-wrap text-xs text-neutral-700">{value}</p>
              </div>
            ) : null,
          )}
        </div>
      ) : null}
    </div>
  );
}
