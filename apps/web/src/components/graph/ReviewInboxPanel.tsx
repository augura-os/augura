import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, Inbox, X } from "lucide-react";
import type { DerivationFactor, ReviewItem, ReviewKind } from "@shared";
import { useReviewQueue } from "../../hooks/useReviewQueue";
import { useDnas } from "../../hooks/useDnas";
import {
  assignCreativeDna,
  closeSimilarPair,
  deleteDerivation,
  linkSimilar,
  mergeCreatives,
  updateDerivation,
  updateLifecycle,
} from "../../services/api";
import { FACTOR_LABEL } from "../../lib/short-name";
import { cn } from "../../lib/utils";

const KIND_META: Record<ReviewKind, { label: string; dot: string }> = {
  archive_suggestion: { label: "建议归档", dot: "bg-stone-500" },
  pending_verdict: { label: "裂变待判定", dot: "bg-violet-500" },
  derivation_review: { label: "裂变复核", dot: "bg-orange-500" },
  merge_candidate: { label: "疑似待合并", dot: "bg-amber-500" },
  dna_unassigned: { label: "DNA 未归族", dot: "bg-rose-400" },
  observation_pair: { label: "观察对未结案", dot: "bg-sky-500" },
  low_confidence: { label: "低置信复核", dot: "bg-red-500" },
  market_conflict: { label: "市场存疑", dot: "bg-teal-500" },
  threshold_calibration: { label: "阈值校准", dot: "bg-cyan-500" },
  market_detect: { label: "市场前缀发现", dot: "bg-lime-500" },
};

const KIND_ORDER: ReviewKind[] = [
  "archive_suggestion",
  "pending_verdict",
  "derivation_review",
  "merge_candidate",
  "dna_unassigned",
  "observation_pair",
  "market_conflict",
  "threshold_calibration",
  "market_detect",
  "low_confidence",
];

/** judge_stats 的类别名 → 人话标签（judge_suggestions.kind） */
const JUDGE_KIND_LABEL: Record<string, string> = {
  dna_assign: "归族建议",
  merge_pair: "合并建议",
  verdict: "裂变判定",
  "derivation-factor": "因子复核",
  observation_pair: "观察对建议",
};

/** 顶部小字统计：各类别"近期采纳率 x% · 自动改判率 y%"，无数据的类别不出声 */
function judgeStatsLine(data: { judge_stats?: Record<string, { auto_total: number; override_rate: number; acceptance_rate: number | null }> } | undefined): string {
  const stats = data?.judge_stats ?? {};
  const parts: string[] = [];
  for (const [kind, s] of Object.entries(stats)) {
    const label = JUDGE_KIND_LABEL[kind] ?? kind;
    if (s.acceptance_rate !== null && s.acceptance_rate !== undefined) {
      parts.push(`${label}近期采纳率 ${Math.round(s.acceptance_rate * 100)}%`);
    }
    if (s.auto_total > 0) {
      parts.push(`${label}自动改判率 ${Math.round(s.override_rate * 100)}%`);
    }
  }
  return parts.join(" · ");
}

/** Two-step confirm: first click arms the button for 3s, second executes. */
function useConfirm() {
  const [armed, setArmed] = useState<string | null>(null);
  const arm = (key: string, execute: () => void) => {
    if (armed === key) {
      setArmed(null);
      execute();
      return;
    }
    setArmed(key);
    setTimeout(() => setArmed((current) => (current === key ? null : current)), 3000);
  };
  return { armed, arm };
}

function MergeActions({ item }: { item: ReviewItem }) {
  const queryClient = useQueryClient();
  const { armed, arm } = useConfirm();
  const [done, setDone] = useState<string | null>(null);
  const [blockMessage, setBlockMessage] = useState<string | null>(null);
  const [forceReason, setForceReason] = useState("");
  const [pendingBody, setPendingBody] = useState<{
    source_creative_id: string;
    target_creative_id: string;
  } | null>(null);
  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["review-queue"] });
    void queryClient.invalidateQueries({ queryKey: ["graph"] });
    void queryClient.invalidateQueries({ queryKey: ["recommendations"] });
  };
  const mergeMutation = useMutation({
    mutationFn: (body: {
      source_creative_id: string;
      target_creative_id: string;
      force_reason?: string;
    }) => mergeCreatives(body),
    onSuccess: () => {
      setDone("已合并");
      setBlockMessage(null);
      invalidate();
    },
    onError: (error, body) => {
      const message = error instanceof Error ? error.message : "";
      if (message.includes("守卫拦截")) {
        setBlockMessage(message);
        setPendingBody(body);
      }
    },
  });
  const similarMutation = useMutation({
    mutationFn: () =>
      linkSimilar({
        source_creative_id: item.creative_id!,
        target_creative_id: item.related_creative_id!,
        reason: "人工确认维持拆分（收件箱）",
      }),
    // 乐观更新：点击即出"已登记"徽章，后台刷新队列（队列重算约 3s，
    // 等它再显示会让用户以为没点上）；失败时回滚徽章重新露出按钮。
    onMutate: () => {
      setDone("已登记观察对");
    },
    onError: () => {
      setDone(null);
    },
    onSettled: () => {
      invalidate();
    },
  });
  if (!item.creative_id || !item.related_creative_id) return null;
  if (done) {
    return (
      <span className="mt-1 inline-flex items-center gap-1 rounded-md bg-neutral-100 px-1.5 py-0.5 text-[11px] font-medium text-neutral-500">
        <Check className="h-3 w-3" /> {done}
      </span>
    );
  }

  const left = item.creative_name ?? "左";
  const right = item.related_creative_name ?? "右";
  const busy = mergeMutation.isPending || similarMutation.isPending;

  if (blockMessage && pendingBody) {
    return (
      <span className="mt-1 block" onClick={(e) => e.stopPropagation()}>
        <span className="block rounded-md border border-amber-200 bg-amber-50 px-2 py-1.5 text-[11px] text-amber-800">
          {blockMessage}
        </span>
        <span className="mt-1 flex items-center gap-1">
          <input
            className="h-6 min-w-0 flex-1 rounded-md border border-amber-300 bg-white px-1.5 text-[11px]"
            placeholder="填写理由（必填）"
            value={forceReason}
            onChange={(event) => setForceReason(event.target.value)}
          />
          <button
            disabled={!forceReason.trim() || busy}
            onClick={() =>
              mergeMutation.mutate({ ...pendingBody, force_reason: forceReason })
            }
            className="rounded-md bg-amber-500 px-1.5 py-0.5 text-[11px] font-medium text-white transition hover:bg-amber-600 disabled:opacity-40"
          >
            强制合并
          </button>
        </span>
      </span>
    );
  }

  return (
    <span className="mt-1 block" onClick={(e) => e.stopPropagation()}>
      <span className="mb-1 block text-[10px] leading-snug text-neutral-400">
        同一创意？选一边并入（数据合并统计）· 不是同一条？拆开观察
      </span>
      <span className="flex items-center gap-1">
      {item.suggestion ? (
        <span
          className={cn(
            "rounded px-1.5 py-0.5 text-[11px] font-medium",
            item.suggestion === "merge"
              ? "bg-emerald-50 text-emerald-700"
              : item.suggestion === "observe"
                ? "bg-sky-50 text-sky-600"
                : "bg-neutral-100 text-neutral-500",
          )}
          title={item.suggestion_reason ?? undefined}
        >
          {item.suggestion === "merge"
            ? "建议合并"
            : item.suggestion === "observe"
              ? "建议继续观察"
              : "建议拆分"}
          {item.suggestion_votes ? ` ${item.suggestion_votes}/3` : ""}
        </span>
      ) : null}
      {([
        { key: "keep-left", label: `并入左`,
          title: `右并入左：${right} 的 Variant 和投放数据并入 ${left}，留痕可回滚`,
          body: { source_creative_id: item.related_creative_id, target_creative_id: item.creative_id } },
        { key: "keep-right", label: `并入右`,
          title: `左并入右：${left} 的 Variant 和投放数据并入 ${right}，留痕可回滚`,
          body: { source_creative_id: item.creative_id, target_creative_id: item.related_creative_id } },
      ]).map(({ key, label, title, body }) => (
        <button
          key={key}
          title={title}
          disabled={busy}
          onClick={() => arm(key + item.title, () => mergeMutation.mutate(body))}
          className={cn(
            "rounded-md px-1.5 py-0.5 text-[11px] font-medium transition",
            armed === key + item.title
              ? "bg-emerald-500 text-white"
              : "bg-emerald-50 text-emerald-700 hover:bg-emerald-100",
          )}
        >
          {armed === key + item.title ? "确认合并？" : label}
        </button>
      ))}
      <button
        title="登记为观察对：先拆开，等数据多了再判断是否同一条"
        disabled={busy}
        onClick={() => similarMutation.mutate()}
        className="rounded-md bg-neutral-100 px-1.5 py-0.5 text-[11px] font-medium text-neutral-500 transition hover:bg-neutral-200"
      >
        不是同一条
      </button>
      </span>
    </span>
  );
}

function VerdictActions({ item }: { item: ReviewItem }) {
  const queryClient = useQueryClient();
  const [done, setDone] = useState<"positive" | "negative" | null>(null);
  const mutation = useMutation({
    mutationFn: (verdict: "positive" | "negative") =>
      updateDerivation(item.derivation_id!, { verdict }),
    onSuccess: (_data, verdict) => {
      setDone(verdict);
      void queryClient.invalidateQueries({ queryKey: ["review-queue"] });
      void queryClient.invalidateQueries({ queryKey: ["evolution"] });
    },
  });
  if (!item.derivation_id) return null;
  if (done) {
    return (
      <span
        className={cn(
          "mt-1 inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium",
          done === "positive"
            ? "bg-emerald-50 text-emerald-600"
            : "bg-red-50 text-red-500",
        )}
      >
        {done === "positive" ? (
          <Check className="h-3 w-3" />
        ) : (
          <X className="h-3 w-3" />
        )}
        已判定{done === "positive" ? "有效" : "无效"}
      </span>
    );
  }
  return (
    <span className="mt-1 flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
      {item.suggestion ? (
        <span
          className={cn(
            "rounded px-1.5 py-0.5 text-[11px] font-medium",
            item.suggestion === "positive"
              ? "bg-emerald-50 text-emerald-700"
              : item.suggestion === "negative"
                ? "bg-red-50 text-red-600"
                : "bg-neutral-100 text-neutral-500",
          )}
          title={item.suggestion_reason ?? undefined}
        >
          {item.suggestion === "positive"
            ? "建议有效"
            : item.suggestion === "negative"
              ? "建议无效"
              : "建议暂缓"}
          {item.suggestion_votes ? ` ${item.suggestion_votes}/3` : ""}
        </span>
      ) : null}
      <button
        title="判定：有效"
        disabled={mutation.isPending}
        onClick={() => mutation.mutate("positive")}
        className="flex items-center gap-0.5 rounded-md bg-emerald-50 px-1.5 py-0.5 text-[11px] font-medium text-emerald-700 transition hover:bg-emerald-100 disabled:opacity-50"
      >
        <Check className="h-3 w-3" /> {mutation.isPending ? "判定中…" : "有效"}
      </button>
      <button
        title="判定：无效"
        disabled={mutation.isPending}
        onClick={() => mutation.mutate("negative")}
        className="flex items-center gap-0.5 rounded-md bg-red-50 px-1.5 py-0.5 text-[11px] font-medium text-red-600 transition hover:bg-red-100 disabled:opacity-50"
      >
        <X className="h-3 w-3" /> {mutation.isPending ? "判定中…" : "无效"}
      </button>
      {mutation.isError ? (
        <span className="text-[11px] text-red-500">失败，请重试</span>
      ) : null}
    </span>
  );
}

function DnaAssignActions({ item }: { item: ReviewItem }) {
  const queryClient = useQueryClient();
  const { data: dnas } = useDnas();
  const [selected, setSelected] = useState(item.suggested_dna_id ?? "");
  const [done, setDone] = useState<string | null>(null);
  const mutation = useMutation({
    mutationFn: () => assignCreativeDna(item.creative_id!, selected),
    onSuccess: (dna) => {
      setDone(dna ? `${dna.code} ${dna.name}` : "已归族");
      void queryClient.invalidateQueries({ queryKey: ["review-queue"] });
      void queryClient.invalidateQueries({ queryKey: ["graph"] });
    },
  });
  if (!item.creative_id) return null;
  if (done) {
    return (
      <span className="mt-1 inline-flex items-center gap-1 rounded-md bg-rose-50 px-1.5 py-0.5 text-[11px] font-medium text-rose-600">
        <Check className="h-3 w-3" /> {done}
      </span>
    );
  }
  return (
    <span className="mt-1 flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
      {item.suggestion ? (
        <span
          className="rounded bg-violet-50 px-1.5 py-0.5 text-[11px] font-medium text-violet-600"
          title={item.suggestion_reason ?? undefined}
        >
          建议 {item.suggestion}
          {item.suggestion_votes ? ` (${item.suggestion_votes}/3)` : ""}
        </span>
      ) : null}
      <select
        value={selected}
        onChange={(event) => setSelected(event.target.value)}
        className="h-6 max-w-[180px] rounded-md border border-black/10 bg-white px-1 text-[11px] text-neutral-700"
      >
        <option value="">选择 DNA 家族…</option>
        {(dnas ?? []).map((dna) => (
          <option key={dna.id} value={dna.id}>
            {dna.code} {dna.name}
          </option>
        ))}
      </select>
      <button
        disabled={!selected || mutation.isPending}
        onClick={() => mutation.mutate()}
        className="rounded-md bg-rose-50 px-1.5 py-0.5 text-[11px] font-medium text-rose-600 transition hover:bg-rose-100 disabled:opacity-40"
      >
        {mutation.isPending ? "归族中…" : "归族"}
      </button>
      {mutation.isError ? (
        <span className="text-[11px] text-red-500">失败，请重试</span>
      ) : null}
    </span>
  );
}

function DerivationReviewActions({ item }: { item: ReviewItem }) {
  const queryClient = useQueryClient();
  const { armed, arm } = useConfirm();
  const [done, setDone] = useState<string | null>(null);
  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["review-queue"] });
    void queryClient.invalidateQueries({ queryKey: ["evolution"] });
    void queryClient.invalidateQueries({ queryKey: ["graph"] });
  };
  const unlinkMutation = useMutation({
    mutationFn: () => deleteDerivation(item.derivation_id!),
    onSuccess: () => {
      setDone("已解链");
      invalidate();
    },
  });
  const adoptMutation = useMutation({
    mutationFn: () =>
      updateDerivation(item.derivation_id!, {
        factor: item.suggestion as DerivationFactor,
      }),
    onSuccess: () => {
      setDone("已采纳");
      invalidate();
    },
  });
  if (!item.derivation_id || !item.suggestion) return null;
  if (done) {
    return (
      <span className="mt-1 inline-flex items-center gap-1 rounded-md bg-neutral-100 px-1.5 py-0.5 text-[11px] font-medium text-neutral-500">
        <Check className="h-3 w-3" /> {done}
      </span>
    );
  }
  const isUnlink = item.suggestion === "not-a-derivation";
  const busy = unlinkMutation.isPending || adoptMutation.isPending;
  const factorLabel = FACTOR_LABEL[item.suggestion] ?? item.suggestion;
  return (
    <span className="mt-1 flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
      <span
        className={cn(
          "rounded px-1.5 py-0.5 text-[11px] font-medium",
          isUnlink ? "bg-red-50 text-red-600" : "bg-orange-50 text-orange-700",
        )}
        title={item.suggestion_reason ?? undefined}
      >
        {isUnlink ? "建议解链" : `建议改为 ${factorLabel}`}
        {item.suggestion_votes ? ` ${item.suggestion_votes}/3` : ""}
      </span>
      {isUnlink ? (
        <button
          title="确认这两条素材不是裂变关系，移除裂变边"
          disabled={busy}
          onClick={() => arm("unlink" + item.derivation_id, () => unlinkMutation.mutate())}
          className={cn(
            "rounded-md px-1.5 py-0.5 text-[11px] font-medium transition",
            armed === "unlink" + item.derivation_id
              ? "bg-red-500 text-white"
              : "bg-red-50 text-red-600 hover:bg-red-100",
          )}
        >
          {armed === "unlink" + item.derivation_id
            ? "确认解链？"
            : unlinkMutation.isPending
              ? "解链中…"
              : "确认解链"}
        </button>
      ) : (
        <button
          title={`把因子改为「${factorLabel}」`}
          disabled={busy}
          onClick={() => adoptMutation.mutate()}
          className="rounded-md bg-orange-50 px-1.5 py-0.5 text-[11px] font-medium text-orange-700 transition hover:bg-orange-100 disabled:opacity-40"
        >
          {adoptMutation.isPending ? "采纳中…" : "采纳"}
        </button>
      )}
      {unlinkMutation.isError || adoptMutation.isError ? (
        <span className="text-[11px] text-red-500">失败，请重试</span>
      ) : null}
    </span>
  );
}

function ArchiveActions({ item }: { item: ReviewItem }) {
  const queryClient = useQueryClient();
  const { armed, arm } = useConfirm();
  const [done, setDone] = useState<string | null>(null);
  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["review-queue"] });
    void queryClient.invalidateQueries({ queryKey: ["graph"] });
    void queryClient.invalidateQueries({ queryKey: ["recommendations"] });
  };
  const mutation = useMutation({
    mutationFn: (state: "archived" | "watch") =>
      updateLifecycle(item.creative_id!, {
        state,
        reason:
          state === "archived" ? "收件箱确认归档" : "收件箱保留观察",
      }),
    onSuccess: (_data, state) => {
      setDone(state === "archived" ? "已归档" : "已保留观察");
      invalidate();
    },
  });
  if (!item.creative_id) return null;
  if (done) {
    return (
      <span className="mt-1 inline-flex items-center gap-1 rounded-md bg-neutral-100 px-1.5 py-0.5 text-[11px] font-medium text-neutral-500">
        <Check className="h-3 w-3" /> {done}
      </span>
    );
  }
  return (
    <span className="mt-1 flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
      <button
        title="归档：从图谱和列表隐藏（数据全留，随时可恢复）"
        disabled={mutation.isPending}
        onClick={() => arm("archive" + item.creative_id, () => mutation.mutate("archived"))}
        className={cn(
          "rounded-md px-1.5 py-0.5 text-[11px] font-medium transition",
          armed === "archive" + item.creative_id
            ? "bg-stone-700 text-white"
            : "bg-stone-100 text-stone-600 hover:bg-stone-200",
        )}
      >
        {armed === "archive" + item.creative_id ? "确认归档？" : "确认归档"}
      </button>
      <button
        title="保留观察：标为 watch，暂不归档"
        disabled={mutation.isPending}
        onClick={() => mutation.mutate("watch")}
        className="rounded-md bg-sky-50 px-1.5 py-0.5 text-[11px] font-medium text-sky-600 transition hover:bg-sky-100 disabled:opacity-40"
      >
        保留观察
      </button>
      {mutation.isError ? (
        <span className="text-[11px] text-red-500">失败，请重试</span>
      ) : null}
    </span>
  );
}

function ClosePairActions({ item }: { item: ReviewItem }) {
  const queryClient = useQueryClient();
  const { armed, arm } = useConfirm();
  const [done, setDone] = useState<string | null>(null);
  const mutation = useMutation({
    mutationFn: () =>
      closeSimilarPair({
        source_creative_id: item.creative_id!,
        target_creative_id: item.related_creative_id!,
        reason: item.suggestion_reason
          ? `采纳 LLM 建议（${item.suggestion_votes ?? "-"}/3）：${item.suggestion_reason}`
          : "人工结案维持拆分",
      }),
    onSuccess: () => {
      setDone("已结案（维持拆分）");
      void queryClient.invalidateQueries({ queryKey: ["review-queue"] });
      void queryClient.invalidateQueries({ queryKey: ["graph"] });
    },
  });
  if (!item.creative_id || !item.related_creative_id) return null;
  if (done) {
    return (
      <span className="mt-1 inline-flex items-center gap-1 rounded-md bg-neutral-100 px-1.5 py-0.5 text-[11px] font-medium text-neutral-500">
        <Check className="h-3 w-3" /> {done}
      </span>
    );
  }
  return (
    <span className="mt-1 flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
      <MergeActions item={item} />
      <button
        title="结案：确认维持拆分，移除观察对"
        disabled={mutation.isPending}
        onClick={() => arm("close" + item.title, () => mutation.mutate())}
        className={cn(
          "rounded-md px-1.5 py-0.5 text-[11px] font-medium transition",
          armed === "close" + item.title
            ? "bg-sky-500 text-white"
            : "bg-sky-50 text-sky-600 hover:bg-sky-100",
        )}
      >
        {armed === "close" + item.title ? "确认结案？" : "结案"}
      </button>
      {mutation.isError ? (
        <span className="text-[11px] text-red-500">失败，请重试</span>
      ) : null}
    </span>
  );
}

export function ReviewInboxPanel({
  onSelectCreative,
}: {
  onSelectCreative: (creativeNodeId: string) => void;
}) {
  const { data } = useReviewQueue();
  const [collapsed, setCollapsed] = useState(true);

  const groups = useMemo(() => {
    const map = new Map<ReviewKind, ReviewItem[]>();
    map.set("archive_suggestion", data?.archive_suggestions ?? []);
    map.set("pending_verdict", data?.pending_verdicts ?? []);
    map.set("derivation_review", data?.derivation_reviews ?? []);
    map.set("merge_candidate", data?.merge_candidates ?? []);
    map.set("dna_unassigned", data?.dna_unassigned ?? []);
    map.set("observation_pair", data?.observation_pairs ?? []);
    map.set("market_conflict", data?.market_conflicts ?? []);
    map.set("threshold_calibration", data?.threshold_calibrations ?? []);
    map.set("market_detect", data?.market_detects ?? []);
    map.set("low_confidence", data?.low_confidence ?? []);
    return map;
  }, [data]);

  if (!data) return null;
  const total = KIND_ORDER.reduce((sum, kind) => sum + (groups.get(kind)?.length ?? 0), 0);
  const statsLine = judgeStatsLine(data);

  if (collapsed) {
    return (
      <button
        onClick={() => setCollapsed(false)}
        className="pointer-events-auto absolute right-4 top-4 flex items-center gap-2 rounded-full bg-white/85 px-4 py-2 shadow-lg ring-1 ring-black/5 backdrop-blur-xl transition hover:bg-white"
      >
        <Inbox className="h-4 w-4 text-sky-500" />
        <span className="text-sm font-medium text-neutral-800">待审核</span>
        <span className={cn(
          "rounded-full px-1.5 text-[11px] font-semibold",
          total > 0 ? "bg-red-500 text-white" : "bg-neutral-100 text-neutral-400",
        )}>
          {total}
        </span>
      </button>
    );
  }

  return (
    <div className="pointer-events-auto absolute right-4 top-4 flex max-h-[calc(100%-2rem)] w-[400px] flex-col overflow-hidden rounded-2xl bg-white/85 shadow-lg ring-1 ring-black/5 backdrop-blur-xl">
      <div className="flex items-center justify-between px-5 pb-2 pt-4">
        <h2 className="flex items-center gap-1.5 text-base font-semibold tracking-tight text-neutral-900">
          <Inbox className="h-4 w-4 text-sky-500" />
          待审核
          <span className="text-xs font-normal text-neutral-400">{total} 项</span>
        </h2>
        <button
          onClick={() => setCollapsed(true)}
          className="rounded-full p-1 text-neutral-400 transition hover:bg-black/5 hover:text-neutral-600"
          aria-label="收起"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
      {statsLine ? (
        <p className="px-5 pb-2 text-[11px] leading-snug text-neutral-400">
          {statsLine}
        </p>
      ) : null}

      <div className="flex-1 space-y-4 overflow-y-auto px-3 pb-3">
        {total === 0 ? (
          <p className="px-2 py-8 text-center text-sm text-neutral-400">
            全部处理完了，没有待审核项
          </p>
        ) : (
          KIND_ORDER.map((kind) => {
            const items = groups.get(kind) ?? [];
            if (items.length === 0) return null;
            return (
              <section key={kind}>
                <p className="mb-1.5 flex items-center gap-1.5 px-2 text-xs font-semibold text-neutral-600">
                  <span className={cn("h-2 w-2 rounded-full", KIND_META[kind].dot)} />
                  {KIND_META[kind].label} · {items.length}
                </p>
                <ul className="space-y-1">
                  {items.map((item, index) => (
                    // 稳定 key：用业务 id 而非 index——否则列表移位后
                    // DnaAssignActions 等子组件的 useState 会继承上一条目的
                    // 选中值（建议徽章与下拉框错位的根因）
                    <li
                      key={`${kind}-${
                        item.creative_id ??
                        item.derivation_id ??
                        item.asset_id ??
                        index
                      }`}
                    >
                      {item.kind === "low_confidence" && item.asset_id ? (
                        <Link
                          to={`/assets/${item.asset_id}`}
                          className="block rounded-xl px-3 py-2 transition hover:bg-black/[0.04]"
                        >
                          <span className="line-clamp-2 block break-all text-sm font-medium leading-snug text-neutral-900" title={item.title}>
                            {item.title}
                          </span>
                          <span className="mt-0.5 line-clamp-2 block text-xs leading-relaxed text-neutral-500">
                            {item.reason}
                          </span>
                        </Link>
                      ) : (
                        <div className="rounded-xl px-3 py-2 transition hover:bg-black/[0.04]">
                          <button
                            onClick={() =>
                              item.creative_id && onSelectCreative(`creative:${item.creative_id}`)
                            }
                            className="w-full text-left"
                          >
                            {(item.kind === "pending_verdict" ||
                              item.kind === "derivation_review") &&
                            item.source_label &&
                            item.target_label ? (
                              <>
                                <span className="block truncate text-[11px] text-neutral-400">
                                  {item.creative_name}
                                </span>
                                <span
                                  className="line-clamp-2 block break-all text-sm font-medium leading-snug text-neutral-900"
                                  title={item.title}
                                >
                                  {item.source_label}
                                  {item.factor ? (
                                    <span className="mx-1 rounded bg-violet-50 px-1 py-px text-[10px] font-medium text-violet-600">
                                      {FACTOR_LABEL[item.factor] ?? item.factor}
                                    </span>
                                  ) : null}
                                  {item.target_label}
                                </span>
                              </>
                            ) : (
                              <span className="line-clamp-2 block break-all text-sm font-medium leading-snug text-neutral-900" title={item.title}>
                                {item.title}
                              </span>
                            )}
                            <span className="mt-0.5 line-clamp-2 block text-xs leading-relaxed text-neutral-500">
                              {item.reason}
                            </span>
                          </button>
                          {item.kind === "archive_suggestion" ? (
                            <ArchiveActions item={item} />
                          ) : item.kind === "merge_candidate" ? (
                            <MergeActions item={item} />
                          ) : item.kind === "pending_verdict" ? (
                            <VerdictActions item={item} />
                          ) : item.kind === "derivation_review" ? (
                            <DerivationReviewActions item={item} />
                          ) : item.kind === "dna_unassigned" ? (
                            <DnaAssignActions item={item} />
                          ) : item.kind === "observation_pair" ? (
                            <ClosePairActions item={item} />
                          ) : null}
                        </div>
                      )}
                    </li>
                  ))}
                </ul>
              </section>
            );
          })
        )}
      </div>
    </div>
  );
}
