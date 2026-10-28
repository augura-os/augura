import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ChevronLeft, ChevronRight, Inbox, Plus, Sparkles, X } from "lucide-react";
import type { DerivationFactor, ReviewItem, ReviewKind } from "@shared";
import { useReviewQueue } from "../../hooks/useReviewQueue";
import { useDnas } from "../../hooks/useDnas";
import {
  assignCreativeDna,
  batchAssignDna,
  closeSimilarPair,
  confirmFamiliesBatch,
  confirmFamily,
  confirmRuleKeyword,
  createDna,
  deleteDerivation,
  dismissFamily,
  dismissRuleKeyword,
  fetchObservations,
  linkSimilar,
  linkSimilarBulk,
  mergeCreatives,
  suggestFamilies,
  updateDerivation,
  updateLifecycle,
  type BatchResponse,
} from "../../services/api";
import { factorLabel } from "../../lib/short-name";
import { apiErrorText } from "../../lib/apiErrorText";
import { apiErrorCode } from "../../services/client";
import { useT } from "../../lib/i18n";
import { cn } from "../../lib/utils";
import { inboxReasonLine, inboxTitleLine } from "./inboxReason";
import {
  keyByItemId,
  reduceBatchResults,
  selectionKey,
  type BatchFailure,
} from "./inboxBatch";
import {
  clusterMergePairs,
  keyByPairId,
  mergeKeyAction,
  moveFocusIndex,
  paginateClusters,
} from "./inboxMerge";
import { MergePairPreview } from "./MergePairPreview";
import { InterventionDensityWidget } from "./InterventionDensityWidget";
import { HubSkewWidget } from "./HubSkewWidget";

/** 契约 §3 + P0b：这三类条目支持多选 + 批量操作 */
const BATCHABLE_KINDS: ReadonlySet<ReviewKind> = new Set([
  "family_bootstrap",
  "dna_unassigned",
  "merge_candidate",
]);

/** 合并候选客户端分页页大小（对） */
const MERGE_PAGE_SIZE = 20;
const OBSERVATION_PAGE_SIZE = 50;
const OBSERVATION_WAITING_REASONS: ReadonlySet<string> = new Set([
  "awaiting_evaluation",
  "insufficient_evidence",
  "incomparable_objective",
  "incomparable_window",
  "incomparable_market",
  "no_recent_data",
  "content_evidence_incomplete",
  "auto_policy_uncertified",
  "unstable_evidence",
  "auto_close_disabled",
  "manual_reopen",
]);
const OBSERVATION_REASON_GROUPS: Record<string, string> = {
  registration_missing: "insufficient_evidence",
  evidence_start_missing: "insufficient_evidence",
  no_performance_data: "no_recent_data",
  no_post_registration_data: "no_recent_data",
  invalid_performance_data: "insufficient_evidence",
  zero_results: "insufficient_evidence",
  insufficient_spend: "insufficient_evidence",
  insufficient_results: "insufficient_evidence",
  insufficient_chronological_halves: "insufficient_evidence",
  insufficient_half_data: "insufficient_evidence",
  effect_below_minimum: "insufficient_evidence",
  msprt_not_crossed: "insufficient_evidence",
  unknown_objective: "incomparable_objective",
  mixed_objectives: "incomparable_objective",
  objective_mismatch: "incomparable_objective",
  unsupported_objective: "incomparable_objective",
  market_unknown: "incomparable_market",
  mixed_markets: "incomparable_market",
  market_mismatch: "incomparable_market",
  unstable_halves: "unstable_evidence",
  uncertified_policy: "auto_policy_uncertified",
};

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
  distribution_hint: "bg-indigo-500",
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
  "distribution_hint",
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

/**
 * Two-step confirm: first click arms the button for 3s, second executes.
 * 键盘流（P0b 加固）走 armOnly + confirm：arm 与 confirm 不由同一个键完成，
 * 键盘上 ←/→/x 只预备，必须再按 Enter 才执行——同一键的重复触发
 * （key repeat / 快速连按）无法走完 arm+confirm 两步。
 */
function useConfirm() {
  const [armed, setArmed] = useState<string | null>(null);
  const pendingRef = useRef<{ key: string; execute: () => void } | null>(null);
  const disarmIf = (key: string) => {
    setArmed((current) => (current === key ? null : current));
    if (pendingRef.current?.key === key) pendingRef.current = null;
  };
  const arm = (key: string, execute: () => void) => {
    if (armed === key) {
      setArmed(null);
      pendingRef.current = null;
      execute();
      return;
    }
    pendingRef.current = { key, execute };
    setArmed(key);
    setTimeout(() => disarmIf(key), 3000);
  };
  /** 只预备不执行：已 armed 同键时再按是 no-op（保持原 3s 倒计时） */
  const armOnly = (key: string, execute: () => void) => {
    if (armed === key) return;
    pendingRef.current = { key, execute };
    setArmed(key);
    setTimeout(() => disarmIf(key), 3000);
  };
  /** 执行当前 armed 动作（键盘 Enter）；无 armed 时 no-op */
  const confirm = () => {
    const pending = pendingRef.current;
    setArmed(null);
    pendingRef.current = null;
    pending?.execute();
  };
  const cancel = () => {
    setArmed(null);
    pendingRef.current = null;
  };
  return { armed, arm, armOnly, confirm, cancel };
}

/** 键盘流（P0b 加固）通过 ref 触发卡片动作：←/→/x 只 arm（预备），
 *  Enter 确认执行 armed 动作，Esc 取消 armed */
export interface MergeActionsHandle {
  armIntoLeft: () => void;
  armIntoRight: () => void;
  armNotSame: () => void;
  confirmArmed: () => void;
  cancelArmed: () => void;
}

const MergeActions = forwardRef<MergeActionsHandle, { item: ReviewItem }>(
function MergeActions({ item }, ref) {
  const t = useT();
  const queryClient = useQueryClient();
  const { armed, arm, armOnly, confirm, cancel } = useConfirm();
  const [done, setDone] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [blockMessage, setBlockMessage] = useState<string | null>(null);
  const [forceReason, setForceReason] = useState("");
  // 单侧预览（仅当素材只有一侧有预览图时兜底）；正常入口是双视频并排预览
  const [preview, setPreview] = useState<{ id: string; name: string } | null>(null);
  const [previewImageFallback, setPreviewImageFallback] = useState(false);
  const [showDualPreview, setShowDualPreview] = useState(false);
  const [pendingBody, setPendingBody] = useState<{
    source_creative_id: string;
    target_creative_id: string;
  } | null>(null);
  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["review-queue"] });
    void queryClient.invalidateQueries({ queryKey: ["review-observations"] });
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
      // 守卫拦截按结构化错误码识别（envelope code），替代中文子串匹配
      if (apiErrorCode(error) === "merge_guard_blocked") {
        setBlockMessage(apiErrorText(error, t));
        setPendingBody(body);
      } else {
        // 超时/500/网络错误以前被静默吞掉（卡片原地不动像"卡住"）——
        // 必须露出错误让用户知道要重试。注意：超时场景后端可能实际已
        // 合并成功，队列轮询（15s）会把已处理的卡片自动撤下。
        setErrorMessage(apiErrorText(error, t) || t("common.failedRetry"));
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
  // 键盘流（P0b 加固）：←/→/x 只 arm（预备，3s 超时取消），Enter 才执行
  // armed 动作——arm 与 confirm 不同键，按住方向键的 key repeat 或快速连按
  // 无法误触发合并。done/busy 时 no-op。
  useImperativeHandle(ref, () => ({
    armIntoLeft: () => {
      if (done || !item.creative_id || !item.related_creative_id) return;
      armOnly("keep-left" + item.title, () =>
        mergeMutation.mutate({
          source_creative_id: item.related_creative_id!,
          target_creative_id: item.creative_id!,
        }),
      );
    },
    armIntoRight: () => {
      if (done || !item.creative_id || !item.related_creative_id) return;
      armOnly("keep-right" + item.title, () =>
        mergeMutation.mutate({
          source_creative_id: item.creative_id!,
          target_creative_id: item.related_creative_id!,
        }),
      );
    },
    armNotSame: () => {
      if (done || !item.creative_id || !item.related_creative_id) return;
      armOnly("not-same" + item.title, () => similarMutation.mutate());
    },
    confirmArmed: confirm,
    cancelArmed: cancel,
  }));
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
  // 文本分徽章（reason_params.score，保留两位）；LLM/扫描器通道无分不显示
  const scoreRaw = item.reason_params?.score;
  const scoreNumber =
    typeof scoreRaw === "number" ? scoreRaw : Number(scoreRaw);
  const scoreText =
    scoreRaw !== null && scoreRaw !== undefined && Number.isFinite(scoreNumber)
      ? scoreNumber.toFixed(2)
      : null;

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
      {item.preview_asset_id || item.related_preview_asset_id ? (
        // 裁决前先看到素材内容：左右缩略图与"并入左/右"方位一致；
        // 两侧都有预览时点击任一缩略图打开双视频并排预览，否则回退单侧弹层
        <span className="mb-1 flex gap-1">
          {[
            { id: item.preview_asset_id, name: left },
            { id: item.related_preview_asset_id, name: right },
          ].map((side) =>
            side.id ? (
              <button
                key={side.id}
                title={
                  item.preview_asset_id && item.related_preview_asset_id
                    ? t("review.merge.previewBoth")
                    : t("inbox.merge.previewTitle").replace("{name}", side.name)
                }
                onClick={() => {
                  if (item.preview_asset_id && item.related_preview_asset_id) {
                    setShowDualPreview(true);
                  } else {
                    setPreviewImageFallback(false);
                    setPreview({ id: side.id!, name: side.name });
                  }
                }}
                className="h-16 w-28 shrink-0 overflow-hidden rounded-md border border-neutral-200 bg-neutral-100 transition hover:border-neutral-400"
              >
                <img
                  src={`/assets/${side.id}/media?variant=thumb`}
                  alt={side.name}
                  loading="lazy"
                  className="h-full w-full object-cover"
                />
              </button>
            ) : null,
          )}
        </span>
      ) : null}
      <span className="flex flex-wrap items-center gap-1">
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
        >
          {item.suggestion === "merge"
            ? t("inbox.merge.suggestMerge")
            : item.suggestion === "observe"
              ? t("inbox.merge.suggestObserve")
              : t("inbox.merge.suggestSplit")}
          {item.suggestion_votes ? ` ${item.suggestion_votes}/3` : ""}
        </span>
      ) : null}
      {scoreText ? (
        <span className="rounded bg-amber-50 px-1.5 py-0.5 text-[11px] font-medium text-amber-700">
          {t("review.merge.score").replace("{score}", scoreText)}
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
              ? "bg-emerald-500 text-white ring-2 ring-emerald-300"
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
        className={cn(
          "rounded-md px-1.5 py-0.5 text-[11px] font-medium transition",
          // 键盘 x 预备态：描边 + 文字变化提示"已预备，按 Enter 执行"；
          // 鼠标点击仍为单击直接登记（行为不变）
          armed === "not-same" + item.title
            ? "bg-neutral-700 text-white ring-2 ring-neutral-400"
            : "bg-neutral-100 text-neutral-500 hover:bg-neutral-200",
        )}
      >
        {armed === "not-same" + item.title
          ? t("inbox.merge.confirmNotSame")
          : t("inbox.merge.notTheSame")}
      </button>
      </span>
      {item.suggestion_reason ? (
        // 证据内联（P0b）：LLM 理由直接显示，不再藏 tooltip
        <span className="mt-0.5 block text-[10px] leading-snug text-neutral-400">
          {item.suggestion_reason}
        </span>
      ) : null}
      {errorMessage ? (
        <span className="mt-1 block text-[11px] text-red-500">
          {t("inbox.merge.failed")}：{errorMessage}
        </span>
      ) : null}
      {showDualPreview && item.preview_asset_id && item.related_preview_asset_id ? (
        <MergePairPreview
          left={{ id: item.preview_asset_id, name: left }}
          right={{ id: item.related_preview_asset_id, name: right }}
          onClose={() => setShowDualPreview(false)}
        />
      ) : null}
      {preview
        ? // 预览弹层：视频默认 <video> 播放，图片素材加载失败时回退 <img>。
          // 必须 portal 到 body：收件箱面板带 backdrop-blur（会形成 fixed
          // 包含块）且 overflow-hidden，直接渲染 fixed 弹层会被裁剪在
          // 400px 面板内而不是覆盖全屏。
          createPortal(
            <span
              className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
              onClick={() => setPreview(null)}
            >
              <span
                className="max-h-[85vh] w-full max-w-2xl overflow-hidden rounded-xl bg-black"
                onClick={(e) => e.stopPropagation()}
              >
                <span className="flex items-center justify-between gap-2 bg-neutral-900 px-3 py-1.5">
                  <span className="truncate text-xs text-neutral-300">{preview.name}</span>
                  <button
                    title={t("inbox.merge.closePreview")}
                    onClick={() => setPreview(null)}
                    className="rounded p-0.5 text-neutral-400 transition hover:bg-neutral-800 hover:text-white"
                  >
                    <X className="h-4 w-4" />
                  </button>
                </span>
                {previewImageFallback ? (
                  <img
                    src={`/assets/${preview.id}/media`}
                    alt={preview.name}
                    className="max-h-[75vh] w-full object-contain"
                  />
                ) : (
                  <video
                    controls
                    autoPlay
                    className="max-h-[75vh] w-full"
                    src={`/assets/${preview.id}/media`}
                    onError={() => setPreviewImageFallback(true)}
                  />
                )}
              </span>
            </span>,
            document.body,
          )
        : null}
    </span>
  );
});

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
      void queryClient.invalidateQueries({ queryKey: ["review-observations"] });
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

function BackgroundObservationCard({
  item,
  onSelectCreative,
}: {
  item: ReviewItem;
  onSelectCreative: (creativeNodeId: string) => void;
}) {
  const t = useT();
  const waitingReason = item.observation_waiting_reason;
  const waitingKey = waitingReason
    ? OBSERVATION_REASON_GROUPS[waitingReason] ?? waitingReason
    : null;
  const waitingText =
    waitingKey && OBSERVATION_WAITING_REASONS.has(waitingKey)
      ? t(`inbox.observation.waiting.${waitingKey}`)
      : waitingReason || t("inbox.observation.waiting.fallback");
  const nextEvaluationDate = item.next_evaluation_at
    ? new Date(item.next_evaluation_at)
    : null;
  const nextEvaluation = nextEvaluationDate
    ? Number.isNaN(nextEvaluationDate.getTime())
      ? item.next_evaluation_at
      : nextEvaluationDate.toLocaleString()
    : null;
  return (
    <li className="rounded-xl bg-sky-50/40 px-3 py-2 transition hover:bg-sky-50/70">
      <button
        onClick={() =>
          item.creative_id && onSelectCreative(`creative:${item.creative_id}`)
        }
        className="w-full text-left"
      >
        <span
          className="line-clamp-2 block break-all text-sm font-medium leading-snug text-neutral-900"
          title={item.title}
        >
          {inboxTitleLine(item, t)}
        </span>
      </button>
      <span className="mt-0.5 block text-xs leading-relaxed text-neutral-500">
        {waitingText}
      </span>
      {nextEvaluation ? (
        <span className="mt-0.5 block text-[11px] leading-relaxed text-neutral-400">
          {t("inbox.observation.nextEvaluation").replace("{time}", nextEvaluation)}
        </span>
      ) : null}
      <ClosePairActions item={item} />
    </li>
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
  const { data: dnas } = useDnas();
  const [collapsed, setCollapsed] = useState(true);
  const [showNewFamily, setShowNewFamily] = useState(false);
  const [showObservations, setShowObservations] = useState(false);
  const [observationPage, setObservationPage] = useState(0);
  const queryClient = useQueryClient();
  const observingPairsTotal = data?.observing_pairs_total ?? 0;
  const observationsQuery = useQuery({
    queryKey: ["review-observations", observationPage],
    queryFn: () =>
      fetchObservations(
        OBSERVATION_PAGE_SIZE,
        observationPage * OBSERVATION_PAGE_SIZE,
      ),
    enabled: showObservations && !collapsed && observingPairsTotal > 0,
  });
  const observationTotal = observationsQuery.data?.total ?? observingPairsTotal;
  const observationPageCount = Math.max(
    1,
    Math.ceil(observationTotal / OBSERVATION_PAGE_SIZE),
  );
  const observationPageIndex = Math.min(observationPage, observationPageCount - 1);
  useEffect(() => {
    if (observationPage !== observationPageIndex) {
      setObservationPage(observationPageIndex);
    }
  }, [observationPage, observationPageIndex]);
  const [bootstrapHint, setBootstrapHint] = useState<string | null>(null);
  // --- 批量操作状态（契约 §2.5） ---------------------------------------------
  // selectedKeys：四元组序列化 key 集合；removedKeys：批量 success/skipped 后
  // 增量移除（队列 refetch 真正消失前先从渲染里摘掉的 key）；failedByKey：
  // 批量 failed 项的标红原因。三者都在队列 refetch 后与存活条目对账。
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(new Set());
  const [removedKeys, setRemovedKeys] = useState<Set<string>>(new Set());
  const [failedByKey, setFailedByKey] = useState<Map<string, BatchFailure>>(new Map());
  const [familyPreview, setFamilyPreview] = useState<ReviewItem[] | null>(null);
  const [assignDnaId, setAssignDnaId] = useState("");
  const [batchHint, setBatchHint] = useState<string | null>(null);
  // P0b：批量否决确认 modal 的待提交对、合并候选分页页码、键盘焦点对的选中 key
  const [rejectPreview, setRejectPreview] = useState<ReviewItem[] | null>(null);
  const [mergePage, setMergePage] = useState(0);
  const [focusKey, setFocusKey] = useState<string | null>(null);
  const mergeHandles = useRef(new Map<string, MergeActionsHandle>());
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
    map.set("distribution_hint", data?.distribution_hints ?? []);
    return map;
  }, [data]);

  /** 三类可批量条目的并集（对账与 id→key 映射的数据源） */
  const batchItems = useMemo(
    () => [
      ...(groups.get("family_bootstrap") ?? []),
      ...(groups.get("dna_unassigned") ?? []),
      ...(groups.get("merge_candidate") ?? []),
    ],
    [groups],
  );

  // 契约 §2.5 集合对账：队列 refetch（15s 轮询 / 操作后 invalidate）后，
  // 已消失的 key 自动丢弃——防"选中 A 提交 B"，也清掉已落地的增量移除/标红。
  useEffect(() => {
    const alive = new Set(batchItems.map((item) => selectionKey(item.kind, item)));
    setSelectedKeys((prev) => {
      const next = new Set([...prev].filter((key) => alive.has(key)));
      return next.size === prev.size ? prev : next;
    });
    setRemovedKeys((prev) => {
      const next = new Set([...prev].filter((key) => alive.has(key)));
      return next.size === prev.size ? prev : next;
    });
    setFailedByKey((prev) => {
      const next = new Map([...prev].filter(([key]) => alive.has(key)));
      return next.size === prev.size ? prev : next;
    });
  }, [batchItems]);

  const invalidateBatch = () => {
    void queryClient.invalidateQueries({ queryKey: ["review-queue"] });
    void queryClient.invalidateQueries({ queryKey: ["dnas"] });
    void queryClient.invalidateQueries({ queryKey: ["graph"] });
  };

  /** per-item 增量更新（契约 §2.5）：success/skipped 移除、failed 保留标红 */
  const applyBatchResponse = (response: BatchResponse) => {
    // 批量否决回合成 id "<cid>:<rid>"，与单 id 映射合并（两者键空间不相交）。
    // 单 id 映射排除 merge 条目：同一 creative 可能同时是待归族和多对候选的
    // 端点，merge 对必须走合成 id，否则批量归族的结果会误摘候选卡片。
    const idToKey = new Map([
      ...keyByItemId(batchItems.filter((item) => item.kind !== "merge_candidate")),
      ...keyByPairId(batchItems),
    ]);
    const { removed, failed } = reduceBatchResults(response.results);
    const removedKeySet = new Set(
      removed
        .map((id) => idToKey.get(id))
        .filter((key): key is string => Boolean(key)),
    );
    const failedEntries = new Map(
      Object.entries(failed)
        .map(([id, failure]) => [idToKey.get(id), failure] as const)
        .filter((entry): entry is readonly [string, BatchFailure] => Boolean(entry[0])),
    );
    setRemovedKeys((prev) => new Set([...prev, ...removedKeySet]));
    setSelectedKeys(
      (prev) =>
        new Set(
          [...prev].filter(
            (key) => !removedKeySet.has(key) && !failedEntries.has(key),
          ),
        ),
    );
    setFailedByKey((prev) => {
      const next = new Map(prev);
      for (const key of removedKeySet) next.delete(key);
      for (const [key, failure] of failedEntries) next.set(key, failure);
      return next;
    });
    const { summary } = response;
    setBatchHint(
      t("review.batch.resultSummary")
        .replace("{ok}", String(summary.success))
        .replace("{skipped}", String(summary.skipped))
        .replace("{failed}", String(summary.failed)),
    );
    invalidateBatch();
  };

  const familyBatchMutation = useMutation({
    mutationFn: (suggestionIds: string[]) =>
      confirmFamiliesBatch(suggestionIds, crypto.randomUUID()),
    onSuccess: (response) => {
      setFamilyPreview(null);
      applyBatchResponse(response);
    },
    onError: (error) => setBatchHint(apiErrorText(error, t) || t("common.failedRetry")),
  });

  const assignBatchMutation = useMutation({
    mutationFn: ({ creativeIds, dnaId }: { creativeIds: string[]; dnaId: string }) =>
      batchAssignDna(creativeIds, dnaId, crypto.randomUUID()),
    onSuccess: applyBatchResponse,
    onError: (error) => setBatchHint(apiErrorText(error, t) || t("common.failedRetry")),
  });

  // 批量否决（P0b）：登记观察对，非永久裁决——confirm modal 明示语义后调 bulk 端点
  const rejectBatchMutation = useMutation({
    mutationFn: (pairs: Array<{ creative_id: string; related_creative_id: string }>) =>
      linkSimilarBulk(pairs, crypto.randomUUID()),
    onSuccess: (response) => {
      setRejectPreview(null);
      applyBatchResponse(response);
    },
    onError: (error) => setBatchHint(apiErrorText(error, t) || t("common.failedRetry")),
  });

  // --- 合并候选：归簇 + 分页 + 键盘焦点（P0b） ------------------------------
  const visibleMergeItems = useMemo(
    () =>
      (groups.get("merge_candidate") ?? []).filter(
        (item) => !removedKeys.has(selectionKey("merge_candidate", item)),
      ),
    [groups, removedKeys],
  );
  const mergeClusters = useMemo(
    () => clusterMergePairs(visibleMergeItems),
    [visibleMergeItems],
  );
  const mergePages = useMemo(
    () => paginateClusters(mergeClusters, MERGE_PAGE_SIZE),
    [mergeClusters],
  );
  const mergePageIndex = Math.min(mergePage, Math.max(mergePages.length - 1, 0));
  /** 当前页可见候选对的选中 key（按渲染顺序），键盘 ↑/↓ 在这条序列上移动 */
  const mergeFocusKeys = useMemo(
    () =>
      (mergePages[mergePageIndex] ?? []).flatMap((cluster) =>
        cluster.items.map((item) => selectionKey("merge_candidate", item)),
      ),
    [mergePages, mergePageIndex],
  );

  // 焦点移动后把焦点卡片滚进可视区
  useEffect(() => {
    if (!focusKey) return;
    document
      .querySelector(`[data-merge-key="${CSS.escape(focusKey)}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [focusKey]);

  /** 键盘流（P0b 加固）：↑/↓ 移动焦点，←/→/x 只 arm（预备）并入左/右/不是同一条，
   *  Enter 才执行 armed 动作，Esc 取消 armed。arm 与 confirm 不同键，
   *  且按住键自动重复（event.repeat）的 keydown 整体忽略——key repeat 或
   *  快速连按无法瞬间走完 arm+confirm。输入框/下拉聚焦时不劫持按键。 */
  const handleMergeKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.repeat) return;
    const target = event.target as HTMLElement;
    if (target.closest("input, select, textarea, [contenteditable='true']")) return;
    const action = mergeKeyAction(event.key);
    if (!action) return;
    const keys = mergeFocusKeys;
    if (keys.length === 0) return;
    const index = focusKey ? keys.indexOf(focusKey) : -1;
    if (action === "focus-down" || action === "focus-up") {
      event.preventDefault();
      const next =
        action === "focus-down"
          ? index === -1
            ? 0
            : moveFocusIndex(index, 1, keys.length)
          : index === -1
            ? keys.length - 1
            : moveFocusIndex(index, -1, keys.length);
      setFocusKey(keys[next]);
      return;
    }
    if (!focusKey) return;
    const handle = mergeHandles.current.get(focusKey);
    if (!handle) return;
    event.preventDefault();
    if (action === "arm-into-left") handle.armIntoLeft();
    else if (action === "arm-into-right") handle.armIntoRight();
    else if (action === "arm-not-same") handle.armNotSame();
    else if (action === "confirm-armed") handle.confirmArmed();
    else handle.cancelArmed();
  };

  const toggleSelected = (key: string) => {
    setSelectedKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
    // 重新勾选视为准备重试，清掉上一轮失败标红
    setFailedByKey((prev) => {
      if (!prev.has(key)) return prev;
      const next = new Map(prev);
      next.delete(key);
      return next;
    });
  };

  const setSectionSelection = (items: ReviewItem[], checked: boolean) => {
    setSelectedKeys((prev) => {
      const next = new Set(prev);
      for (const item of items) {
        const key = selectionKey(item.kind, item);
        if (checked) {
          next.add(key);
        } else {
          next.delete(key);
        }
      }
      return next;
    });
  };

  if (!data) return null;
  const total = KIND_ORDER.reduce((sum, kind) => sum + (groups.get(kind)?.length ?? 0), 0);
  const statsLine = judgeStatsLine(data, t);
  const unassignedCount = groups.get("dna_unassigned")?.length ?? 0;
  const familyHint = data.family_bootstrap_hint;
  // 后端 P0b 新增字段；未就绪（undefined）时降级为现状显示（不显示全量总数）
  const mergeTotal = (data as { merge_candidates_total?: number | null })
    .merge_candidates_total;

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
      {observingPairsTotal > 0 ? (
        <div className="flex px-5 pb-2">
          <button
            onClick={() => setShowObservations((open) => !open)}
            aria-expanded={showObservations}
            className="rounded-full bg-sky-50 px-2 py-1 text-[11px] font-medium text-sky-600 transition hover:bg-sky-100"
          >
            {t("inbox.observation.background").replace(
              "{n}",
              String(observingPairsTotal),
            )}
          </button>
        </div>
      ) : null}
      {data.observation_sync_status === "error" ? (
        <p
          role="status"
          className="mx-5 mb-2 rounded-lg bg-amber-50 px-2.5 py-1.5 text-[11px] leading-snug text-amber-800"
        >
          {t("inbox.observation.syncError")}
          {data.observation_sync_error ? (
            <span className="mt-0.5 block break-all text-amber-700">
              {data.observation_sync_error}
            </span>
          ) : null}
        </p>
      ) : null}
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
      {batchHint ? (
        <p className="px-5 pb-2 text-[11px] leading-snug text-neutral-400">
          {batchHint}
        </p>
      ) : null}
      {statsLine ? (
        <p className="px-5 pb-2 text-[11px] leading-snug text-neutral-400">
          {statsLine}
        </p>
      ) : null}
      <InterventionDensityWidget density={data.intervention_density} />
      <HubSkewWidget skew={data.hub_skew} />

      <div
        className="flex-1 space-y-4 overflow-y-auto px-3 pb-3 outline-none"
        tabIndex={0}
        onKeyDown={handleMergeKeyDown}
      >
        {showObservations && observingPairsTotal > 0 ? (
          <section aria-label={t("inbox.observation.title")}>
            <p className="mb-1.5 flex items-center gap-1.5 px-2 text-xs font-semibold text-neutral-600">
              <span className={cn("h-2 w-2 rounded-full", KIND_DOT.observation_pair)} />
              {t("inbox.observation.title")} · {observationTotal}
            </p>
            {observationsQuery.isPending ? (
              <p className="px-2 py-3 text-center text-xs text-neutral-400">
                {t("inbox.observation.loading")}
              </p>
            ) : observationsQuery.isError ? (
              <p className="mx-2 flex items-center justify-between gap-2 rounded-lg bg-red-50 px-2.5 py-2 text-[11px] text-red-600">
                {t("inbox.observation.loadFailed")}
                <button
                  onClick={() => void observationsQuery.refetch()}
                  className="shrink-0 rounded bg-white/70 px-1.5 py-0.5 font-medium transition hover:bg-white"
                >
                  {t("inbox.observation.retry")}
                </button>
              </p>
            ) : (
              <>
                {(observationsQuery.data?.items.length ?? 0) > 0 ? (
                  <ul className="space-y-1">
                    {observationsQuery.data?.items.map((item) => (
                      <BackgroundObservationCard
                        key={`${item.creative_id}-${item.related_creative_id}`}
                        item={item}
                        onSelectCreative={onSelectCreative}
                      />
                    ))}
                  </ul>
                ) : (
                  <p className="px-2 py-3 text-center text-xs text-neutral-400">
                    {t("inbox.observation.empty")}
                  </p>
                )}
                {observationPageCount > 1 ? (
                  <div className="mx-2 mt-1.5 flex items-center justify-between text-[11px] text-neutral-500">
                    <button
                      disabled={observationPageIndex === 0}
                      onClick={() => setObservationPage(observationPageIndex - 1)}
                      className="flex items-center gap-0.5 rounded px-1 py-0.5 transition hover:bg-black/5 disabled:opacity-40"
                    >
                      <ChevronLeft className="h-3 w-3" />
                      {t("review.merge.pagePrev")}
                    </button>
                    <span>
                      {t("review.merge.pageInfo")
                        .replace("{page}", String(observationPageIndex + 1))
                        .replace("{pages}", String(observationPageCount))}
                    </span>
                    <button
                      disabled={observationPageIndex >= observationPageCount - 1}
                      onClick={() => setObservationPage(observationPageIndex + 1)}
                      className="flex items-center gap-0.5 rounded px-1 py-0.5 transition hover:bg-black/5 disabled:opacity-40"
                    >
                      {t("review.merge.pageNext")}
                      <ChevronRight className="h-3 w-3" />
                    </button>
                  </div>
                ) : null}
              </>
            )}
          </section>
        ) : null}
        {total === 0 ? (
          <p className="px-2 py-8 text-center text-sm text-neutral-400">
            {t("inbox.empty")}
          </p>
        ) : (
          KIND_ORDER.map((kind) => {
            const items = groups.get(kind) ?? [];
            if (items.length === 0) return null;
            const batchable = BATCHABLE_KINDS.has(kind);
            const isMerge = kind === "merge_candidate";
            // 批量 success/skipped 的卡片先从渲染里摘掉，等队列 refetch 落锤
            const visible = batchable
              ? items.filter((item) => !removedKeys.has(selectionKey(kind, item)))
              : items;
            if (visible.length === 0) return null;
            const selectedInSection = batchable
              ? visible.filter((item) => selectedKeys.has(selectionKey(kind, item)))
              : [];
            const renderItemCard = (item: ReviewItem, index: number) => {
              const itemKey = selectionKey(kind, item);
              const failure = failedByKey.get(itemKey);
              const focused = isMerge && itemKey === focusKey;
              return (
              // 稳定 key：用业务 id 而非 index——否则列表移位后
              // DnaAssignActions 等子组件的 useState 会继承上一条目的
              // 选中值（建议徽章与下拉框错位的根因）。
              // 同一 creative 可同时是多对合并候选的左侧（A↔B、A↔C），
              // 必须拼上 related_creative_id，否则 key 重复、React 复用
              // 错组件实例（预览/裁决状态互相串扰）。
              <li
                key={`${kind}-${
                  item.creative_id ??
                  item.derivation_id ??
                  item.asset_id ??
                  item.family?.suggestion_id ??
                  index
                }-${item.related_creative_id ?? ""}`}
                data-merge-key={isMerge ? itemKey : undefined}
              >
                {item.kind === "low_confidence" && item.asset_id ? (
                  <Link
                    to={`/assets/${item.asset_id}`}
                    className="block rounded-xl px-3 py-2 transition hover:bg-black/[0.04]"
                  >
                    <span className="line-clamp-2 block break-all text-sm font-medium leading-snug text-neutral-900" title={item.title}>
                      {inboxTitleLine(item, t)}
                    </span>
                    <span className="mt-0.5 line-clamp-2 block text-xs leading-relaxed text-neutral-500">
                      {inboxReasonLine(item, t)}
                    </span>
                  </Link>
                ) : (
                  <div className="flex items-start gap-1.5">
                    {batchable ? (
                      <input
                        type="checkbox"
                        checked={selectedKeys.has(itemKey)}
                        onChange={() => toggleSelected(itemKey)}
                        aria-label={t("review.batch.selectItem")}
                        className="mt-2.5 h-3.5 w-3.5 shrink-0 accent-rose-500"
                      />
                    ) : null}
                  <div
                    className={cn(
                      "min-w-0 flex-1 rounded-xl px-3 py-2 transition hover:bg-black/[0.04]",
                      failure ? "bg-red-50 ring-1 ring-red-200 hover:bg-red-50" : null,
                      // 键盘焦点对的高亮（↑/↓ 移动）
                      focused ? "bg-amber-50 ring-2 ring-amber-400 hover:bg-amber-50" : null,
                    )}
                  >
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
                          {inboxTitleLine(item, t)}
                        </span>
                      )}
                      <span className="mt-0.5 line-clamp-2 block text-xs leading-relaxed text-neutral-500">
                        {inboxReasonLine(item, t)}
                      </span>
                    </button>
                    {item.kind === "family_bootstrap" ? (
                      <FamilyBootstrapActions item={item} />
                    ) : item.kind === "rule_keyword" ? (
                      <RuleKeywordActions item={item} />
                    ) : item.kind === "archive_suggestion" ? (
                      <ArchiveActions item={item} />
                    ) : item.kind === "merge_candidate" ? (
                      <MergeActions
                        ref={(handle: MergeActionsHandle | null) => {
                          if (handle) mergeHandles.current.set(itemKey, handle);
                          else mergeHandles.current.delete(itemKey);
                        }}
                        item={item}
                      />
                    ) : item.kind === "pending_verdict" ? (
                      <VerdictActions item={item} />
                    ) : item.kind === "derivation_review" ? (
                      <DerivationReviewActions item={item} />
                    ) : item.kind === "dna_unassigned" ? (
                      <DnaAssignActions item={item} />
                    ) : item.kind === "observation_pair" ? (
                      <ClosePairActions item={item} />
                    ) : null}
                    {failure ? (
                      <span className="mt-1 block text-[11px] text-red-500">
                        {t("review.batch.failedItem")}：
                        {failure.message ??
                          t(`review.batch.reason.${failure.reason ?? "error"}`)}
                      </span>
                    ) : null}
                  </div>
                  </div>
                )}
              </li>
              );
            };
            return (
              <section key={kind}>
                <p className="mb-1.5 flex items-center gap-1.5 px-2 text-xs font-semibold text-neutral-600">
                  <span className={cn("h-2 w-2 rounded-full", KIND_DOT[kind])} />
                  {t(`inbox.kind.${kind}`)} · {visible.length}
                  {isMerge && typeof mergeTotal === "number" ? (
                    // 后端 merge_candidates_total 就绪时显示全量总数；缺失则降级为现状
                    <span className="font-normal text-neutral-400">
                      {t("review.merge.totalCount").replace("{total}", String(mergeTotal))}
                    </span>
                  ) : null}
                  {batchable ? (
                    <span className="ml-auto flex items-center gap-1.5 text-[11px] font-normal">
                      {selectedInSection.length > 0 ? (
                        <span className="text-neutral-400">
                          {t("review.batch.selected").replace(
                            "{n}",
                            String(selectedInSection.length),
                          )}
                        </span>
                      ) : null}
                      <button
                        onClick={() => setSectionSelection(visible, true)}
                        className="rounded px-1 py-0.5 text-neutral-500 transition hover:bg-black/5 hover:text-neutral-700"
                      >
                        {t("review.batch.selectAll")}
                      </button>
                      <button
                        onClick={() => setSectionSelection(visible, false)}
                        disabled={selectedInSection.length === 0}
                        className="rounded px-1 py-0.5 text-neutral-500 transition hover:bg-black/5 hover:text-neutral-700 disabled:opacity-40"
                      >
                        {t("review.batch.clear")}
                      </button>
                    </span>
                  ) : null}
                </p>
                {kind === "family_bootstrap" ? (
                  <div className="mx-2 mb-1.5 flex items-center gap-1">
                    <button
                      disabled={
                        selectedInSection.length === 0 || familyBatchMutation.isPending
                      }
                      onClick={() => setFamilyPreview([...selectedInSection])}
                      className="rounded-md bg-rose-50 px-1.5 py-0.5 text-[11px] font-medium text-rose-600 transition hover:bg-rose-100 disabled:opacity-40"
                    >
                      {t("review.batch.confirmSelected")}
                    </button>
                    <button
                      disabled={familyBatchMutation.isPending}
                      onClick={() => setFamilyPreview([...visible])}
                      className="rounded-md bg-rose-500 px-1.5 py-0.5 text-[11px] font-medium text-white transition hover:bg-rose-600 disabled:opacity-40"
                    >
                      {t("review.batch.confirmAll")}
                    </button>
                  </div>
                ) : null}
                {kind === "dna_unassigned" && selectedInSection.length > 0 ? (
                  <div className="mx-2 mb-1.5 flex items-center gap-1 rounded-lg bg-rose-50/70 px-2 py-1.5">
                    <select
                      value={assignDnaId}
                      onChange={(event) => setAssignDnaId(event.target.value)}
                      className="h-6 min-w-0 flex-1 rounded-md border border-black/10 bg-white px-1 text-[11px] text-neutral-700"
                    >
                      <option value="">{t("inbox.dna.selectFamily")}</option>
                      {(dnas ?? []).map((dna) => (
                        <option key={dna.id} value={dna.id}>
                          {dna.code} {dna.name}
                        </option>
                      ))}
                    </select>
                    <button
                      disabled={!assignDnaId || assignBatchMutation.isPending}
                      onClick={() =>
                        assignBatchMutation.mutate({
                          creativeIds: selectedInSection
                            .map((item) => item.creative_id)
                            .filter((id): id is string => Boolean(id)),
                          dnaId: assignDnaId,
                        })
                      }
                      className="shrink-0 rounded-md bg-rose-500 px-1.5 py-0.5 text-[11px] font-medium text-white transition hover:bg-rose-600 disabled:opacity-40"
                    >
                      {assignBatchMutation.isPending
                        ? t("review.batch.assigning")
                        : t("review.batch.assignTo")}
                    </button>
                  </div>
                ) : null}
                {isMerge ? (
                  <>
                    <div className="mx-2 mb-1.5 flex items-center gap-1">
                      <button
                        disabled={
                          selectedInSection.length === 0 || rejectBatchMutation.isPending
                        }
                        onClick={() => setRejectPreview([...selectedInSection])}
                        className="rounded-md bg-neutral-100 px-1.5 py-0.5 text-[11px] font-medium text-neutral-600 transition hover:bg-neutral-200 disabled:opacity-40"
                      >
                        {rejectBatchMutation.isPending
                          ? t("review.merge.batchRejecting")
                          : t("review.merge.batchReject")}
                      </button>
                    </div>
                    <p className="mx-2 mb-1.5 text-[10px] leading-snug text-neutral-400">
                      {t("review.merge.keyboardHint")}
                    </p>
                    {(mergePages[mergePageIndex] ?? []).map((cluster) => (
                      <div key={cluster.hubId} className="mb-1.5">
                        {cluster.items.length > 1 ? (
                          // 连通分量簇：枢纽 creative 的多对一簇裁一次。
                          // 簇级快捷操作只有"全部不是同一素材"（登记观察对、可逆）；
                          // 产品决策：不做簇级并入（太危险）。
                          <p className="mb-1 flex items-center justify-between gap-2 rounded-md bg-amber-50 px-2 py-1 text-[11px] font-medium text-amber-800">
                            <span>
                              {t("review.merge.clusterTitle")
                                .replace("{name}", cluster.hubName)
                                .replace("{n}", String(cluster.items.length))}
                            </span>
                            <button
                              disabled={rejectBatchMutation.isPending}
                              onClick={() => setRejectPreview([...cluster.items])}
                              className="shrink-0 rounded-md bg-white/70 px-1.5 py-0.5 text-[11px] font-medium text-amber-800 ring-1 ring-amber-300 transition hover:bg-white disabled:opacity-40"
                            >
                              {rejectBatchMutation.isPending
                                ? t("review.merge.batchRejecting")
                                : t("review.merge.clusterRejectAll")}
                            </button>
                          </p>
                        ) : null}
                        <ul className="space-y-1">
                          {cluster.items.map((item, index) => renderItemCard(item, index))}
                        </ul>
                      </div>
                    ))}
                    {mergePages.length > 1 ? (
                      <div className="mx-2 mt-1.5 flex items-center justify-between text-[11px] text-neutral-500">
                        <button
                          disabled={mergePageIndex === 0}
                          onClick={() => setMergePage(mergePageIndex - 1)}
                          className="flex items-center gap-0.5 rounded px-1 py-0.5 transition hover:bg-black/5 disabled:opacity-40"
                        >
                          <ChevronLeft className="h-3 w-3" />
                          {t("review.merge.pagePrev")}
                        </button>
                        <span>
                          {t("review.merge.pageInfo")
                            .replace("{page}", String(mergePageIndex + 1))
                            .replace("{pages}", String(mergePages.length))}
                        </span>
                        <button
                          disabled={mergePageIndex >= mergePages.length - 1}
                          onClick={() => setMergePage(mergePageIndex + 1)}
                          className="flex items-center gap-0.5 rounded px-1 py-0.5 transition hover:bg-black/5 disabled:opacity-40"
                        >
                          {t("review.merge.pageNext")}
                          <ChevronRight className="h-3 w-3" />
                        </button>
                      </div>
                    ) : null}
                  </>
                ) : (
                  <ul className="space-y-1">
                    {visible.map((item, index) => renderItemCard(item, index))}
                  </ul>
                )}
              </section>
            );
          })
        )}
      </div>
      {familyPreview
        ? // 预览 modal 必须 portal 到 body：面板带 backdrop-blur（会形成 fixed
          // 包含块）且 overflow-hidden，直接渲染 fixed 弹层会被裁剪在 400px
          // 面板内（与 MergeActions 预览同坑）。
          createPortal(
            <div
              className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
              onClick={() =>
                familyBatchMutation.isPending ? null : setFamilyPreview(null)
              }
            >
              <div
                className="flex max-h-[80vh] w-full max-w-md flex-col overflow-hidden rounded-xl bg-white shadow-xl"
                onClick={(event) => event.stopPropagation()}
              >
                <div className="flex items-center justify-between px-4 py-3">
                  <h3 className="text-sm font-semibold text-neutral-900">
                    {t("review.batch.previewTitle")}
                  </h3>
                  <button
                    onClick={() => setFamilyPreview(null)}
                    disabled={familyBatchMutation.isPending}
                    className="rounded p-0.5 text-neutral-400 transition hover:bg-black/5 hover:text-neutral-600 disabled:opacity-40"
                    aria-label={t("common.close")}
                  >
                    <X className="h-4 w-4" />
                  </button>
                </div>
                <ul className="flex-1 space-y-1 overflow-y-auto px-4 pb-2">
                  {familyPreview.map((item) =>
                    item.family ? (
                      <li
                        key={item.family.suggestion_id}
                        className="flex items-center justify-between gap-2 rounded-lg bg-black/[0.03] px-2.5 py-1.5"
                      >
                        <span className="min-w-0 truncate text-xs font-medium text-neutral-800">
                          {item.family.existing_dna_code
                            ? t("inbox.family.attachTo").replace(
                                "{label}",
                                item.family.name,
                              )
                            : item.family.name}
                        </span>
                        <span className="shrink-0 text-[11px] text-neutral-500">
                          {t("inbox.family.members").replace(
                            "{n}",
                            String(item.family.members.length),
                          )}
                        </span>
                      </li>
                    ) : null,
                  )}
                </ul>
                <div className="border-t border-black/5 px-4 py-3">
                  <p className="text-[11px] text-neutral-500">
                    {t("review.batch.previewSummary")
                      .replace("{families}", String(familyPreview.length))
                      .replace(
                        "{members}",
                        String(
                          familyPreview.reduce(
                            (sum, item) => sum + (item.family?.members.length ?? 0),
                            0,
                          ),
                        ),
                      )}
                  </p>
                  <p className="mt-1 text-[11px] leading-snug text-neutral-400">
                    {t("review.batch.previewNote")}
                  </p>
                  <div className="mt-2.5 flex items-center justify-end gap-1.5">
                    <button
                      onClick={() => setFamilyPreview(null)}
                      disabled={familyBatchMutation.isPending}
                      className="rounded-md bg-neutral-100 px-2.5 py-1 text-[11px] font-medium text-neutral-500 transition hover:bg-neutral-200 disabled:opacity-40"
                    >
                      {t("review.batch.cancel")}
                    </button>
                    <button
                      disabled={familyBatchMutation.isPending}
                      onClick={() =>
                        familyBatchMutation.mutate(
                          familyPreview
                            .map((item) => item.family?.suggestion_id)
                            .filter((id): id is string => Boolean(id)),
                        )
                      }
                      className="rounded-md bg-rose-500 px-2.5 py-1 text-[11px] font-medium text-white transition hover:bg-rose-600 disabled:opacity-40"
                    >
                      {familyBatchMutation.isPending
                        ? t("review.batch.confirming")
                        : t("review.batch.confirm")}
                    </button>
                  </div>
                </div>
              </div>
            </div>,
            document.body,
          )
        : null}
      {rejectPreview
        ? // 批量否决确认 modal：明示语义（登记观察对、可再合并、非永久裁决），
          // 与 familyPreview 同款 portal（面板 backdrop-blur/overflow 会裁剪 fixed）。
          createPortal(
            <div
              className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
              onClick={() =>
                rejectBatchMutation.isPending ? null : setRejectPreview(null)
              }
            >
              <div
                className="flex max-h-[80vh] w-full max-w-md flex-col overflow-hidden rounded-xl bg-white shadow-xl"
                onClick={(event) => event.stopPropagation()}
              >
                <div className="flex items-center justify-between px-4 py-3">
                  <h3 className="text-sm font-semibold text-neutral-900">
                    {t("review.merge.batchRejectTitle")}
                  </h3>
                  <button
                    onClick={() => setRejectPreview(null)}
                    disabled={rejectBatchMutation.isPending}
                    className="rounded p-0.5 text-neutral-400 transition hover:bg-black/5 hover:text-neutral-600 disabled:opacity-40"
                    aria-label={t("common.close")}
                  >
                    <X className="h-4 w-4" />
                  </button>
                </div>
                <ul className="flex-1 space-y-1 overflow-y-auto px-4 pb-2">
                  {rejectPreview.map((item) => (
                    <li
                      key={selectionKey("merge_candidate", item)}
                      className="rounded-lg bg-black/[0.03] px-2.5 py-1.5 text-xs font-medium text-neutral-800"
                    >
                      <span className="break-all">
                        {item.creative_name ?? item.creative_id}
                      </span>
                      <span className="mx-1 text-neutral-400">⇄</span>
                      <span className="break-all">
                        {item.related_creative_name ?? item.related_creative_id}
                      </span>
                    </li>
                  ))}
                </ul>
                <div className="border-t border-black/5 px-4 py-3">
                  <p className="text-[11px] text-neutral-500">
                    {t("review.merge.batchRejectSummary").replace(
                      "{n}",
                      String(rejectPreview.length),
                    )}
                  </p>
                  <p className="mt-1 text-[11px] leading-snug text-neutral-400">
                    {t("review.merge.batchRejectNote")}
                  </p>
                  <div className="mt-2.5 flex items-center justify-end gap-1.5">
                    <button
                      onClick={() => setRejectPreview(null)}
                      disabled={rejectBatchMutation.isPending}
                      className="rounded-md bg-neutral-100 px-2.5 py-1 text-[11px] font-medium text-neutral-500 transition hover:bg-neutral-200 disabled:opacity-40"
                    >
                      {t("review.batch.cancel")}
                    </button>
                    <button
                      disabled={rejectBatchMutation.isPending}
                      onClick={() =>
                        rejectBatchMutation.mutate(
                          rejectPreview
                            .map((item) => ({
                              creative_id: item.creative_id,
                              related_creative_id: item.related_creative_id,
                            }))
                            .filter(
                              (pair): pair is {
                                creative_id: string;
                                related_creative_id: string;
                              } => Boolean(pair.creative_id && pair.related_creative_id),
                            ),
                        )
                      }
                      className="rounded-md bg-neutral-700 px-2.5 py-1 text-[11px] font-medium text-white transition hover:bg-neutral-800 disabled:opacity-40"
                    >
                      {rejectBatchMutation.isPending
                        ? t("review.merge.batchRejecting")
                        : t("review.batch.confirm")}
                    </button>
                  </div>
                </div>
              </div>
            </div>,
            document.body,
          )
        : null}
    </div>
  );
}
