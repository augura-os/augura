import { useCallback, useRef, useState } from "react";
import { uploadFile } from "../services/api";
import { translate } from "../lib/i18n";

export type UploadStatus = "queued" | "uploading" | "success" | "skipped" | "error";

export interface UploadItem {
  id: string;
  file: File;
  status: UploadStatus;
  progress: number;
  message: string;
}

const ACCEPTED_EXTENSIONS = ["mp4", "mov", "qt", "png", "jpg", "jpeg", "xlsx", "xls"];

// Mirrors the backend default (Settings.upload_max_bytes, 1 GiB).
export const UPLOAD_MAX_BYTES = 1024 * 1024 * 1024;

export function isAcceptedFile(file: File): boolean {
  const ext = file.name.split(".").pop()?.toLowerCase() ?? "";
  return ACCEPTED_EXTENSIONS.includes(ext);
}

function initialState(file: File): Pick<UploadItem, "status" | "message"> {
  if (!isAcceptedFile(file)) {
    return { status: "error", message: "Unsupported file type" };
  }
  if (file.size > UPLOAD_MAX_BYTES) {
    return { status: "error", message: translate("upload.tooLarge").replace("{n}", "1 GB") };
  }
  return { status: "queued", message: "" };
}

function patchItem(
  setItems: React.Dispatch<React.SetStateAction<UploadItem[]>>,
  id: string,
  patch: Partial<UploadItem>,
) {
  setItems((prev) => prev.map((item) => (item.id === id ? { ...item, ...patch } : item)));
}

async function uploadOne(
  setItems: React.Dispatch<React.SetStateAction<UploadItem[]>>,
  id: string,
  file: File,
) {
  patchItem(setItems, id, { status: "uploading", progress: 0 });
  try {
    const result = await uploadFile(file, (percent) => {
      patchItem(setItems, id, { progress: percent });
    });
    if (result.uploaded.length === 0 && result.skipped.length > 0) {
      patchItem(setItems, id, {
        status: "skipped",
        progress: 100,
        message: result.skipped[0].reason,
      });
      return;
    }
    const skippedNote = result.skipped.length > 0 ? `, ${result.skipped.length} skipped` : "";
    const warningNote = result.warnings.length > 0 ? ` · ⚠️ ${result.warnings[0]}` : "";
    patchItem(setItems, id, {
      status: "success",
      progress: 100,
      message: `Created ${result.uploaded.length} asset${result.uploaded.length === 1 ? "" : "s"}${skippedNote}${warningNote}`,
    });
  } catch (error) {
    patchItem(setItems, id, {
      status: "error",
      message: error instanceof Error ? error.message : "Upload failed",
    });
  }
}

/**
 * Upload manager: accepts files, validates extensions, uploads sequentially
 * (one POST /upload request per file so each row gets its own status/progress).
 */
export function useUpload() {
  const [items, setItems] = useState<UploadItem[]>([]);
  // Chain uploads through a promise ref so large videos upload one at a time.
  const queueRef = useRef<Promise<void>>(Promise.resolve());

  const addFiles = useCallback((files: File[]) => {
    const next: UploadItem[] = files.map((file) => ({
      id: crypto.randomUUID(),
      file,
      progress: 0,
      ...initialState(file),
    }));
    setItems((prev) => [...prev, ...next]);
    for (const item of next) {
      if (item.status === "queued") {
        queueRef.current = queueRef.current.then(() => uploadOne(setItems, item.id, item.file));
      }
    }
  }, []);

  const clearFinished = useCallback(() => {
    setItems((prev) => prev.filter((item) => item.status === "queued" || item.status === "uploading"));
  }, []);

  return { items, addFiles, clearFinished };
}
