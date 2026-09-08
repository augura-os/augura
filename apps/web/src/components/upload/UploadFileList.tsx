import { CheckCircle2, FileIcon, Loader2, XCircle } from "lucide-react";
import type { UploadItem } from "../../hooks/useUpload";
import { Badge } from "../ui/badge";

const STATUS_BADGE: Record<UploadItem["status"], { label: string; variant: "secondary" | "warning" | "success" | "destructive" }> = {
  queued: { label: "Queued", variant: "secondary" },
  uploading: { label: "Uploading", variant: "warning" },
  success: { label: "Done", variant: "success" },
  skipped: { label: "Skipped", variant: "secondary" },
  error: { label: "Failed", variant: "destructive" },
};

function formatSize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function UploadFileList({ items }: { items: UploadItem[] }) {
  if (items.length === 0) return null;
  return (
    <ul className="divide-y divide-[#e5e5e5] rounded-lg border border-[#e5e5e5] bg-white">
      {items.map((item) => {
        const meta = STATUS_BADGE[item.status];
        return (
          <li key={item.id} className="flex items-center gap-3 px-4 py-3">
            {item.status === "uploading" ? (
              <Loader2 className="h-4 w-4 shrink-0 animate-spin text-neutral-400" />
            ) : item.status === "success" ? (
              <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-500" />
            ) : item.status === "skipped" ? (
              <CheckCircle2 className="h-4 w-4 shrink-0 text-neutral-300" />
            ) : item.status === "error" ? (
              <XCircle className="h-4 w-4 shrink-0 text-red-500" />
            ) : (
              <FileIcon className="h-4 w-4 shrink-0 text-neutral-400" />
            )}
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm text-neutral-900">{item.file.name}</p>
              <p className="text-xs text-neutral-500">
                {formatSize(item.file.size)}
                {item.message ? ` · ${item.message}` : ""}
              </p>
              {item.status === "uploading" ? (
                <div className="mt-1.5 h-1 w-full overflow-hidden rounded-full bg-neutral-100">
                  <div
                    className="h-full rounded-full bg-neutral-900 transition-all"
                    style={{ width: `${item.progress}%` }}
                  />
                </div>
              ) : null}
            </div>
            <Badge variant={meta.variant}>{meta.label}</Badge>
          </li>
        );
      })}
    </ul>
  );
}
