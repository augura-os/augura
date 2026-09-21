import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, Inbox, Plus, Sparkles, X } from "lucide-react";
import type { DerivationFactor, ReviewItem, ReviewKind } from "@shared";
import { useReviewQueue } from "../../hooks/useReviewQueue";
import { useDnas } from "../../hooks/useDnas";
import {
  assignCreativeDna,
  closeSimilarPair,
  confirmFamily,
  confirmRuleKeyword,
  createDna,
  deleteDerivation,
  dismissFamily,
  dismissRuleKeyword,
  linkSimilar,
  mergeCreatives,
  suggestFamilies,
  updateDerivation,
  updateLifecycle,
} from "../../services/api";
import { factorLabel } from "../../lib/short-name";
import { useT } from "../../lib/i18n";
import { cn } from "../../lib/utils";
import { InterventionDensityWidget } from "./InterventionDensityWidget";
import { HubSkewWidget } from "./HubSkewWidget";

const KIND_DOT: Record<ReviewKind, string> = {
  auto_brake: "bg-red-600",
  family_bootstrap: "bg-pink-500",
  rule_keyword: "bg-emerald-500",
  archive_suggestion: "bg-stone-500",
  pending_verdict: "bg-violet-500",
  derivation_review: "bg-orange-500",
  merge_candidate: "bg-amber-500",
  dna_unassigned: "bg-rose-400",
  observation_pair: "bg-sky-500",
  low_confidence: "bg-red-500",
  market_conflict: "bg-teal-500",
  threshold_calibration: "bg-cyan-500",
  market_detect: "bg-lime-500",
};

const KIND_ORDER: ReviewKind[] = [
  "auto_brake",
  "family_bootstrap",
  "rule_keyword",
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

/** judge_stats 的类别名（judge_suggestions.kind），人话标签走 inbox.judge.* 字典 */
const JUDGE_KINDS = new Set([
  "dna_assign",
  "merge_pair",
  "verdict",
  "derivation-factor",
  "observation_pair",
]);

/** 顶部小字统计：各类别"近期采纳率 x% · 自动改判率 y%"，无数据的类别不出声 */
function judgeStatsLine(
  data: { judge_stats?: Record<string, { auto_total: number; override_rate: number; acceptance_rate: number | null }> } | undefined,
  t: (key: string) => string,
): string {
  const stats = data?.judge_stats ?? {};
  const parts: string[] = [];
  for (const [kind, s] of Object.entries(stats)) {
    const label = JUDGE_KINDS.has(kind) ? t(`inbox.judge.${kind}`) : kind;
    if (s.acceptance_rate !== null && s.acceptance_rate !== undefined) {
      parts.push(
        t("inbox.stats.acceptance")
          .replace("{label}", label)
          .replace("{pct}", String(Math.round(s.acceptance_rate * 100))),
      );
    }
    if (s.auto_total > 0) {
      parts.push(
        t("inbox.stats.override")
          .replace("{label}", label)
          .replace("{pct}", String(Math.round(s.override_rate * 100))),
      );
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
  const t = useT();
  const queryClient = useQueryClient();
  const { armed, arm } = useConfirm();
  const [done, setDone] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
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
    // 乐观更新：点击即出"并入中"徽章（队列重算要几秒，等它再显示会让用户
    // 以为没点上）；任何失败都回滚徽章重新露出按钮——与 similarMutation 同款。
    onMutate: () => {
      setDone(t("inbox.merge.merging"));
      setErrorMessage(null);
    },
    onSuccess: () => {
      setDone(t("inbox.merge.merged"));
      setBlockMessage(null);
      invalidate();
    },
    onError: (error, body) => {
      setDone(null);
      const message = error instanceof Error ? error.message : "";
      // 守卫拦截的消息由后端返回且仍为中文，这里按原文匹配。
      // 后端消息翻译为英文时，此判断必须同步改为错误码——详见 PR 说明。
      if (message.includes("守卫拦截")) {
        setBlockMessage(message);
        setPendingBody(body);
      } else {
        // 超时/500/网络错误以前被静默吞掉（卡片原地不动像"卡住"）——
        // 必须露出错误让用户知道要重试。注意：超时场景后端可能实际已
        // 合并成功，队列轮询（15s）会把已处理的卡片自动撤下。
        setErrorMessage(message || t("common.failedRetry"));
      }
    },
  });
  const similarMutation = useMutation({
    mutationFn: () =>
      linkSimilar({
        source_creative_id: item.creative_id!,
        target_creative_id: item.related_creative_id!,
        reason: t("inbox.merge.keepSplitReason"),
      }),
    // 乐观更新：点击即出"已登记"徽章，后台刷新队列（队列重算约 3s，
    // 等它再显示会让用户以为没点上）；失败时回滚徽章重新露出按钮。
    onMutate: () => {
      setDone(t("inbox.merge.registered"));
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

  const left = item.creative_name ?? t("inbox.merge.left");
  const right = item.related_creative_name ?? t("inbox.merge.right");
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
            placeholder={t("inbox.merge.reasonRequired")}
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
            {t("inbox.merge.forceMerge")}
          </button>
        </span>
      </span>
    );
  }

  return (
    <span className="mt-1 block" onClick={(e) => e.stopPropagation()}>
      <span className="mb-1 block text-[10px] leading-snug text-neutral-400">
        {t("inbox.merge.hint")}
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
            ? t("inbox.merge.suggestMerge")
            : item.suggestion === "observe"
              ? t("inbox.merge.suggestObserve")
              : t("inbox.merge.suggestSplit")}
          {item.suggestion_votes ? ` ${item.suggestion_votes}/3` : ""}
        </span>
      ) : null}
      {([
        { key: "keep-left", label: t("inbox.merge.intoLeft"),
          title: t("inbox.merge.intoLeftTitle").replace("{right}", right).replace("{left}", left),
          body: { source_creative_id: item.related_creative_id, target_creative_id: item.creative_id } },
        { key: "keep-right", label: t("inbox.merge.intoRight"),
          title: t("inbox.merge.intoRightTitle").replace("{left}", left).replace("{right}", right),
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
          {armed === key + item.title ? t("inbox.merge.confirm") : label}
        </button>
      ))}
      <button
        title={t("inbox.merge.notTheSameTitle")}
        disabled={busy}
        onClick={() => similarMutation.mutate()}
        className="rounded-md bg-neutral-100 px-1.5 py-0.5 text-[11px] font-medium text-neutral-500 transition hover:bg-neutral-200"
      >
        {t("inbox.merge.notTheSame")}
      </button>
      </span>
      {errorMessage ? (
        <span className="mt-1 block text-[11px] text-red-500">
          {t("inbox.merge.failed")}：{errorMessage}
        </span>
      ) : null}
    </span>
  );
}

function VerdictActions({ item }: { item: ReviewItem }) {
  const t = useT();
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
        {t("inbox.verdict.done").replace(
          "{verdict}",
          done === "positive" ? t("inbox.verdict.donePositive") : t("inbox.verdict.doneNegative"),
        )}
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
            ? t("inbox.verdict.suggestPositive")
            : item.suggestion === "negative"
              ? t("inbox.verdict.suggestNegative")
              : t("inbox.verdict.suggestHold")}
          {item.suggestion_votes ? ` ${item.suggestion_votes}/3` : ""}
        </span>
      ) : null}
      <button
        title={t("inbox.verdict.titlePositive")}
        disabled={mutation.isPending}
        onClick={() => mutation.mutate("positive")}
        className="flex items-center gap-0.5 rounded-md bg-emerald-50 px-1.5 py-0.5 text-[11px] font-medium text-emerald-700 transition hover:bg-emerald-100 disabled:opacity-50"
      >
        <Check className="h-3 w-3" /> {mutation.isPending ? t("inbox.verdict.saving") : t("inbox.verdict.positive")}
      </button>
      <button
        title={t("inbox.verdict.titleNegative")}
        disabled={mutation.isPending}
        onClick={() => mutation.mutate("negative")}
        className="flex items-center gap-0.5 rounded-md bg-red-50 px-1.5 py-0.5 text-[11px] font-medium text-red-600 transition hover:bg-red-100 disabled:opacity-50"
      >
        <X className="h-3 w-3" /> {mutation.isPending ? t("inbox.verdict.saving") : t("inbox.verdict.negative")}
      </button>
      {mutation.isError ? (
        <span className="text-[11px] text-red-500">{t("common.failedRetry")}</span>
      ) : null}
    </span>
  );
}

function DnaAssignActions({ item }: { item: ReviewItem }) {
  const t = useT();
  const queryClient = useQueryClient();
  const { data: dnas } = useDnas();
  const [selected, setSelected] = useState(item.suggested_dna_id ?? "");
  const [done, setDone] = useState<string | null>(null);
  const mutation = useMutation({
    mutationFn: () => assignCreativeDna(item.creative_id!, selected),
    onSuccess: (dna) => {
      setDone(dna ? `${dna.code} ${dna.name}` : t("inbox.dna.assigned"));
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
          {t("inbox.dna.suggested").replace("{suggestion}", item.suggestion)}
          {item.suggestion_votes ? ` (${item.suggestion_votes}/3)` : ""}
        </span>
      ) : null}
      <select
        value={selected}
        onChange={(event) => setSelected(event.target.value)}
        className="h-6 max-w-[180px] rounded-md border border-black/10 bg-white px-1 text-[11px] text-neutral-700"
      >
        <option value="">{t("inbox.dna.selectFamily")}</option>
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
        {mutation.isPending ? t("inbox.dna.assigning") : t("inbox.dna.assign")}
      </button>
      {mutation.isError ? (
        <span className="text-[11px] text-red-500">{t("common.failedRetry")}</span>
      ) : null}
    </span>
  );
}

function DerivationReviewActions({ item }: { item: ReviewItem }) {
  const t = useT();
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
      setDone(t("inbox.derivation.unlinked"));
      invalidate();
    },
  });
  const adoptMutation = useMutation({
    mutationFn: () =>
      updateDerivation(item.derivation_id!, {
        factor: item.suggestion as DerivationFactor,
      }),
    onSuccess: () => {
      setDone(t("inbox.derivation.adopted"));
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
  const factorText = factorLabel(item.suggestion);
  return (
    <span className="mt-1 flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
      <span
        className={cn(
          "rounded px-1.5 py-0.5 text-[11px] font-medium",
          isUnlink ? "bg-red-50 text-red-600" : "bg-orange-50 text-orange-700",
        )}
        title={item.suggestion_reason ?? undefined}
      >
        {isUnlink
          ? t("inbox.derivation.suggestUnlink")
          : t("inbox.derivation.suggestChange").replace("{factor}", factorText)}
        {item.suggestion_votes ? ` ${item.suggestion_votes}/3` : ""}
      </span>
      {isUnlink ? (
        <button
          title={t("inbox.derivation.unlinkTitle")}
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
            ? t("inbox.derivation.confirmUnlinkQ")
            : unlinkMutation.isPending
              ? t("inbox.derivation.unlinking")
              : t("inbox.derivation.confirmUnlink")}
        </button>
      ) : (
        <button
          title={t("inbox.derivation.changeFactorTitle").replace("{factor}", factorText)}
          disabled={busy}
          onClick={() => adoptMutation.mutate()}
          className="rounded-md bg-orange-50 px-1.5 py-0.5 text-[11px] font-medium text-orange-700 transition hover:bg-orange-100 disabled:opacity-40"
        >
          {adoptMutation.isPending ? t("inbox.derivation.adopting") : t("inbox.derivation.adopt")}
        </button>
      )}
      {unlinkMutation.isError || adoptMutation.isError ? (
        <span className="text-[11px] text-red-500">{t("common.failedRetry")}</span>
      ) : null}
    </span>
  );
}

function ArchiveActions({ item }: { item: ReviewItem }) {
  const t = useT();
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
          state === "archived"
            ? t("inbox.archive.reasonArchived")
            : t("inbox.archive.reasonWatch"),
      }),
    onSuccess: (_data, state) => {
      setDone(state === "archived" ? t("inbox.archive.archived") : t("inbox.archive.keptWatch"));
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
        title={t("inbox.archive.title")}
        disabled={mutation.isPending}
        onClick={() => arm("archive" + item.creative_id, () => mutation.mutate("archived"))}
        className={cn(
          "rounded-md px-1.5 py-0.5 text-[11px] font-medium transition",
          armed === "archive" + item.creative_id
            ? "bg-stone-700 text-white"
            : "bg-stone-100 text-stone-600 hover:bg-stone-200",
        )}
      >
        {armed === "archive" + item.creative_id ? t("inbox.archive.confirmQ") : t("inbox.archive.confirm")}
      </button>
      <button
        title={t("inbox.archive.watchTitle")}
        disabled={mutation.isPending}
        onClick={() => mutation.mutate("watch")}
        className="rounded-md bg-sky-50 px-1.5 py-0.5 text-[11px] font-medium text-sky-600 transition hover:bg-sky-100 disabled:opacity-40"
      >
        {t("inbox.archive.keepWatch")}
      </button>
      {mutation.isError ? (
        <span className="text-[11px] text-red-500">{t("common.failedRetry")}</span>
      ) : null}
    </span>
  );
}

function ClosePairActions({ item }: { item: ReviewItem }) {
  const t = useT();
  const queryClient = useQueryClient();
  const { armed, arm } = useConfirm();
  const [done, setDone] = useState<string | null>(null);
  const mutation = useMutation({
    mutationFn: () =>
      closeSimilarPair({
        source_creative_id: item.creative_id!,
        target_creative_id: item.related_creative_id!,
        reason: item.suggestion_reason
          ? t("inbox.close.adoptReason")
              .replace("{votes}", String(item.suggestion_votes ?? "-"))
              .replace("{reason}", item.suggestion_reason)
          : t("inbox.close.closedReason"),
      }),
    onSuccess: () => {
      setDone(t("inbox.close.closed"));
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
        title={t("inbox.close.title")}
        disabled={mutation.isPending}
        onClick={() => arm("close" + item.title, () => mutation.mutate())}
        className={cn(
          "rounded-md px-1.5 py-0.5 text-[11px] font-medium transition",
          armed === "close" + item.title
            ? "bg-sky-500 text-white"
            : "bg-sky-50 text-sky-600 hover:bg-sky-100",
        )}
      >
        {armed === "close" + item.title ? t("inbox.close.confirmQ") : t("inbox.close.close")}
      </button>
      {mutation.isError ? (
        <span className="text-[11px] text-red-500">{t("common.failedRetry")}</span>
      ) : null}
    </span>
  );
}

/** 规则词建议：确认加入词表 / 跳过（词表落 settings，下一轮判定生效） */
function RuleKeywordActions({ item }: { item: ReviewItem }) {
  const t = useT();
  const queryClient = useQueryClient();
  const [done, setDone] = useState<string | null>(null);
  const proposal = item.rule_keyword;
  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["review-queue"] });
  };
  const confirmMutation = useMutation({
    mutationFn: () => confirmRuleKeyword(proposal!.suggestion_id),
    onSuccess: () => {
      setDone(t("inbox.rule.confirmed"));
      invalidate();
    },
  });
  const skipMutation = useMutation({
    mutationFn: () => dismissRuleKeyword(proposal!.suggestion_id),
    onSuccess: () => {
      setDone(t("inbox.rule.skipped"));
      invalidate();
    },
  });
  if (!proposal) return null;
  if (done) {
    return (
      <span className="mt-1 inline-flex items-center gap-1 rounded-md bg-neutral-100 px-1.5 py-0.5 text-[11px] font-medium text-neutral-500">
        <Check className="h-3 w-3" /> {done}
      </span>
    );
  }
  const pending = confirmMutation.isPending || skipMutation.isPending;
  return (
    <span className="mt-1 flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
      <span className="rounded bg-emerald-50 px-1 py-px text-[10px] font-medium text-emerald-600">
        {t(`inbox.rule.target.${proposal.target}`)}
      </span>
      <button
        disabled={pending}
        onClick={() => confirmMutation.mutate()}
        className="rounded-md bg-emerald-500 px-1.5 py-0.5 text-[11px] font-medium text-white transition hover:bg-emerald-600 disabled:opacity-40"
      >
        {t("inbox.rule.confirm")}
      </button>
      <button
        disabled={pending}
        onClick={() => skipMutation.mutate()}
        className="rounded-md bg-neutral-100 px-1.5 py-0.5 text-[11px] font-medium text-neutral-500 transition hover:bg-neutral-200 disabled:opacity-40"
      >
        {t("inbox.rule.skip")}
      </button>
      {confirmMutation.isError || skipMutation.isError ? (
        <span className="text-[11px] text-red-500">{t("common.failedRetry")}</span>
      ) : null}
    </span>
  );
}

function FamilyBootstrapActions({ item }: { item: ReviewItem }) {
  const t = useT();
  const queryClient = useQueryClient();
  const family = item.family;
  const [name, setName] = useState(family?.name ?? "");
  const [done, setDone] = useState<string | null>(null);
  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["review-queue"] });
    void queryClient.invalidateQueries({ queryKey: ["dnas"] });
    void queryClient.invalidateQueries({ queryKey: ["graph"] });
  };
  const confirmMutation = useMutation({
    mutationFn: () =>
      family!.existing_dna_code
        ? confirmFamily({
            suggestion_id: family!.suggestion_id,
            name: "",
            member_ids: family!.members.map((member) => member.id),
            existing_dna_code: family!.existing_dna_code,
          })
        : confirmFamily({
            suggestion_id: family!.suggestion_id,
            name: name.trim(),
            core_mechanic: family!.core_mechanic,
            hook_prototype: family!.hook_prototype,
            narrative_structure: family!.narrative_structure,
            keywords: family!.keywords,
            member_ids: family!.members.map((member) => member.id),
          }),
    onSuccess: (dna) => {
      setDone(
        t("inbox.family.created").replace("{label}", `${dna.code} ${dna.name}`),
      );
      invalidate();
    },
  });
  const skipMutation = useMutation({
    mutationFn: () => dismissFamily(family!.suggestion_id),
    onSuccess: () => {
      setDone(t("inbox.family.skipped"));
      invalidate();
    },
  });
  if (!family) return null;
  if (done) {
    return (
      <span className="mt-1 inline-flex items-center gap-1 rounded-md bg-neutral-100 px-1.5 py-0.5 text-[11px] font-medium text-neutral-500">
        <Check className="h-3 w-3" /> {done}
      </span>
    );
  }
  const busy = confirmMutation.isPending || skipMutation.isPending;
  const isAttach = Boolean(family.existing_dna_code);
  const preview: Array<[string, string]> = [
    [t("inbox.dna.coreMechanicPlaceholder"), family.core_mechanic],
    [t("inbox.dna.hookPrototypePlaceholder"), family.hook_prototype],
    [t("inbox.dna.narrativeStructurePlaceholder"), family.narrative_structure],
  ];
  return (
    <span className="mt-1 block" onClick={(e) => e.stopPropagation()}>
      {isAttach ? (
        <span className="block rounded-md bg-pink-50 px-1.5 py-0.5 text-[11px] font-medium text-pink-600">
          {t("inbox.family.attachTo").replace("{label}", family.name)}
        </span>
      ) : (
        <input
          className="h-6 w-full rounded-md border border-black/10 bg-white px-1.5 text-[11px] font-medium text-neutral-800"
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
      )}
      <span className="mt-1 block text-[10px] leading-snug text-neutral-400">
        {preview
          .filter(([, value]) => value)
          .map(([label, value]) => `${label}：${value}`)
          .join(" · ")}
      </span>
      {family.keywords.length > 0 ? (
        <span className="mt-1 flex flex-wrap gap-1">
          {family.keywords.map((keyword) => (
            <span
              key={keyword}
              className="rounded bg-neutral-100 px-1.5 py-px text-[10px] text-neutral-500"
            >
              {keyword}
            </span>
          ))}
        </span>
      ) : null}
      <span className="mt-0.5 line-clamp-2 block text-[10px] leading-snug text-neutral-400">
        {t("inbox.family.members").replace("{n}", String(family.members.length))}
        {"："}
        {family.members.map((member) => member.name).join("、")}
      </span>
      <span className="mt-1 flex items-center gap-1">
        <button
          disabled={(!isAttach && !name.trim()) || busy}
          onClick={() => confirmMutation.mutate()}
          className="rounded-md bg-rose-50 px-1.5 py-0.5 text-[11px] font-medium text-rose-600 transition hover:bg-rose-100 disabled:opacity-40"
        >
          {confirmMutation.isPending
            ? isAttach
              ? t("inbox.family.attaching")
              : t("inbox.family.confirming")
            : isAttach
              ? t("inbox.family.attach")
              : t("inbox.family.confirm")}
        </button>
        <button
          disabled={busy}
          onClick={() => skipMutation.mutate()}
          className="rounded-md bg-neutral-100 px-1.5 py-0.5 text-[11px] font-medium text-neutral-500 transition hover:bg-neutral-200 disabled:opacity-40"
        >
          {t("inbox.family.skip")}
        </button>
        {confirmMutation.isError || skipMutation.isError ? (
          <span className="text-[11px] text-red-500">{t("common.failedRetry")}</span>
        ) : null}
      </span>
    </span>
  );
}

function NewFamilyForm({ onClose }: { onClose: () => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [coreMechanic, setCoreMechanic] = useState("");
  const [hookPrototype, setHookPrototype] = useState("");
  const [narrativeStructure, setNarrativeStructure] = useState("");
  const [done, setDone] = useState<string | null>(null);
  const mutation = useMutation({
    mutationFn: () =>
      createDna({
        name: name.trim(),
        core_mechanic: coreMechanic.trim(),
        hook_prototype: hookPrototype.trim(),
        narrative_structure: narrativeStructure.trim(),
      }),
    onSuccess: (dna) => {
      setDone(`${dna.code} ${dna.name}`);
      void queryClient.invalidateQueries({ queryKey: ["dnas"] });
      void queryClient.invalidateQueries({ queryKey: ["review-queue"] });
      void queryClient.invalidateQueries({ queryKey: ["graph"] });
    },
  });
  if (done) {
    return (
      <span className="mx-5 mb-2 inline-flex items-center gap-1 rounded-md bg-rose-50 px-1.5 py-0.5 text-[11px] font-medium text-rose-600">
        <Check className="h-3 w-3" /> {t("inbox.dna.created").replace("{label}", done)}
      </span>
    );
  }
  const inputClass =
    "h-7 min-w-0 flex-1 rounded-md border border-black/10 bg-white px-2 text-[11px] text-neutral-700";
  return (
    <div className="mx-5 mb-2 space-y-1.5 rounded-xl bg-black/[0.03] p-2">
      <div className="flex items-center gap-1">
        <input
          className={inputClass}
          placeholder={t("inbox.dna.namePlaceholder")}
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
        <input
          className={inputClass}
          placeholder={t("inbox.dna.coreMechanicPlaceholder")}
          value={coreMechanic}
          onChange={(event) => setCoreMechanic(event.target.value)}
        />
      </div>
      <div className="flex items-center gap-1">
        <input
          className={inputClass}
          placeholder={t("inbox.dna.hookPrototypePlaceholder")}
          value={hookPrototype}
          onChange={(event) => setHookPrototype(event.target.value)}
        />
        <input
          className={inputClass}
          placeholder={t("inbox.dna.narrativeStructurePlaceholder")}
          value={narrativeStructure}
          onChange={(event) => setNarrativeStructure(event.target.value)}
        />
      </div>
      <div className="flex items-center justify-end gap-1">
        <button
          onClick={onClose}
          className="rounded-md bg-neutral-100 px-1.5 py-0.5 text-[11px] font-medium text-neutral-500 transition hover:bg-neutral-200"
        >
          {t("inbox.dna.cancel")}
        </button>
        <button
          disabled={!name.trim() || mutation.isPending}
          onClick={() => mutation.mutate()}
          className="rounded-md bg-rose-50 px-1.5 py-0.5 text-[11px] font-medium text-rose-600 transition hover:bg-rose-100 disabled:opacity-40"
        >
          {mutation.isPending ? t("inbox.dna.creating") : t("inbox.dna.create")}
        </button>
      </div>
      {mutation.isError ? (
        <span className="block text-right text-[11px] text-red-500">
          {t("common.failedRetry")}
        </span>
      ) : null}
    </div>
  );
}

export function ReviewInboxPanel({
  onSelectCreative,
}: {
  onSelectCreative: (creativeNodeId: string) => void;
}) {
  const t = useT();
  const { data } = useReviewQueue();
  const [collapsed, setCollapsed] = useState(true);
  const [showNewFamily, setShowNewFamily] = useState(false);
  const queryClient = useQueryClient();
  const [bootstrapHint, setBootstrapHint] = useState<string | null>(null);
  const bootstrapMutation = useMutation({
    mutationFn: suggestFamilies,
    onSuccess: (count) => {
      setBootstrapHint(
        count > 0
          ? t("inbox.family.suggested").replace("{n}", String(count))
          : t("inbox.family.none"),
      );
      if (count > 0) {
        void queryClient.invalidateQueries({ queryKey: ["review-queue"] });
      }
    },
    onError: () => setBootstrapHint(t("common.failedRetry")),
  });

  const groups = useMemo(() => {
    const map = new Map<ReviewKind, ReviewItem[]>();
    map.set("auto_brake", data?.auto_brakes ?? []);
    map.set("family_bootstrap", data?.family_bootstraps ?? []);
    map.set("rule_keyword", data?.rule_keywords ?? []);
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
  const statsLine = judgeStatsLine(data, t);
  const unassignedCount = groups.get("dna_unassigned")?.length ?? 0;
  const familyHint = data.family_bootstrap_hint;

  if (collapsed) {
    return (
      <button
        onClick={() => setCollapsed(false)}
        className="pointer-events-auto absolute right-4 top-4 flex items-center gap-2 rounded-full bg-white/85 px-4 py-2 shadow-lg ring-1 ring-black/5 backdrop-blur-xl transition hover:bg-white"
      >
        <Inbox className="h-4 w-4 text-sky-500" />
        <span className="text-sm font-medium text-neutral-800">{t("inbox.review")}</span>
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
          {t("inbox.title")}
          <span className="text-xs font-normal text-neutral-400">
            {t("inbox.items").replace("{n}", String(total))}
          </span>
        </h2>
        <div className="flex items-center gap-1">
          {unassignedCount > 0 ? (
            <button
              onClick={() => bootstrapMutation.mutate()}
              disabled={bootstrapMutation.isPending}
              title={t("inbox.family.suggest")}
              className="flex items-center gap-0.5 rounded-full px-2 py-1 text-[11px] font-medium text-pink-500 transition hover:bg-black/5 disabled:opacity-40"
            >
              <Sparkles className="h-3.5 w-3.5" />
              {bootstrapMutation.isPending
                ? t("inbox.family.suggesting")
                : t("inbox.family.suggest")}
            </button>
          ) : null}
          <button
            onClick={() => setShowNewFamily((open) => !open)}
            title={t("inbox.dna.newFamily")}
            className="flex items-center gap-0.5 rounded-full px-2 py-1 text-[11px] font-medium text-neutral-500 transition hover:bg-black/5 hover:text-neutral-700"
          >
            <Plus className="h-3.5 w-3.5" />
            {t("inbox.dna.newFamily")}
          </button>
          <button
            onClick={() => setCollapsed(true)}
            className="rounded-full p-1 text-neutral-400 transition hover:bg-black/5 hover:text-neutral-600"
            aria-label={t("common.collapse")}
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      </div>
      {showNewFamily ? (
        <NewFamilyForm onClose={() => setShowNewFamily(false)} />
      ) : null}
      {familyHint.suggest ? (
        <div className="mx-5 mb-2 flex items-center justify-between gap-2 rounded-xl bg-pink-50 px-3 py-2">
          <p className="text-[11px] leading-snug text-pink-700">
            {t("inbox.family.hint").replace("{n}", String(familyHint.unassigned))}
          </p>
          <button
            onClick={() => bootstrapMutation.mutate()}
            disabled={bootstrapMutation.isPending}
            className="flex shrink-0 items-center gap-0.5 rounded-md bg-pink-500 px-2 py-1 text-[11px] font-medium text-white transition hover:bg-pink-600 disabled:opacity-40"
          >
            <Sparkles className="h-3.5 w-3.5" />
            {bootstrapMutation.isPending
              ? t("inbox.family.suggesting")
              : t("inbox.family.suggest")}
          </button>
        </div>
      ) : null}
      {bootstrapMutation.isPending ? (
        <p className="px-5 pb-2 text-[11px] leading-snug text-neutral-400">
          {t("inbox.family.pendingHint")}
        </p>
      ) : null}
      {bootstrapHint ? (
        <p className="px-5 pb-2 text-[11px] leading-snug text-neutral-400">
          {bootstrapHint}
        </p>
      ) : null}
      {statsLine ? (
        <p className="px-5 pb-2 text-[11px] leading-snug text-neutral-400">
          {statsLine}
        </p>
      ) : null}
      <InterventionDensityWidget density={data.intervention_density} />
      <HubSkewWidget skew={data.hub_skew} />

      <div className="flex-1 space-y-4 overflow-y-auto px-3 pb-3">
        {total === 0 ? (
          <p className="px-2 py-8 text-center text-sm text-neutral-400">
            {t("inbox.empty")}
          </p>
        ) : (
          KIND_ORDER.map((kind) => {
            const items = groups.get(kind) ?? [];
            if (items.length === 0) return null;
            return (
              <section key={kind}>
                <p className="mb-1.5 flex items-center gap-1.5 px-2 text-xs font-semibold text-neutral-600">
                  <span className={cn("h-2 w-2 rounded-full", KIND_DOT[kind])} />
                  {t(`inbox.kind.${kind}`)} · {items.length}
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
                        item.family?.suggestion_id ??
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
                                      {factorLabel(item.factor)}
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
                          {item.kind === "family_bootstrap" ? (
                            <FamilyBootstrapActions item={item} />
                          ) : item.kind === "rule_keyword" ? (
                            <RuleKeywordActions item={item} />
                          ) : item.kind === "archive_suggestion" ? (
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
