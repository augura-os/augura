import type { AnalysisStatus } from "@shared";
import { useT } from "../../lib/i18n";
import { Badge } from "../ui/badge";

const STATUS_VARIANT: Record<AnalysisStatus, "secondary" | "warning" | "success" | "destructive" | "outline"> = {
  pending: "warning",
  processing: "warning",
  completed: "success",
  failed: "destructive",
  none: "secondary",
};

export function StatusBadge({ status }: { status: AnalysisStatus }) {
  const t = useT();
  // 未知状态（后端新增枚举时）按 none 兜底
  const safe: AnalysisStatus = status in STATUS_VARIANT ? status : "none";
  return <Badge variant={STATUS_VARIANT[safe]}>{t(`assets.status.${safe}`)}</Badge>;
}
