import { useMemo, useState } from "react";
import type { GraphResponse } from "@shared";
import { useMergeCreatives } from "../../hooks/useGraph";
import { useGraphStore } from "../../stores/graphStore";
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
          {selectedCreatives.length}/2 creatives selected
        </p>
        {selectedCreatives.length === 2 ? (
          <>
            <label className="text-xs text-neutral-500" htmlFor="merge-target">
              Keep
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
              Merge
            </Button>
          </>
        ) : (
          <p className="text-xs text-neutral-400">Select one more creative</p>
        )}
        <Button size="sm" variant="ghost" onClick={clearMergeSelection}>
          Clear
        </Button>
        {mergeMutation.isError ? (
          <p className="text-xs text-red-600">
            {mergeMutation.error instanceof Error ? mergeMutation.error.message : "Merge failed"}
          </p>
        ) : null}
      </div>

      <Dialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title="Merge creatives"
        description="The source creative will be merged into the target. This cannot be undone from the UI."
        footer={
          <>
            <Button size="sm" variant="outline" onClick={() => setConfirmOpen(false)}>
              Cancel
            </Button>
            <Button
              size="sm"
              disabled={mergeMutation.isPending || (blockMessage !== null && !forceReason.trim())}
              onClick={onConfirmMerge}
            >
              {mergeMutation.isPending ? "Merging…" : blockMessage ? "强制合并" : "Confirm merge"}
            </Button>
          </>
        }
      >
        {ready && target && source ? (
          <>
            <p>
              Merge <span className="font-medium">{source.label}</span> into{" "}
              <span className="font-medium">{target.label}</span>.
            </p>
            {blockMessage ? (
              <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3">
                <p className="text-xs text-amber-800">{blockMessage}</p>
                <input
                  className="mt-2 h-8 w-full rounded-md border border-amber-300 bg-white px-2 text-sm"
                  placeholder="填写推翻既定裁决的理由（必填）"
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
