import type { AnalysisStatus } from "@shared";
import { Badge } from "../ui/badge";

const STATUS_META: Record<AnalysisStatus, { label: string; variant: "secondary" | "warning" | "success" | "destructive" | "outline" }> = {
  pending: { label: "Pending", variant: "warning" },
  processing: { label: "Processing", variant: "warning" },
  completed: { label: "Completed", variant: "success" },
  failed: { label: "Failed", variant: "destructive" },
  none: { label: "None", variant: "secondary" },
};

export function StatusBadge({ status }: { status: AnalysisStatus }) {
  const meta = STATUS_META[status] ?? STATUS_META.none;
  return <Badge variant={meta.variant}>{meta.label}</Badge>;
}
