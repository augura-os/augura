import { useRef, useState } from "react";
import { UploadCloud } from "lucide-react";
import { cn } from "../../lib/utils";

const ACCEPT_ATTR = ".mp4,.mov,.qt,.png,.jpg,.jpeg,.xlsx,.xls";

interface DropZoneProps {
  onFiles: (files: File[]) => void;
}

/** Drag-and-drop zone for video / image / Facebook Excel uploads. */
export function DropZone({ onFiles }: DropZoneProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

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
      aria-label="Upload files"
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
      <p className="text-sm font-medium text-neutral-900">
        Drop files here, or click to browse
      </p>
      <p className="text-xs text-neutral-500">
        Video (mp4, mov) · Image (png, jpg, jpeg) · Facebook Excel (xlsx, xls)
      </p>
      <input
        ref={inputRef}
        type="file"
        multiple
        accept={ACCEPT_ATTR}
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
