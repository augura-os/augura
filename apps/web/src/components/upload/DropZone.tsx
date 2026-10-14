import { useRef, useState } from "react";
import { UploadCloud } from "lucide-react";
import { useT } from "../../lib/i18n";
import { cn } from "../../lib/utils";

const ACCEPT_ATTR = ".mp4,.mov,.qt,.png,.jpg,.jpeg,.xlsx,.xls";

interface DropZoneProps {
  onFiles: (files: File[]) => void;
  /** 文件类型过滤（默认视频/图片/Excel 全量；Creative Lab 只收视频/图片） */
  accept?: string;
  /** 主/副文案（默认走 upload.dropTitle/dropHint；调用方可传自定义 i18n 文本） */
  title?: string;
  subtitle?: string;
}

/** Drag-and-drop zone for video / image / Facebook Excel uploads. */
export function DropZone({ onFiles, accept = ACCEPT_ATTR, title, subtitle }: DropZoneProps) {
  const t = useT();
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const resolvedTitle = title ?? t("upload.dropTitle");
  const resolvedSubtitle = subtitle ?? t("upload.dropHint");

  const handleDrop = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    const files = Array.from(event.dataTransfer.files);
    if (files.length > 0) onFiles(files);
  };

  return (
    <div
      role="button"
      tabIndex={0}
      aria-label={resolvedTitle}
      className={cn(
        "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-[#e5e5e5] bg-white px-6 py-14 text-center transition-colors",
        dragging ? "border-neutral-400 bg-neutral-50" : "hover:bg-neutral-50",
      )}
      onClick={() => inputRef.current?.click()}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") inputRef.current?.click();
      }}
      onDragOver={(event) => {
        event.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={handleDrop}
    >
      <UploadCloud className="h-8 w-8 text-neutral-400" />
      <p className="text-sm font-medium text-neutral-900">{resolvedTitle}</p>
      <p className="text-xs text-neutral-500">{resolvedSubtitle}</p>
      <input
        ref={inputRef}
        type="file"
        multiple
        accept={accept}
        className="hidden"
        onChange={(event) => {
          const files = event.target.files ? Array.from(event.target.files) : [];
          if (files.length > 0) onFiles(files);
          event.target.value = "";
        }}
      />
    </div>
  );
}
