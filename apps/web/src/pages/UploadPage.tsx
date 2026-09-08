import { Link } from "react-router-dom";
import { useUpload } from "../hooks/useUpload";
import { DropZone } from "../components/upload/DropZone";
import { UploadFileList } from "../components/upload/UploadFileList";
import { Button } from "../components/ui/button";

export default function UploadPage() {
  const { items, addFiles, clearFinished } = useUpload();
  const doneCount = items.filter((item) => item.status === "success").length;
  const uploading = items.some((item) => item.status === "uploading" || item.status === "queued");

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-3xl space-y-6 p-8">
        <header>
          <h1 className="text-lg font-semibold text-neutral-900">Upload</h1>
          <p className="mt-1 text-sm text-neutral-500">
            Videos and images trigger automatic AI analysis. Facebook Excel exports are parsed
            into performance data.
          </p>
          <p className="mt-1.5 rounded-md bg-amber-50 px-2.5 py-1.5 text-xs text-amber-800">
            投放数据的评判基准统一以 <span className="font-medium">Facebook 报表</span>为准
            （中文看板可解析入库，但不作为今日建议的判定依据）。
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
              View {doneCount} uploaded file{doneCount === 1 ? "" : "s"} in Assets →
            </Link>
          ) : null}
          {items.length > 0 && !uploading ? (
            <Button size="sm" variant="ghost" onClick={clearFinished}>
              Clear list
            </Button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
