import { ArrowDown, Check, CircleHelp, X } from "lucide-react";
import { useEvolution, useUpdateDerivation } from "../../hooks/useEvolution";
import { factorLabel, shortAssetLabel } from "../../lib/short-name";
import { useMetricConfig } from "../../hooks/useMetricConfig";
import { useT } from "../../lib/i18n";
import { Badge } from "../ui/badge";
import { Skeleton } from "../ui/skeleton";
import { cn } from "../../lib/utils";

function fmtCpp(cpp: number | null): string {
  return cpp === null ? "—" : `$${cpp.toFixed(2)}`;
}

function fmtRoas(roas: number | null): string {
  return roas === null ? "—" : `${(roas * 100).toFixed(1)}%`;
}

export function EvolutionSection({ creativeId }: { creativeId: string }) {
  const t = useT();
  const { data, isLoading } = useEvolution(creativeId);
  const { marketPrefixes } = useMetricConfig();
  const updateMutation = useUpdateDerivation(creativeId);

  if (isLoading) return <Skeleton className="h-16 w-full" />;
  if (!data || data.steps.length === 0) return null;

  return (
    <section>
      <h4 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-neutral-500">
        {t("graph.evolution.derivations").replace("{n}", String(data.steps.length))}
        {data.pending_count > 0 ? (
          <span className="ml-1.5 font-normal text-amber-600">
            {t("graph.evolution.pending").replace("{n}", String(data.pending_count))}
          </span>
        ) : null}
      </h4>
      <div className="space-y-2">
        {data.steps.map((step) => {
          const cppGood = step.cpp_delta !== null && step.cpp_delta < 0;
          const roasGood = step.roas_delta !== null && step.roas_delta > 0;
          const verdict = step.derivation.verdict;
          return (
            <div
              key={step.derivation.id}
              className="rounded-xl border border-black/5 bg-white/60 p-2.5"
            >
              <div className="flex items-center justify-between gap-2">
                <span
                  className="min-w-0 truncate text-xs font-medium text-neutral-800"
                  title={step.source.filename}
                >
                  {shortAssetLabel(step.source.filename, marketPrefixes)}
                </span>
                <span className="shrink-0 text-[11px] text-neutral-400">
                  {fmtCpp(step.source.cpp)} · {fmtRoas(step.source.roas)}
                </span>
              </div>

              <div className="my-1 flex items-center gap-1.5 text-[11px] text-neutral-400">
                <ArrowDown className="h-3 w-3" />
                <Badge variant="secondary" className="px-1.5 py-0 text-[10px]">
                  {factorLabel(step.derivation.factor)}
                </Badge>
                {step.cpp_delta !== null ? (
                  <span className={cn("font-medium", cppGood ? "text-emerald-600" : "text-red-500")}>
                    CPP{cppGood ? "" : "+"}{step.cpp_delta.toFixed(2)}
                  </span>
                ) : null}
                {step.roas_delta !== null ? (
                  <span className={cn("font-medium", roasGood ? "text-emerald-600" : "text-red-500")}>
                    ROAS{roasGood ? "+" : ""}{(step.roas_delta * 100).toFixed(1)}%
                  </span>
                ) : null}
                <span className="ml-auto flex items-center gap-0.5">
                  <button
                    title={t("graph.evolution.verdictPositive")}
                    disabled={updateMutation.isPending}
                    onClick={() =>
                      updateMutation.mutate({ derivationId: step.derivation.id, verdict: "positive" })
                    }
                    className={cn(
                      "rounded-full p-0.5 transition",
                      verdict === "positive"
                        ? "bg-emerald-100 text-emerald-600"
                        : "text-neutral-300 hover:bg-black/5 hover:text-emerald-600",
                    )}
                  >
                    <Check className="h-3.5 w-3.5" />
                  </button>
                  <button
                    title={t("graph.evolution.verdictNegative")}
                    disabled={updateMutation.isPending}
                    onClick={() =>
                      updateMutation.mutate({ derivationId: step.derivation.id, verdict: "negative" })
                    }
                    className={cn(
                      "rounded-full p-0.5 transition",
                      verdict === "negative"
                        ? "bg-red-100 text-red-500"
                        : "text-neutral-300 hover:bg-black/5 hover:text-red-500",
                    )}
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                  <button
                    title={t("graph.evolution.verdictPending")}
                    disabled={updateMutation.isPending}
                    onClick={() =>
                      updateMutation.mutate({ derivationId: step.derivation.id, verdict: "pending" })
                    }
                    className={cn(
                      "rounded-full p-0.5 transition",
                      verdict === "pending"
                        ? "bg-amber-100 text-amber-500"
                        : "text-neutral-300 hover:bg-black/5 hover:text-amber-500",
                    )}
                  >
                    <CircleHelp className="h-3.5 w-3.5" />
                  </button>
                </span>
              </div>

              <div className="flex items-center justify-between gap-2">
                <span
                  className="min-w-0 truncate text-xs font-medium text-neutral-800"
                  title={step.target.filename}
                >
                  {shortAssetLabel(step.target.filename, marketPrefixes)}
                </span>
                <span className="shrink-0 text-[11px] text-neutral-400">
                  {fmtCpp(step.target.cpp)} · {fmtRoas(step.target.roas)}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
