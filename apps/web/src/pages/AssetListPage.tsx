import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { FileImage, FileSpreadsheet, FileVideo, Search } from "lucide-react";
import type { FileType } from "@shared";
import { useAssets } from "../hooks/useAssets";
import { StatusBadge } from "../components/assets/StatusBadge";
import { Badge } from "../components/ui/badge";
import { Input } from "../components/ui/input";
import { Skeleton } from "../components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../components/ui/table";

function TypeIcon({ type }: { type: FileType }) {
  if (type === "video") return <FileVideo className="h-4 w-4 text-neutral-400" />;
  if (type === "image") return <FileImage className="h-4 w-4 text-neutral-400" />;
  return <FileSpreadsheet className="h-4 w-4 text-neutral-400" />;
}

function formatTime(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

export default function AssetListPage() {
  const navigate = useNavigate();
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  // 生命周期过滤（全部/活跃/观察/已归档）；未归族素材视为活跃
  const [lifecycleFilter, setLifecycleFilter] = useState<string>("all");

  // Debounce the search term sent to GET /assets?search=
  useEffect(() => {
    const timer = setTimeout(() => setSearch(searchInput.trim()), 300);
    return () => clearTimeout(timer);
  }, [searchInput]);

  const { data, isLoading, isError, error, isFetching } = useAssets(search);
  const filtered = data?.filter((asset) =>
    lifecycleFilter === "all"
      ? true
      : lifecycleFilter === "active"
        ? (asset.lifecycle_state ?? "active") === "active"
        : asset.lifecycle_state === lifecycleFilter,
  );

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl space-y-6 p-8">
        <header className="flex items-end justify-between gap-4">
          <div>
            <h1 className="text-lg font-semibold text-neutral-900">Assets</h1>
            <p className="mt-1 text-sm text-neutral-500">
              Uploaded creatives and their AI analysis status.
            </p>
          </div>
          <div className="relative w-64">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-neutral-400" />
            <Input
              className="pl-8"
              placeholder="Search by filename…"
              value={searchInput}
              onChange={(event) => setSearchInput(event.target.value)}
            />
          </div>
        </header>

        <div className="flex items-center gap-1.5">
          {(
            [
              ["all", "全部"],
              ["active", "活跃"],
              ["watch", "观察"],
              ["archived", "已归档"],
            ] as const
          ).map(([key, label]) => (
            <button
              key={key}
              type="button"
              onClick={() => setLifecycleFilter(key)}
              className={
                lifecycleFilter === key
                  ? "rounded-full bg-neutral-900 px-3 py-1 text-xs font-medium text-white"
                  : "rounded-full bg-neutral-100 px-3 py-1 text-xs text-neutral-500 transition hover:bg-neutral-200"
              }
            >
              {label}
            </button>
          ))}
        </div>

        {isError ? (
          <p className="text-sm text-red-600">
            {error instanceof Error ? error.message : "Failed to load assets"}
          </p>
        ) : null}

        <div className="rounded-lg border border-[#e5e5e5] bg-white">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-[72px]">Preview</TableHead>
                <TableHead>Filename</TableHead>
                <TableHead className="w-[180px]">Uploaded</TableHead>
                <TableHead className="w-[130px]">AI Status</TableHead>
                <TableHead>Creative</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {isLoading
                ? Array.from({ length: 5 }).map((_, index) => (
                    <TableRow key={index}>
                      <TableCell>
                        <Skeleton className="h-9 w-16" />
                      </TableCell>
                      <TableCell>
                        <Skeleton className="h-4 w-48" />
                      </TableCell>
                      <TableCell>
                        <Skeleton className="h-4 w-28" />
                      </TableCell>
                      <TableCell>
                        <Skeleton className="h-5 w-20" />
                      </TableCell>
                      <TableCell>
                        <Skeleton className="h-4 w-32" />
                      </TableCell>
                    </TableRow>
                  ))
                : null}

              {filtered?.map((asset) => (
                <TableRow
                  key={asset.id}
                  className="cursor-pointer"
                  onClick={() => navigate(`/assets/${asset.id}`)}
                >
                  <TableCell>
                    {asset.thumbnail_url ? (
                      <img
                        src={asset.thumbnail_url}
                        alt={asset.filename}
                        className="h-9 w-16 rounded border border-[#e5e5e5] object-cover"
                      />
                    ) : (
                      <div className="flex h-9 w-16 items-center justify-center rounded border border-[#e5e5e5] bg-neutral-50">
                        <TypeIcon type={asset.file_type} />
                      </div>
                    )}
                  </TableCell>
                  <TableCell>
                    <span className="block max-w-[320px] truncate font-medium">
                      {asset.filename}
                    </span>
                  </TableCell>
                  <TableCell className="text-neutral-500">{formatTime(asset.created_at)}</TableCell>
                  <TableCell>
                    <span className="inline-flex items-center gap-1.5">
                      <StatusBadge status={asset.analysis_status} />
                      {asset.confidence !== null && asset.confidence < 0.7 ? (
                        <Badge
                          variant="warning"
                          title={`AI 置信度 ${(asset.confidence * 100).toFixed(0)}%，低于 70% — 建议人工复核`}
                        >
                          待复核
                        </Badge>
                      ) : null}
                    </span>
                  </TableCell>
                  <TableCell className="text-neutral-700">
                    {asset.creative_name ?? <span className="text-neutral-400">—</span>}
                  </TableCell>
                </TableRow>
              ))}

              {!isLoading && filtered && filtered.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={5} className="py-10 text-center text-sm text-neutral-400">
                    {search ? "No assets match your search." : "No assets yet — upload some first."}
                  </TableCell>
                </TableRow>
              ) : null}
            </TableBody>
          </Table>
        </div>

        {isFetching && !isLoading ? (
          <p className="text-xs text-neutral-400">Refreshing…</p>
        ) : null}
      </div>
    </div>
  );
}
