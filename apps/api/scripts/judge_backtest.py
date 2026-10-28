"""B2 离线回测：merge-judge-v4.1（指纹卡 + 代码规则 + 判异复核闸）vs 存量人工 gold set。

Gold set（开发诊断集，不是独立人工测试集）：
- same：同一 creative 内的变体两两组合，用各变体自己的 asset 分析证据；
  这些对由当前分组派生，不等于用户逐对确认。
- different：Neo4j SIMILAR_TO 观察对——人工"维持拆分"登记，
  用 creative 级证据（首个变体分析，同生产判定口径）。

gold 来源偏差（读数前必读）：
- same 侧由"当前 creative 分组"派生：分组本身可能已被污染（历史上的误并/
  误拆），且变体两两组合 ≠ 人工逐对确认——该侧只作开发诊断信号，
  不作精度/召回承诺；
- diff 侧来自 Neo4j SIMILAR_TO 观察对：人工只登记了"被选中 review 且维持
  拆分"的对——选择偏差使相似度偏高、最值得纠结的对被过度代表，
  "未登记"不等于"已合并"。判异召回分母受此偏差影响。

口径与纪律：
- 判异精度 = 判 different_concept 的对中真不同的比例，目标 ≥99%，
  报 Wilson 单侧 95% 置信下界；同侧错判异（same gold → different_concept）
  为最严重错误类型，单列；
- 不在这份数据上调阈值（调/测同源会高估）——阈值只从 risk-coverage
  曲线带 margin 读取，终阈值由 shadow 期累积样本定（方案 §4.3 v1 阶段）；
- 结果 JSONL + Markdown 报告落盘，可复跑回归。

回测语义（harness v4.2，2026-10-26）：
- judge_pair 返回 None 的灰区分两类（classify_none）：语义灰区（复核推翻 /
  skeleton_new，不应通过反复重抽改变语义结论）与基础设施失败
  （打卡 / 复核调用失败，重试可能恢复）。语义灰区不重试；基础设施失败按对
  有限重试（默认 4 次尝试），重试只清当前对打卡失败的缓存条目，不动其他
  对；耗尽后该对记 error，不归灰区、不叫推翻。
- 复核调用逐次计数三态 confirmed/overturned/failed；推翻率只以 overturned
  为分子、confirmed+overturned 为分母，failed 单列。
- --max-llm-calls 限制 complete_json 调用次数（打卡+复核，缓存命中不计）：
  耗尽后未跑/未完的对记 not_run，不纳入灰区与任何率的分母。
  这是函数调用口径；temperature 兼容重试可能产生额外 provider HTTP 请求。
  默认不设上限（保持旧行为），真实 provider 硬预算由 vNext runner 负责。

Usage（api 容器内）:
    python -u -m scripts.judge_backtest                     # 全量
    python -u -m scripts.judge_backtest --limit 6           # 冒烟
    python -u -m scripts.judge_backtest --max-llm-calls 50  # 预算内试跑
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
import threading
import time
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.config import get_settings
from app.database import SessionLocal
from app.services import fingerprint_card as _fingerprint_card_mod
from app.services import merge_judge as _merge_judge_mod
from app.services.fingerprint_card import FingerprintCard, cache_key, match_cards
from app.services.merge_judge import PairEvidence, evidence_from_analysis, judge_pair
from app.services.settings import AIConfig, resolve_ai_config

_REPORT_FILENAME = "judge_backtest_report.md"
_RESULTS_FILENAME = "judge_backtest_results.jsonl"
# 限流敏感：kimi-for-coding 有 RPM 窗口，complete_json 失败静默 → 并发压低
_CONCURRENCY = 2

# 每对 status（relation 保持生产值，status 是归一短码，报告/测试用）
STATUS_SAME = "same"
STATUS_DIFF = "diff"
STATUS_GREY = "grey"
STATUS_ERROR = "error"
STATUS_NOT_RUN = "not_run"
_RELATION_STATUS = {
    "same_variant_family": STATUS_SAME,
    "different_concept": STATUS_DIFF,
    STATUS_GREY: STATUS_GREY,
    STATUS_ERROR: STATUS_ERROR,
    STATUS_NOT_RUN: STATUS_NOT_RUN,
}

# abstain / failure 原因码
ABSTAIN_RECHECK_OVERTURNED = "recheck_overturned"  # 复核推翻（语义，不重试）
ABSTAIN_SKELETON_NEW = "skeleton_new"  # 库外新结构（语义，不重试）
ABSTAIN_MATCH_UNKNOWN = "match_none"  # 有效卡但规则无结论（防御）
ABSTAIN_NO_EVIDENCE = "no_evidence"  # 无基础设施失败证据，不重试
ABSTAIN_BUDGET = "budget_exhausted"  # LLM 预算耗尽，未判
FAIL_CARD = "card_failed"  # 打卡失败（基础设施，可重试）
FAIL_RECHECK = "recheck_failed"  # 复核调用失败（基础设施，可重试）

_ANALYSIS_COLS = (
    "summary", "hook", "conflict", "gameplay", "reward",
    "characters", "environment", "emotion", "tags", "variant_factors",
)


def _variant_evidence(db) -> dict[str, tuple[str, PairEvidence]]:
    """variant_id → (creative_id, 该变体 asset 的分析证据)。"""
    rows = db.execute(
        text(
            "select v.id, v.creative_id, a.summary, a.hook, a.conflict, "
            "a.gameplay, a.reward, a.characters, a.environment, a.emotion, "
            "a.tags, a.variant_factors "
            "from creative_variants v "
            "join analysis_results a on a.asset_id = v.asset_id "
            "order by a.created_at"
        )
    ).fetchall()
    out: dict[str, tuple[str, PairEvidence]] = {}
    for row in rows:
        variant_id, creative_id = row[0], row[1]
        if variant_id in out:
            continue  # 多变体同 asset 重复时取首个
        out[variant_id] = (
            creative_id,
            evidence_from_analysis(dict(zip(_ANALYSIS_COLS, row[2:], strict=True))),
        )
    return out


def _creative_names(db) -> dict[str, str]:
    return dict(db.execute(text("select id, name from creatives")).fetchall())


def _same_gold(db) -> list[tuple[str, str, str]]:
    """(pair_key, variant_a, variant_b)：同 creative 内变体两两。"""
    by_creative: dict[str, list[str]] = {}
    for variant_id, creative_id in db.execute(
        text("select id, creative_id from creative_variants")
    ).fetchall():
        by_creative.setdefault(creative_id, []).append(variant_id)
    pairs = []
    for creative_id, variants in by_creative.items():
        for a, b in itertools.combinations(sorted(variants), 2):
            pairs.append((f"same:{a}:{b}", a, b))
    return pairs


def _diff_gold() -> list[tuple[str, str, str]]:
    """(pair_key, creative_a, creative_b)：/graph 的 SIMILAR_TO 观察对。"""
    with urllib.request.urlopen("http://localhost:8000/graph", timeout=30) as resp:
        data = json.load(resp)["data"]
    pairs = []
    for edge in data["edges"]:
        if edge.get("type") != "SIMILAR_TO":
            continue
        a = edge["source"].removeprefix("creative:")
        b = edge["target"].removeprefix("creative:")
        pairs.append((f"diff:{a}:{b}", a, b))
    return pairs


def _wilson_lower(k: int, n: int, z: float = 1.645) -> float:
    """单侧 95% Wilson 下界（z=1.645）。"""
    if n == 0:
        return 0.0
    p = k / n
    denom = 1 + z * z / n
    center = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (center - margin) / denom)


def classify_none(
    card_cache: dict[str, FingerprintCard | None],
    key_a: str,
    key_b: str,
    recheck_outcomes: list[dict[str, Any] | None],
) -> tuple[str, str]:
    """judge_pair 返回 None 后的归因：(kind, reason)。

    kind="grey" 语义灰区（不重试）；kind="infra" 基础设施失败（可有限重试）。
    只读 card_cache 当前状态与本次 judge_pair 调用内经包装 recheck_split
    记录的结果——复核推翻、skeleton_new、有效卡规则无结论是语义灰区；
    卡 None / 复核 None 才是可重试失败；没有证据不误判、也不无意义重试。
    """
    for outcome in recheck_outcomes:
        if outcome is not None and not outcome.get("split_confirmed"):
            return "grey", ABSTAIN_RECHECK_OVERTURNED
    card_a = card_cache.get(key_a)
    card_b = card_cache.get(key_b)
    if card_a is None or card_b is None:
        # 打卡失败（None 也缓存）或缓存异常缺失——重试可能恢复
        return "infra", FAIL_CARD
    if card_a.skeleton is None or card_b.skeleton is None:
        # skeleton_new：库外新结构，不应通过反复重抽改变语义结论
        return "grey", ABSTAIN_SKELETON_NEW
    if match_cards(card_a, card_b) is None:
        return "grey", ABSTAIN_MATCH_UNKNOWN
    if recheck_outcomes and all(o is None for o in recheck_outcomes):
        # 两张有效卡、规则可判，只可能是复核调用失败——重试可能恢复
        return "infra", FAIL_RECHECK
    # 有效卡 + 有复核结论却返回 None：与 judge_pair 实现矛盾，无证据指向
    # 基础设施问题，按语义灰区处理（没有证据不要无意义重试）
    return "grey", ABSTAIN_NO_EVIDENCE


class _BudgetExhausted(Exception):
    """LLM 调用达到 --max-llm-calls 硬上限（打卡+复核统一计数）。"""


class BacktestRunner:
    """单批次回测执行器：语义灰区不重试、基础设施失败按对有限重试。

    通过 patch 模块命名空间包装 merge_judge.recheck_split（计数 + 记录
    每次结果）与 fingerprint_card.complete_json（LLM 调用计数 + 预算闸），
    with 退出即恢复——生产 merge_judge / fingerprint_card 行为不变。
    线程安全：线程池每线程独立记录 recheck 结果（threading.local），
    预算计数用锁 + Event。
    """

    def __init__(
        self,
        config: AIConfig,
        *,
        card_cache: dict[str, FingerprintCard | None] | None = None,
        max_attempts: int = 4,
        sleep_seconds: float = 20.0,
        max_llm_calls: int | None = None,
        sleep: Any = time.sleep,
    ) -> None:
        self.config = config
        self.card_cache = card_cache if card_cache is not None else {}
        self.max_attempts = max_attempts
        self.sleep_seconds = sleep_seconds
        self.max_llm_calls = max_llm_calls
        self._sleep = sleep
        self.recheck_stats: Counter = Counter()  # confirmed/overturned/failed
        self.llm_calls = 0  # 经 complete_json 的实际调用（缓存命中不计）
        self._tls = threading.local()
        self._budget_lock = threading.Lock()
        self._budget_exhausted = threading.Event()
        self._orig_recheck = None
        self._orig_complete_json = None

    def __enter__(self) -> "BacktestRunner":
        self._orig_recheck = _merge_judge_mod.recheck_split
        self._orig_complete_json = _fingerprint_card_mod.complete_json

        def _counting_recheck(*args: Any, **kwargs: Any) -> Any:
            outcomes = getattr(self._tls, "recheck_outcomes", None)
            if outcomes is None:
                outcomes = self._tls.recheck_outcomes = []
            outcome = self._orig_recheck(*args, **kwargs)
            outcomes.append(outcome)
            if outcome is None:
                self.recheck_stats["failed"] += 1
            elif outcome["split_confirmed"]:
                self.recheck_stats["confirmed"] += 1
            else:
                self.recheck_stats["overturned"] += 1
            return outcome

        # judge_pair 从 merge_judge / fingerprint_card 模块命名空间解析
        # recheck_split / complete_json，patch 该处即可，退出时恢复
        _merge_judge_mod.recheck_split = _counting_recheck
        _fingerprint_card_mod.complete_json = self._counted_complete_json
        return self

    def __exit__(self, *exc_info: Any) -> bool:
        _merge_judge_mod.recheck_split = self._orig_recheck
        _fingerprint_card_mod.complete_json = self._orig_complete_json
        return False

    # -- LLM 调用计数 / 预算闸 ----------------------------------------------

    def _counted_complete_json(self, *args: Any, **kwargs: Any) -> Any:
        with self._budget_lock:
            if (
                self.max_llm_calls is not None
                and self.llm_calls >= self.max_llm_calls
            ):
                self._budget_exhausted.set()
                raise _BudgetExhausted(
                    f"LLM 调用达到上限 --max-llm-calls={self.max_llm_calls}"
                )
            self.llm_calls += 1
        return self._orig_complete_json(*args, **kwargs)

    # -- 单对执行 -----------------------------------------------------------

    def _clear_failed_cards(self, text_a: str, text_b: str) -> None:
        """重试前只清当前对打卡失败的缓存条目（值 None）；不动其他对，
        也不动 skeleton_new 等非 None 卡（重试零 LLM 成本，清了反而重 roll）。"""
        for ev_text in (text_a, text_b):
            key = cache_key(ev_text)
            if self.card_cache.get(key) is None:
                self.card_cache.pop(key, None)

    def _result(
        self,
        task: tuple[str, str, str, PairEvidence, PairEvidence],
        *,
        relation: str,
        attempts: int,
        judged: Any = None,
        abstain: str = "",
        error: str = "",
    ) -> dict[str, Any]:
        key, gold, label = task[0], task[1], task[2]
        j = judged
        return {
            "key": key,
            "gold": gold,
            "label": label,
            "relation": relation,  # 生产 relation 值，保持下游兼容
            "status": _RELATION_STATUS.get(relation, relation),
            "attempts": attempts,
            "votes": j.votes if j else 0,
            "core": j.core if j else {},
            "reason": j.reason if j else "",
            "cards": j.cards if j else None,
            "recheck": j.recheck if j else None,
            "abstain": abstain,
            "error": error,
        }

    def run(self, task: tuple[str, str, str, PairEvidence, PairEvidence]) -> dict[str, Any]:
        """跑一对 gold，返回结果 dict（含 attempts/status/abstain/error）。"""
        ev_a, ev_b = task[3], task[4]
        text_a, text_b = ev_a.full_text(), ev_b.full_text()
        if self._budget_exhausted.is_set():
            return self._result(
                task, relation=STATUS_NOT_RUN, attempts=0, abstain=ABSTAIN_BUDGET
            )
        if not text_a or not text_b:
            return self._result(
                task, relation=STATUS_GREY, attempts=0, abstain=ABSTAIN_NO_EVIDENCE
            )
        last_failure = ""
        for attempt in range(1, self.max_attempts + 1):
            if attempt > 1:
                self._sleep(self.sleep_seconds * (attempt - 1))  # 0s/20s/40s…
            self._tls.recheck_outcomes = []
            try:
                j = judge_pair(
                    self.config, evidence_a=ev_a, evidence_b=ev_b,
                    card_cache=self.card_cache,
                )
            except _BudgetExhausted:
                return self._result(
                    task, relation=STATUS_NOT_RUN, attempts=attempt - 1,
                    abstain=ABSTAIN_BUDGET,
                )
            except Exception as exc:  # noqa: BLE001
                last_failure = f"judge_pair raised {exc.__class__.__name__}: {str(exc)[:150]}"
                continue
            if j is not None:
                return self._result(
                    task, relation=j.relation, attempts=attempt, judged=j
                )
            kind, reason = classify_none(
                self.card_cache, cache_key(text_a), cache_key(text_b),
                self._tls.recheck_outcomes,
            )
            if kind == "grey":
                # 语义灰区：不应通过反复重抽改变语义结论——不重试
                return self._result(
                    task, relation=STATUS_GREY, attempts=attempt, abstain=reason
                )
            last_failure = reason
            self._clear_failed_cards(text_a, text_b)
        # 基础设施失败重试耗尽：记 error，不归灰区、不叫推翻
        return self._result(
            task, relation=STATUS_ERROR, attempts=self.max_attempts,
            abstain=last_failure, error=last_failure,
        )


def summarize_recheck(recheck_stats: Counter) -> dict[str, Any]:
    """复核三态汇总。推翻率只算 overturned（分母 = 有结论的复核），
    failed 单列——禁止把失败叫推翻、禁止并入推翻率。"""
    confirmed = recheck_stats.get("confirmed", 0)
    overturned = recheck_stats.get("overturned", 0)
    failed = recheck_stats.get("failed", 0)
    decided = confirmed + overturned
    return {
        "total": decided + failed,
        "confirmed": confirmed,
        "overturned": overturned,
        "failed": failed,
        "decided": decided,
        "overturn_rate": overturned / decided if decided else None,
    }


def summarize_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """按 status 汇总。grey 只含语义灰区；error 与 not_run 单列。
    error 保留在 gold 总数中，not_run 不进入指标分母。"""
    by_status = Counter(r["status"] for r in results)
    return {
        "unique_pairs": len({r["key"] for r in results}),
        "same": by_status.get(STATUS_SAME, 0),
        "diff": by_status.get(STATUS_DIFF, 0),
        "grey": by_status.get(STATUS_GREY, 0),
        "error": by_status.get(STATUS_ERROR, 0),
        "not_run": by_status.get(STATUS_NOT_RUN, 0),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 对（冒烟）")
    parser.add_argument("--report", type=Path, default=None, help="报告输出路径")
    parser.add_argument("--results", type=Path, default=None, help="JSONL 结果路径")
    parser.add_argument(
        "--max-llm-calls",
        type=int,
        default=None,
        dest="max_llm_calls",
        help="complete_json 调用上限（非底层 HTTP 次数；temperature 兼容重试另计）；"
        "耗尽后未跑/未完的对记 not_run（默认不限）",
    )
    return parser.parse_args(argv)


def _default_output_path(filename: str) -> Path:
    """/tmp 下默认输出：同名文件已存在时依次追加 .1/.2/…，不覆盖旧报告。"""
    candidate = Path("/tmp") / filename
    suffix = 1
    while candidate.exists():
        candidate = Path("/tmp") / f"{filename}.{suffix}"
        suffix += 1
    return candidate


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args(argv)
    report_path = args.report or _default_output_path(_REPORT_FILENAME)
    results_path = args.results or _default_output_path(_RESULTS_FILENAME)
    limit = args.limit

    db = SessionLocal()
    try:
        config = resolve_ai_config(db, get_settings())
        variant_ev = _variant_evidence(db)
        names = _creative_names(db)

        # creative 级证据 = 首个变体证据（生产判定口径）
        creative_ev: dict[str, PairEvidence] = {}
        for _vid, (cid, ev) in sorted(variant_ev.items()):
            creative_ev.setdefault(cid, ev)

        tasks: list[tuple[str, str, str, PairEvidence, PairEvidence]] = []
        for key, va, vb in _same_gold(db):
            if va in variant_ev and vb in variant_ev:
                label = names.get(variant_ev[va][0], "?")
                tasks.append(
                    (key, "same", label, variant_ev[va][1], variant_ev[vb][1])
                )
        for key, ca, cb in _diff_gold():
            if ca in creative_ev and cb in creative_ev:
                label = f"{names.get(ca, '?')} ↔ {names.get(cb, '?')}"
                tasks.append(
                    (key, "diff", label, creative_ev[ca], creative_ev[cb])
                )
        if limit:
            tasks = tasks[:limit]
        print(f"gold pairs: {len(tasks)}（same / diff 见报告）", flush=True)
    finally:
        db.close()

    results = []
    started = time.time()
    # v4：指纹卡按证据哈希缓存——146 对里同素材重复出现，缓存省一半以上
    # LLM 调用（限流环境下是关键成本项）
    with BacktestRunner(config, max_llm_calls=args.max_llm_calls) as runner:
        with ThreadPoolExecutor(max_workers=_CONCURRENCY) as pool:
            for i, r in enumerate(pool.map(runner.run, tasks), 1):
                results.append(r)
                if i % 10 == 0 or i == len(tasks):
                    print(f"progress {i}/{len(tasks)}", flush=True)

    results_path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in results),
        encoding="utf-8",
    )

    # ---- 报告 ----
    # not_run（预算耗尽未跑）不入任何分母
    judged = [r for r in results if r["status"] != STATUS_NOT_RUN]
    by_gold = Counter((r["gold"], r["relation"]) for r in judged)
    same_total = sum(1 for r in judged if r["gold"] == "same")
    diff_total = sum(1 for r in judged if r["gold"] == "diff")
    judged_diff = [r for r in judged if r["relation"] == "different_concept"]
    true_diff = [r for r in judged_diff if r["gold"] == "diff"]
    precision = len(true_diff) / len(judged_diff) if judged_diff else None
    precision_lb = _wilson_lower(len(true_diff), len(judged_diff)) if judged_diff else 0.0
    recall = len(true_diff) / diff_total if diff_total else None
    same_wrong = [r for r in judged
                  if r["gold"] == "same" and r["relation"] == "different_concept"]

    summary = summarize_results(results)
    recheck = summarize_recheck(runner.recheck_stats)
    total_pairs = same_total + diff_total
    grey_count = summary["grey"]
    diff_same = by_gold.get(("diff", "same_variant_family"), 0)
    lines = [
        "# B2 回测报告：merge-judge-v4.1 + harness v4.2（开发诊断 gold）",
        "",
        f"- 时间：{time.strftime('%Y-%m-%d %H:%M:%S')}；耗时 {time.time() - started:.0f}s",
        f"- 模型：{config.vision_model}；prompt 见 reason_params.prompt_version",
        "- harness v4.2：灰区=语义灰区（复核推翻/skeleton_new）；基础设施失败重试耗尽归"
        " error；预算耗尽归 not_run。error 保留在 gold 总数并单列，not_run 不入分母",
        f"- 样本：unique pair {summary['unique_pairs']}（same gold {same_total} 对"
        f" / diff gold {diff_total} 对；not_run {summary['not_run']} 对未判）",
        f"- complete_json 调用：{runner.llm_calls} 次（非底层 HTTP 次数；缓存命中不计"
        + (f"；预算上限 {args.max_llm_calls}" if args.max_llm_calls else "；无预算上限") + "）",
        "",
        "## 本轮诊断指标（旧报告只作历史参照，不自动拼成同集对比）",
        "",
        "| 指标 | 本轮 v4.1 |",
        "|---|---|",
        f"| 判异精度 | {f'{precision:.2%}' if precision is not None else 'n/a'}"
        f"（n={len(judged_diff)}） |",
        f"| 语义灰区率 | {f'{grey_count / total_pairs:.1%}' if total_pairs else 'n/a'}"
        f"（{grey_count}/{total_pairs}；error {summary['error']} 另列） |",
        f"| 同侧错判异 | {len(same_wrong)}/{same_total} |",
        f"| 异侧漏并（diff 判 same） | {diff_same}/{diff_total} |",
        "",
        "## 判异复核闸",
        "",
        f"- 复核调用 {recheck['total']} 次：确认 {recheck['confirmed']} /"
        f" 推翻 {recheck['overturned']} / 调用失败 {recheck['failed']}（单列，"
        f"禁止并入推翻、禁止计入推翻率分母）",
        (
            f"- **复核推翻率**（推翻 ÷ 有结论复核 {recheck['decided']}）："
            f"{recheck['overturn_rate']:.1%}"
            f"（{recheck['overturned']}/{recheck['decided']}）"
            if recheck["overturn_rate"] is not None
            else "- 本轮无复核结论（confirmed=overturned=0），推翻率 n/a"
        ),
        "",
        "## 混淆矩阵（gold × 判定，not_run 未判不入表）",
        "",
        "| gold \\ 判定 | same_variant_family | different_concept | grey | error |",
        "|---|---|---|---|---|",
    ]
    for gold in ("same", "diff"):
        row = [by_gold.get((gold, rel), 0) for rel in (
            "same_variant_family", "different_concept", STATUS_GREY, STATUS_ERROR,
        )]
        lines.append(f"| {gold} | " + " | ".join(str(x) for x in row) + " |")
    lines += [
        "",
        "## 判异指标",
        "",
        f"- 判异精度（different_concept 中真不同）："
        f"{f'{precision:.2%}' if precision is not None else 'n/a（无判异输出）'}"
        f"，Wilson 单侧 95% 下界 {precision_lb:.2%}（n={len(judged_diff)}）",
        "- Wilson 区间未处理同族对相关性，只作描述，不能单独作为自动放行证据",
        f"- 判异召回（diff gold 中被判异）："
        f"{f'{recall:.2%}' if recall is not None else 'n/a'}"
        f"（分母受 gold 选择偏差影响，见 docstring）",
        f"- **同侧错判异（最严重错误）**：{len(same_wrong)} 对"
        f"（same gold 来自当前分组派生，可能已被污染，只作开发诊断）",
        "",
        "## 错误明细",
    ]
    errors = [r for r in judged
              if (r["gold"] == "same" and r["relation"] == "different_concept")
              or (r["gold"] == "diff" and r["relation"] == "same_variant_family")]
    if errors:
        for r in errors:
            contested = "（**争议 gold：现分组可能已被污染，待人工裁定**）" if (
                r["gold"] == "same"
            ) else "（**疑似漏并：人工判异但 AI 判同族**）"
            lines.append(
                f"- {r['label']} gold={r['gold']} 判={r['relation']}"
                f"{contested} :: {r['reason'][:150]}"
            )
    else:
        lines.append("（无）")

    # ---- 灰区分流 + skeleton_new 提案（从卡片缓存回收）----
    grey_rows = [r for r in judged if r["status"] == STATUS_GREY]
    grey_by_reason = Counter(r["abstain"] for r in grey_rows)
    new_skeleton_cards = [
        c for c in runner.card_cache.values() if c is not None and c.skeleton_new
    ]
    failed_cards = sum(1 for c in runner.card_cache.values() if c is None)
    lines += [
        "",
        "## 灰区分流",
        "",
        f"- 语义灰区对总数：{len(grey_rows)}（按因："
        + (", ".join(f"{k} {v}" for k, v in sorted(grey_by_reason.items()))
           or "无") + "）",
        f"- 基础设施失败（重试 {runner.max_attempts} 轮后仍失败，记 error 不归灰区）："
        f"{summary['error']} 对",
        f"- 未跑（预算 not_run）：{summary['not_run']} 对",
        f"- 打卡失败残留卡：{failed_cards} 张（error/not_run 对的失败缓存）",
        "",
        "## skeleton_new 提案（库外新结构，人工命名候选）",
        "",
    ]
    proposals = Counter(
        f"{c.family}/{c.track}: {c.skeleton_new}" for c in new_skeleton_cards
    )
    if proposals:
        for proposal, count in proposals.most_common():
            lines.append(f"- {proposal} ×{count}")
    else:
        lines.append("（无）")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nreport → {report_path}\nresults → {results_path}", flush=True)
    print("\n".join(lines[:22]), flush=True)


if __name__ == "__main__":
    main()
