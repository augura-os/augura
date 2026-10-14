import { useState } from "react";
import type { AssetListItem, UploadResponse } from "@shared";
import { DropZone } from "../upload/DropZone";
import { uploadLabReference } from "../../services/api";
import { useLabReferences } from "../../hooks/useLab";
import { skippedReasonText, UPLOAD_MAX_BYTES, warningText } from "../../hooks/useUpload";
import { apiErrorText } from "../../lib/apiErrorText";
import { useT } from "../../lib/i18n";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { formatReferenceDate, referenceRowAction, referenceStatusKey } from "./referenceHistory";
import type { LabSession } from "./labSession";

const LAB_ACCEPT_ATTR = ".mp4,.mov,.qt,.png,.jpg,.jpeg";
const LAB_ACCEPTED_EXTENSIONS = ["mp4", "mov", "qt", "png", "jpg", "jpeg"];

interface ReferenceStepProps {
  current: LabSession | null;
  onReady: (asset: AssetListItem) => void;
  /** 历史列表「查看报告」：替换会话并跳过受众步直开报告步 */
  onOpenReport: (asset: AssetListItem) => void;
}

/** 参考步：上传竞品参考（POST /lab/references，自动打 competitor-reference 标签入库）。
 *  M1 单参考——多选文件时只取第一个并明示；上传成功即替换当前会话、进入受众步。
 *  下方「已分析参考」历史列表（GET /lab/references）：有当前版本报告的直开报告步，
 *  观察层就绪但无报告的走受众步继续分析，其余状态只显示不给跳转。 */
export function ReferenceStep({ current, onReady, onOpenReport }: ReferenceStepProps) {
  const t = useT();
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState("");
  const [result, setResult] = useState<UploadResponse | null>(null);
  const [multiNote, setMultiNote] = useState(false);
  const { data: referenceList, isLoading: historyLoading } = useLabReferences();
  const historyItems = referenceList?.items ?? [];

  const handleFiles = (files: File[]) => {
    const file = files[0];
    if (!file || uploading) return;
    setMultiNote(files.length > 1);
    setError("");
    setResult(null);
    const ext = file.name.split(".").pop()?.toLowerCase() ?? "";
    if (!LAB_ACCEPTED_EXTENSIONS.includes(ext)) {
      setError(t("lab.reference.unsupportedType"));
      return;
    }
    if (file.size > UPLOAD_MAX_BYTES) {
      setError(t("upload.tooLarge").replace("{n}", "1 GB"));
      return;
    }
    setUploading(true);
    setProgress(0);
    uploadLabReference(file, setProgress)
      .then((outcome) => {
        setResult(outcome);
        const first = outcome.uploaded[0];
        if (first) onReady(first);
      })
      .catch((uploadError) => setError(apiErrorText(uploadError, t) || t("lab.reference.failed")))
      .finally(() => setUploading(false));
  };

  return (
    <div className="space-y-4">
      <p className="rounded-md bg-amber-50 px-2.5 py-1.5 text-xs text-amber-800">
        {t("lab.reference.tagNote")}
      </p>

      {current ? (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-[#e5e5e5] bg-white px-3 py-2">
          <p className="text-sm text-neutral-600">
            {t("lab.reference.current")}
            <span className="ml-1 font-medium text-neutral-900">{current.filename}</span>
          </p>
          <span className="text-xs text-neutral-400">{t("lab.reference.replaceHint")}</span>
        </div>
      ) : null}

      <DropZone
        onFiles={handleFiles}
        accept={LAB_ACCEPT_ATTR}
        title={t("lab.reference.dropTitle")}
        subtitle={t("lab.reference.dropHint")}
      />
      <p className="text-xs text-neutral-400">{t("lab.reference.singleNote")}</p>
      {multiNote ? <p className="text-xs text-amber-700">{t("lab.reference.multiNote")}</p> : null}

      {uploading ? (
        <div className="space-y-1.5">
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-neutral-100">
            <div
              className="h-full bg-neutral-900 transition-all"
              style={{ width: `${progress}%` }}
            />
          </div>
          <p className="text-xs text-neutral-500">
            {t("lab.reference.uploading").replace("{pct}", String(progress))}
          </p>
        </div>
      ) : null}

      {error ? <p className="text-sm text-red-600">{error}</p> : null}

      {result ? (
        <div className="space-y-1 text-sm">
          {result.uploaded.map((asset) => (
            <p key={asset.id} className="text-emerald-700">
              {t("lab.reference.uploaded").replace("{name}", asset.filename)}
            </p>
          ))}
          {result.skipped.map((skipped) => (
            <p key={skipped.filename} className="text-neutral-600">
              {skipped.filename}：{skippedReasonText(skipped)}
            </p>
          ))}
          {result.warnings.map((warning, index) => (
            <p key={index} className="text-amber-700">
              ⚠️ {warningText(warning)}
            </p>
          ))}
        </div>
      ) : null}

      {historyLoading ? (
        <p className="text-xs text-neutral-400">{t("lab.reference.history.loading")}</p>
      ) : historyItems.length > 0 ? (
        <section className="space-y-2">
          <h3 className="text-sm font-medium text-neutral-900">
            {t("lab.reference.history.title")}
          </h3>
          <ul className="divide-y divide-[#e5e5e5] rounded-md border border-[#e5e5e5] bg-white">
            {historyItems.map((item) => {
              const action = referenceRowAction(item);
              return (
                <li key={item.id} className="flex items-center gap-3 px-3 py-2.5">
                  <div className="min-w-0 flex-1">
                    <p className="flex items-center gap-1.5 text-sm">
                      <span className="truncate font-medium text-neutral-900">
                        {item.creative_name || item.filename}
                      </span>
                      {current?.assetId === item.id ? (
                        <Badge variant="default" className="shrink-0">
                          {t("lab.reference.history.current")}
                        </Badge>
                      ) : null}
                    </p>
                    <p className="mt-0.5 text-xs text-neutral-400">
                      {formatReferenceDate(item.created_at)} ·{" "}
                      {t(referenceStatusKey(item.analysis_status))}
                    </p>
                  </div>
                  {action === "view-report" ? (
                    <Button size="sm" onClick={() => onOpenReport(item)}>
                      {t("lab.reference.history.viewReport")}
                    </Button>
                  ) : action === "continue" ? (
                    <Button size="sm" variant="outline" onClick={() => onReady(item)}>
                      {t("lab.reference.history.continue")}
                    </Button>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
