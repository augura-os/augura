import { useMemo, useState } from "react";
import type { GraphResponse } from "@shared";
import { useMergeCreatives } from "../../hooks/useGraph";
import { useGraphStore } from "../../stores/graphStore";
import { useT } from "../../lib/i18n";
import { Button } from "../ui/button";
import { Dialog } from "../ui/dialog";

interface MergeBarProps {
  graph: GraphResponse;
}

/**
 * Floating bar shown when creative nodes are selected for merge.
 * Pick the target (surviving) creative, confirm, POST /graph/merge.
 */
export function MergeBar({ graph }: MergeBarProps) {
  const t = useT();
  const mergeSelection = useGraphStore((state) => state.mergeSelection);
  const clearMergeSelection = useGraphStore((state) => state.clearMergeSelection);
  const mergeMutation = useMergeCreatives();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [targetId, setTargetId] = useState<string>("");
  const [blockMessage, setBlockMessage] = useState<string | null>(null);
  const [forceReason, setForceReason] = useState("");

  const selectedCreatives = useMemo(
    () => graph.nodes.filter((node) => mergeSelection.includes(node.id)),
    [graph, mergeSelection],
  );

  if (selectedCreatives.length === 0) return null;

  const effectiveTargetId =
    targetId && selectedCreatives.some((node) => node.id === targetId)
      ? targetId
      : selectedCreatives[0]?.id ?? "";
  const target = selectedCreatives.find((node) => node.id === effectiveTargetId);
  const source = selectedCreatives.find((node) => node.id !== effectiveTargetId);
  const ready = selectedCreatives.length === 2 && target && source;

  const onConfirmMerge = () => {
    if (!ready || !target || !source) return;
    mergeMutation.mutate(
      {
        source_creative_id: source.ref_id,
        target_creative_id: target.ref_id,
        ...(blockMessage ? { force_reason: forceReason } : {}),
      },
      {
        onSuccess: () => {
          setConfirmOpen(false);
          setBlockMessage(null);
          setForceReason("");
          clearMergeSelection();
          setTargetId("");
        },
        onError: (error) => {
          const message = error instanceof Error ? error.message : "";
          // 后端 merge_ops 的拦截消息仍是中文（"合并被守卫拦截：…"），此处按原文匹配。
          // 后端文案翻译为英文时必须同步改成错误码，否则「强制合并」入口会静默失效。
          if (message.includes("守卫拦截")) {
            setBlockMessage(message);
          }
        },
      },
    );
  };

  return (
    <>
      <div className="pointer-events-auto flex items-center gap-3 rounded-lg border border-[#e5e5e5] bg-white px-4 py-2 shadow-sm">
        <p className="text-xs text-neutral-500">
          {t("graph.merge.selected").replace("{n}", String(selectedCreatives.length))}
        </p>
        {selectedCreatives.length === 2 ? (
          <>
            <label className="text-xs text-neutral-500" htmlFor="merge-target">
              {t("graph.merge.keep")}
            </label>
            <select
              id="merge-target"
              className="h-8 rounded-md border border-[#e5e5e5] bg-white px-2 text-xs text-neutral-900"
              value={effectiveTargetId}
              onChange={(event) => setTargetId(event.target.value)}
            >
              {selectedCreatives.map((node) => (
                <option key={node.id} value={node.id}>
                  {node.label}
                </option>
              ))}
            </select>
            <Button size="sm" disabled={!ready || mergeMutation.isPending} onClick={() => setConfirmOpen(true)}>
              {t("graph.merge.merge")}
            </Button>
          </>
        ) : (
          <p className="text-xs text-neutral-400">{t("graph.merge.selectMore")}</p>
        )}
        <Button size="sm" variant="ghost" onClick={clearMergeSelection}>
          {t("graph.merge.clear")}
        </Button>
        {mergeMutation.isError ? (
          <p className="text-xs text-red-600">
            {mergeMutation.error instanceof Error ? mergeMutation.error.message : t("graph.merge.failed")}
          </p>
        ) : null}
      </div>

      <Dialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title={t("graph.merge.dialogTitle")}
        description={t("graph.merge.dialogDesc")}
        footer={
          <>
            <Button size="sm" variant="outline" onClick={() => setConfirmOpen(false)}>
              {t("graph.merge.cancel")}
            </Button>
            <Button
              size="sm"
              disabled={mergeMutation.isPending || (blockMessage !== null && !forceReason.trim())}
              onClick={onConfirmMerge}
            >
              {mergeMutation.isPending
                ? t("graph.merge.merging")
                : blockMessage
                  ? t("graph.merge.forceMerge")
                  : t("graph.merge.confirm")}
            </Button>
          </>
        }
      >
        {ready && target && source ? (
          <>
            <p>
              {t("graph.merge.confirmLineA")}
              <span className="font-medium">{source.label}</span>
              {t("graph.merge.confirmLineB")}
              <span className="font-medium">{target.label}</span>
              {t("graph.merge.confirmLineC")}
            </p>
            {blockMessage ? (
              <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3">
                <p className="text-xs text-amber-800">{blockMessage}</p>
                <input
                  className="mt-2 h-8 w-full rounded-md border border-amber-300 bg-white px-2 text-sm"
                  placeholder={t("graph.merge.forcePlaceholder")}
                  value={forceReason}
                  onChange={(event) => setForceReason(event.target.value)}
                />
              </div>
            ) : null}
          </>
        ) : null}
      </Dialog>
    </>
  );
}
