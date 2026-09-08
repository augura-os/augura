import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, X } from "lucide-react";
import type { GraphNodeDTO, GraphResponse } from "@shared";
import { childrenOf, parentOf } from "../../lib/graph-layout";
import { useAssetDetail } from "../../hooks/useAssetDetail";
import { useCreativePerformance } from "../../hooks/useCreativePerformance";
import { useDnas } from "../../hooks/useDnas";
import { useRecommendations } from "../../hooks/useRecommendations";
import { useSplitCreative } from "../../hooks/useGraph";
import { useGraphStore } from "../../stores/graphStore";
import { assignCreativeDna } from "../../services/api";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Skeleton } from "../ui/skeleton";
import { PerformanceSummary, PerformanceTable } from "../assets/PerformanceTable";
import { EvolutionSection } from "./EvolutionSection";
import { shortAssetLabel } from "../../lib/short-name";
import { useMetricConfig } from "../../hooks/useMetricConfig";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h4 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-neutral-500">
        {title}
      </h4>
      {children}
    </section>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  if (!value) return null;
  return (
    <div className="mb-2">
      <p className="text-[11px] font-medium text-neutral-400">{label}</p>
      <p className="text-sm text-neutral-800">{value}</p>
    </div>
  );
}

function ChipList({ items }: { items: string[] }) {
  if (items.length === 0) return <p className="text-sm text-neutral-400">—</p>;
  return (
    <div className="flex flex-wrap gap-1">
      {items.map((item) => (
        <Badge key={item} variant="secondary">
          {item}
        </Badge>
      ))}
    </div>
  );
}

function NodeLinkList({ nodes }: { nodes: GraphNodeDTO[] }) {
  if (nodes.length === 0) return <p className="text-sm text-neutral-400">—</p>;
  return (
    <ul className="space-y-1">
      {nodes.map((node) => (
        <li key={node.id} className="truncate text-sm text-neutral-800">
          {node.label}
        </li>
      ))}
    </ul>
  );
}

// --- Asset panel: fetch GET /assets/{id} ------------------------------------

function AssetPanel({ assetId }: { assetId: string }) {
  const { data, isLoading, isError, error } = useAssetDetail(assetId);

  if (isLoading) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-4 w-2/3" />
        <Skeleton className="h-20 w-full" />
        <Skeleton className="h-20 w-full" />
      </div>
    );
  }
  if (isError) {
    return (
      <p className="text-sm text-red-600">
        {error instanceof Error ? error.message : "Failed to load asset"}
      </p>
    );
  }
  if (!data) return null;

  const { asset, analysis, tags, creative, performance } = data;

  return (
    <div className="space-y-4">
      <Section title="Variant">
        <p className="text-sm text-neutral-800">
          Creative: <span className="font-medium">{creative?.name ?? "—"}</span>
        </p>
        <Link
          to={`/assets/${asset.id}`}
          className="mt-1 inline-block text-xs font-medium text-neutral-900 underline underline-offset-2 hover:text-neutral-600"
        >
          Open full detail →
        </Link>
      </Section>

      <Section title="Composition">
        {analysis ? (
          <div>
            <Field label="Hook" value={analysis.hook} />
            <Field label="Conflict" value={analysis.conflict} />
            <Field label="Gameplay" value={analysis.gameplay} />
            <Field label="Reward" value={analysis.reward} />
            <Field label="Characters" value={analysis.characters.join(", ")} />
            <Field label="Environment" value={analysis.environment.join(", ")} />
            <Field label="Emotion" value={analysis.emotion.join(", ")} />
            <Field label="Summary" value={analysis.summary} />
          </div>
        ) : (
          <p className="text-sm text-neutral-400">No analysis yet.</p>
        )}
      </Section>

      <Section title="Tags">
        <ChipList items={tags.length > 0 ? tags : (analysis?.tags ?? [])} />
      </Section>

      <Section title="Performance">
        {performance.length === 0 ? (
          <p className="text-sm text-neutral-400">No performance data.</p>
        ) : (
          <div className="space-y-2">
            <PerformanceSummary rows={performance} />
            <PerformanceTable rows={performance} />
          </div>
        )}
      </Section>
    </div>
  );
}

// --- Creative panel: aggregate from graph data + split -----------------------

function CreativePerformanceSection({ creativeId }: { creativeId: string }) {
  const { data, isLoading } = useCreativePerformance(creativeId);
  if (isLoading) {
    return <Skeleton className="h-16 w-full" />;
  }
  const rows = data ?? [];
  if (rows.length === 0) {
    return <p className="text-sm text-neutral-400">No performance data.</p>;
  }
  // Summary only — daily rows live on the asset detail page.
  return <PerformanceSummary rows={rows} />;
}

/** DNA 归族/改归（Creative 面板）——写 edit_logs，correction 自动回流训练。 */
function DnaAssignSection({
  creativeId,
  dnaLabel,
}: {
  creativeId: string;
  dnaLabel: string | null;
}) {
  const queryClient = useQueryClient();
  const { data: dnas } = useDnas();
  const [selected, setSelected] = useState("");
  const [done, setDone] = useState<string | null>(null);
  const mutation = useMutation({
    mutationFn: () => assignCreativeDna(creativeId, selected),
    onSuccess: (dna) => {
      setDone(dna ? `${dna.code} ${dna.name}` : "已取消归族");
      void queryClient.invalidateQueries({ queryKey: ["graph"] });
      void queryClient.invalidateQueries({ queryKey: ["review-queue"] });
      void queryClient.invalidateQueries({ queryKey: ["recommendations"] });
    },
  });
  return (
    <Section title="DNA 归族">
      <p className="mb-1.5 text-sm text-neutral-800">
        当前：{dnaLabel ?? <span className="text-neutral-400">未归族</span>}
      </p>
      {done ? (
        <p className="inline-flex items-center gap-1 rounded-md bg-violet-50 px-1.5 py-0.5 text-[11px] font-medium text-violet-600">
          <Check className="h-3 w-3" /> {done}
        </p>
      ) : (
        <span className="flex items-center gap-1">
          <select
            value={selected}
            onChange={(event) => setSelected(event.target.value)}
            className="h-6 max-w-[200px] rounded-md border border-black/10 bg-white px-1 text-[11px] text-neutral-700"
          >
            <option value="">选择家族…</option>
            {(dnas ?? []).map((dna) => (
              <option key={dna.id} value={dna.id}>
                {dna.code} {dna.name}
              </option>
            ))}
          </select>
          <button
            disabled={!selected || mutation.isPending}
            onClick={() => mutation.mutate()}
            className="rounded-md bg-violet-50 px-1.5 py-0.5 text-[11px] font-medium text-violet-600 transition hover:bg-violet-100 disabled:opacity-40"
          >
            {mutation.isPending ? "归族中…" : "改归"}
          </button>
          {mutation.isError ? (
            <span className="text-[11px] text-red-500">失败，请重试</span>
          ) : null}
        </span>
      )}
    </Section>
  );
}

function CreativePanel({ node, graph }: { node: GraphNodeDTO; graph: GraphResponse }) {
  const { marketPrefixes } = useMetricConfig();
  const variants = childrenOf(graph, node.id, "HAS_VARIANT");
  const assets = variants.flatMap((variant) => childrenOf(graph, variant.id, "HAS_ASSET"));
  const tags = Array.from(
    new Map(
      assets
        .flatMap((asset) => childrenOf(graph, asset.id, "HAS_TAG"))
        .map((tag) => [tag.id, tag]),
    ).values(),
  );
  const { data: recommendations } = useRecommendations();
  const recommendation = recommendations?.items.find(
    (item) => item.creative_id === node.ref_id,
  );
  const dnaLabel = parentOf(graph, node.id, "HAS_CREATIVE")?.label ?? null;

  const [splitIds, setSplitIds] = useState<string[]>([]);
  const splitMutation = useSplitCreative();
  const mergeSelection = useGraphStore((state) => state.mergeSelection);
  const toggleMergeSelection = useGraphStore((state) => state.toggleMergeSelection);
  const selectedForMerge = mergeSelection.includes(node.id);

  const toggleSplit = (variantId: string) => {
    setSplitIds((prev) =>
      prev.includes(variantId) ? prev.filter((id) => id !== variantId) : [...prev, variantId],
    );
  };

  const onSplit = () => {
    if (splitIds.length === 0) return;
    const variantRefIds = variants
      .filter((variant) => splitIds.includes(variant.id))
      .map((variant) => variant.ref_id);
    splitMutation.mutate(
      { creative_id: node.ref_id, variant_ids: variantRefIds },
      { onSuccess: () => setSplitIds([]) },
    );
  };

  return (
    <div className="space-y-4">
      {recommendation ? (
        <Section title="Recommendation">
          <p className="mb-1.5">
            <Badge
              variant="secondary"
              className={
                recommendation.action === "KEEP"
                  ? "bg-emerald-100 text-emerald-800"
                  : recommendation.action === "ITERATE"
                    ? "bg-amber-100 text-amber-800"
                    : recommendation.action === "PAUSE"
                      ? "bg-red-100 text-red-800"
                      : "bg-neutral-100 text-neutral-600"
              }
            >
              {recommendation.action}
            </Badge>
          </p>
          <ul className="list-disc space-y-1 pl-4 text-xs text-neutral-600">
            {recommendation.reasons.map((reason, index) => (
              <li key={index}>{reason}</li>
            ))}
          </ul>
        </Section>
      ) : null}

      <Section title="Composition">
        <p className="text-sm text-neutral-800">
          {variants.length} variant{variants.length === 1 ? "" : "s"} · {assets.length} asset
          {assets.length === 1 ? "" : "s"} · {tags.length} tag{tags.length === 1 ? "" : "s"}
        </p>
      </Section>

      <DnaAssignSection creativeId={node.ref_id} dnaLabel={dnaLabel} />

      <Section title="Performance">
        <CreativePerformanceSection creativeId={node.ref_id} />
      </Section>

      <EvolutionSection creativeId={node.ref_id} />

      <Section title="Tags">
        <ChipList items={tags.map((tag) => tag.label)} />
      </Section>

      <Section title="Merge">
        <label className="flex items-center gap-2 text-sm text-neutral-800">
          <input
            type="checkbox"
            className="h-3.5 w-3.5 accent-neutral-900"
            checked={selectedForMerge}
            onChange={() => toggleMergeSelection(node.id)}
          />
          Select for merge
        </label>
        <p className="mt-1 text-xs text-neutral-400">
          Select two creatives (checkbox or shift-click), then use the merge bar at the top.
        </p>
      </Section>

      <Section title="Variants — split out">
        {variants.length === 0 ? (
          <p className="text-sm text-neutral-400">No variants.</p>
        ) : (
          <div className="space-y-1.5">
            {variants.map((variant) => (
              <label
                key={variant.id}
                className="flex items-center gap-2 text-sm text-neutral-800"
                title={variant.label}
              >
                <input
                  type="checkbox"
                  className="h-3.5 w-3.5 shrink-0 accent-neutral-900"
                  checked={splitIds.includes(variant.id)}
                  onChange={() => toggleSplit(variant.id)}
                />
                <span className="line-clamp-2 break-all leading-snug">
                  {shortAssetLabel(variant.label, marketPrefixes)}
                </span>
              </label>
            ))}
            <Button
              size="sm"
              variant="outline"
              className="mt-2"
              disabled={splitIds.length === 0 || splitMutation.isPending}
              onClick={onSplit}
            >
              {splitMutation.isPending ? "Splitting…" : `Split ${splitIds.length || ""} into new creative`}
            </Button>
            {splitMutation.isError ? (
              <p className="text-xs text-red-600">
                {splitMutation.error instanceof Error
                  ? splitMutation.error.message
                  : "Split failed"}
              </p>
            ) : null}
          </div>
        )}
      </Section>
    </div>
  );
}

// --- Variant / tag panels -----------------------------------------------------

function VariantPanel({ node, graph }: { node: GraphNodeDTO; graph: GraphResponse }) {
  const creative = parentOf(graph, node.id, "HAS_VARIANT");
  const assets = childrenOf(graph, node.id, "HAS_ASSET");
  return (
    <div className="space-y-4">
      <Section title="Creative">
        <p className="text-sm text-neutral-800">{creative?.label ?? "—"}</p>
      </Section>
      <Section title="Assets">
        <NodeLinkList nodes={assets} />
      </Section>
    </div>
  );
}

function TagPanel({ node, graph }: { node: GraphNodeDTO; graph: GraphResponse }) {
  const assets = graph.edges
    .filter((edge) => edge.target === node.id && edge.type === "HAS_TAG")
    .map((edge) => graph.nodes.find((candidate) => candidate.id === edge.source))
    .filter((candidate): candidate is GraphNodeDTO => Boolean(candidate));
  return (
    <div className="space-y-4">
      <Section title="Assets with this tag">
        <NodeLinkList nodes={assets} />
      </Section>
    </div>
  );
}

// --- Panel shell ---------------------------------------------------------------

interface GraphNodePanelProps {
  node: GraphNodeDTO;
  graph: GraphResponse;
  onClose: () => void;
}

export function GraphNodePanel({ node, graph, onClose }: GraphNodePanelProps) {
  const { marketPrefixes } = useMetricConfig();
  return (
    <aside className="flex w-[380px] shrink-0 flex-col border-l border-[#e5e5e5] bg-white">
      <header className="flex items-start justify-between gap-2 border-b border-[#e5e5e5] px-4 py-3">
        <div className="min-w-0">
          <p
            className="line-clamp-2 break-all text-sm font-semibold leading-snug text-neutral-900"
            title={node.label}
          >
            {node.type === "asset" || node.type === "variant"
              ? shortAssetLabel(node.label, marketPrefixes)
              : node.label}
          </p>
          <Badge variant="secondary" className="mt-1 capitalize">
            {node.type}
          </Badge>
        </div>
        <button
          type="button"
          aria-label="Close panel"
          className="rounded-md p-1 text-neutral-400 hover:bg-neutral-100 hover:text-neutral-600"
          onClick={onClose}
        >
          <X className="h-4 w-4" />
        </button>
      </header>
      <div className="flex-1 overflow-y-auto p-4">
        {node.type === "asset" ? <AssetPanel assetId={node.ref_id} /> : null}
        {node.type === "creative" ? <CreativePanel node={node} graph={graph} /> : null}
        {node.type === "variant" ? <VariantPanel node={node} graph={graph} /> : null}
        {node.type === "tag" ? <TagPanel node={node} graph={graph} /> : null}
      </div>
    </aside>
  );
}
