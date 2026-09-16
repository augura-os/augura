import { Link } from "react-router-dom";
import { useUpload } from "../hooks/useUpload";
import { DropZone } from "../components/upload/DropZone";
import { UploadFileList } from "../components/upload/UploadFileList";
import { Button } from "../components/ui/button";
import { useT } from "../lib/i18n";

export default function UploadPage() {
  const t = useT();
  const { items, addFiles, clearFinished } = useUpload();
  const doneCount = items.filter((item) => item.status === "success").length;
  const uploading = items.some((item) => item.status === "uploading" || item.status === "queued");

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-3xl space-y-6 p-8">
        <header>
          <h1 className="text-lg font-semibold text-neutral-900">{t("upload.title")}</h1>
          <p className="mt-1 text-sm text-neutral-500">
            {t("upload.subtitle")}
          </p>
          <p className="mt-1.5 rounded-md bg-amber-50 px-2.5 py-1.5 text-xs text-amber-800">
            {t("upload.fbNoteA")}
            <span className="font-medium">{t("upload.fbName")}</span>
            {t("upload.fbNoteB")}
          </p>
        </header>

        <DropZone onFiles={addFiles} />

        <UploadFileList items={items} />

        <div className="flex items-center gap-3">
          {doneCount > 0 ? (
            <Link
              to="/assets"
              className="text-sm font-medium text-neutral-900 underline underline-offset-2 hover:text-neutral-600"
            >
              {t("upload.viewUploaded")
                .replace("{n}", String(doneCount))
                .replace("{s}", doneCount === 1 ? "" : "s")}
            </Link>
          ) : null}
          {items.length > 0 && !uploading ? (
            <Button size="sm" variant="ghost" onClick={clearFinished}>
              {t("upload.clearList")}
            </Button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
