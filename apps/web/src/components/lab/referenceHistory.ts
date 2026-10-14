import type { AnalysisStatus, LabReferenceItem } from "@shared";

/** 历史列表行的动作分支：
 *  已有当前版本报告 → 直接打开报告步；观察层就绪但无报告 → 继续分析流程（受众步起）；
 *  其余（排队/分析中/失败/未分析）→ 只显示状态，不给跳转。 */
export type ReferenceRowAction = "view-report" | "continue" | "none";

export function referenceRowAction(
  item: Pick<LabReferenceItem, "analysis_status" | "diagnosis_current_langs">,
): ReferenceRowAction {
  if (item.diagnosis_current_langs.length > 0) return "view-report";
  if (item.analysis_status === "completed") return "continue";
  return "none";
}

/** 行内分析状态文案的 i18n key；未知状态（后端新增枚举时）按 none 兜底，与 StatusBadge 同口径。 */
const STATUS_TEXT_KEY: Record<AnalysisStatus, string> = {
  pending: "lab.reference.history.status.pending",
  processing: "lab.reference.history.status.processing",
  completed: "lab.reference.history.status.completed",
  failed: "lab.reference.history.status.failed",
  none: "lab.reference.history.status.none",
};

export function referenceStatusKey(status: AnalysisStatus): string {
  const safe: AnalysisStatus = status in STATUS_TEXT_KEY ? status : "none";
  return STATUS_TEXT_KEY[safe];
}

/** 上传日期短格式（无效日期原样返回，与 AssetListPage formatTime 同口径）。 */
export function formatReferenceDate(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleDateString();
}
