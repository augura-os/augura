"""vNext 归族离线实验 runner（prepare/freeze/sample/run/report/diagnose）。

所有数据输出只写 ``--workdir``（默认 /tmp/merge_family_vnext），不触碰任何
业务 upsert/merge/graph 写接口，不写业务数据库/Neo4j。除 run 外所有子命令
零 LLM 调用（diagnose 为纯本地重算）；run 是唯一调用真实 provider 的子命令，
真实请求硬上限 100（含 temperature 内部重试），达到上限当前/剩余对一律标
not_run，绝不冒充灰区。report 只做离线汇总，不调用模型或数据库。

子命令：

- prepare  按 --creatives 指定的两组目标 creative（合计 13 项）生成
           review_universe.json 供人工确认家族划分。不含任何 AI 判定；
           文件已存在时拒绝覆盖。
- freeze   校验 13 项全部人工 confirmed 后冻结 gold：生成 78 对 gold_manifest.json
           （同族 same / 异族 diff），记录 product_scope、来源、时间与证据快照哈希。
           文件已存在时拒绝覆盖。
- sample   固定 seed=20261026，在查看任何模型输出前按 gold × family pair ×
           ending relation 分层抽取 20 对（same/diff 各尽量 10，不足补齐并披露），
           写 sample20.json。文件已存在时拒绝覆盖。
- run      读取 gold_manifest.json + sample20.json（校验 gold_manifest_hash 绑定与
           证据哈希一致性），为涉及素材预打 baseline/vNext 两套卡（共享缓存），
           逐对跑生产 judge_pair（基线）与 build/match/differences/adjudicate
           （vNext，盲态两步复判），结果原子写 results.jsonl 并记 run_metadata.json。
           语义 grey 绝不重试；调用失败（None）最多 3 次尝试（0/20/40s）；空证据
           不重试、不调 LLM。缓存键含模型与 prompt/taxonomy 版本（基线卡另含
           骨架库内容哈希），失败不缓存。真实请求经 request_ledger.json 跨进程
           累计记账（每次 create 先记账再发 HTTP），硬上限 100 崩溃也不超。
- diagnose 接收 v4/v4.1 旧回测 JSONL 路径，零 LLM 重算 gold×relation、有害错误、
           灰区迁移与 label 集中度，并基于保留 cards 做明确命名的规则消融。
           全部结果标注 development/contested，不构成正式测试结论。
- report  纯汇总 run 产物（零 LLM/零 DB）：只计入与当前 sample/model/versions/
           gold/证据哈希一致的结果（error/not_run 保留在分母口径内，缺失/上下文
           失效单独披露），输出 pilot_metrics.json + pilot_report.md；
           非 78/78 全判时 B³/LEA/ARI 一律未验证，独立家族不足时 bootstrap 明确
           返回 insufficient_independent_families，不做任何成功声明。

Usage（在 apps/api 目录下）：

    python -m scripts.run_merge_family_experiment prepare [--workdir DIR]
    python -m scripts.run_merge_family_experiment freeze [--workdir DIR]
    python -m scripts.run_merge_family_experiment sample [--workdir DIR]
    python -m scripts.run_merge_family_experiment run [--workdir DIR]
    python -m scripts.run_merge_family_experiment report [--workdir DIR]
    python -m scripts.run_merge_family_experiment diagnose --v4 X.jsonl --v4-1 Y.jsonl \
        [--workdir DIR]
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import os
import random
import sys
import time
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:
    fcntl = None
try:
    import msvcrt
except ImportError:
    msvcrt = None

from sqlalchemy import bindparam, text

from app.config import get_settings
from app.database import SessionLocal
from app.services import fingerprint_card, ip_pack, llm_judge, merge_judge
from app.services.merge_family_experiment import (
    ENDING_VALUES,
    EXPERIMENT_VERSION,
    TAXONOMY_VERSION,
    ExperimentCard,
    RequestBudget,
    adjudicate_differences,
    build_experiment_card,
    generate_differences,
    match_experiment_cards,
    pair_cache_key,
    versioned_cache_key,
)
from app.services.merge_judge import PairEvidence, evidence_from_analysis, judge_pair
from app.services.settings import resolve_ai_config

# --- 实验常量 -------------------------------------------------------------

# 实验协议固定为两组、合计 13 项；具体名称仅由外部 --creatives JSON 提供。
EXPECTED_UNIVERSE_SIZE = 13
EXPECTED_PAIR_COUNT = EXPECTED_UNIVERSE_SIZE * (EXPECTED_UNIVERSE_SIZE - 1) // 2

SEED = 20261026  # 抽样在查看模型输出前完成；种子固定保证可复现
SAMPLE_TARGET_TOTAL = 20
SAMPLE_TARGET_PER_GOLD = 10

DEFAULT_WORKDIR = Path("/tmp/merge_family_vnext")

REVIEW_UNIVERSE_NAME = "review_universe.json"
GOLD_MANIFEST_NAME = "gold_manifest.json"
SAMPLE_NAME = "sample20.json"
DIAGNOSE_JSON_NAME = "diagnose_report.json"
DIAGNOSE_MD_NAME = "diagnose_report.md"

# 旧回测 JSONL 的 relation 取值空间（judge_backtest 输出）。
BACKTEST_RELATIONS = ("same_variant_family", "different_concept", "grey", "error")

# --- run 协议常量 -----------------------------------------------------------
# 真实 provider 请求硬上限（含 complete_json 内部 temperature 重试、卡片与复判）。
REQUEST_BUDGET_LIMIT = 100
# 调用失败（None/异常）最多 3 次尝试，间隔 0s/20s/40s；语义 grey 绝不重试。
MAX_CALL_FAILURE_ATTEMPTS = 3
# 离线兼容基线的输出预算；生产 fingerprint_card 默认仍为 1200。
BASELINE_CARD_MAX_TOKENS = 2400
# 终态：可复用、可入质量分母（not_run/error 不算成功结果，重跑会重试）。
TERMINAL_STATUSES = ("same", "diff", "grey")

RESULTS_NAME = "results.jsonl"
RUN_METADATA_NAME = "run_metadata.json"
BASELINE_CARD_CACHE_NAME = "cache_baseline_cards.json"
VNEXT_CARD_CACHE_NAME = "cache_vnext_cards.json"
# vNext 复判两步的版本化成功缓存（第一阶段 differences 按无序证据对键，
# 第二阶段 adjudication 按 differences 内容键；调用失败不缓存）。
DIFFERENCES_CACHE_NAME = "cache_differences.json"
ADJUDICATIONS_CACHE_NAME = "cache_adjudications.json"
# 跨进程请求账本：consume 后立即原子落盘（先记账再发 HTTP），崩溃也不超上限。
REQUEST_LEDGER_NAME = "request_ledger.json"

_ANALYSIS_COLS = (
    "summary", "hook", "conflict", "gameplay", "reward",
    "characters", "environment", "emotion", "tags", "variant_factors",
)

_ENDING_NEUTRAL = ("unknown", "none")


class ExperimentError(RuntimeError):
    """用户可读的实验流程错误（缺项、未确认、文件已存在等）。"""


# --- 通用小工具 -----------------------------------------------------------


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ExperimentError(f"缺少输入文件：{path}（请先运行前置子命令）") from exc
    except json.JSONDecodeError as exc:
        raise ExperimentError(f"{path} 不是合法 JSON：{exc}") from exc


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def write_json_exclusive(path: Path, data: Any) -> None:
    if path.exists():
        raise ExperimentError(
            f"{path} 已存在，拒绝覆盖（可能含人工编辑或已冻结数据）。"
            f"请人工确认后移走或删除该文件再重跑。"
        )
    write_json(path, data)


def run_query(db, sql: str, params: dict[str, Any] | None = None) -> list:
    """只读 SELECT 守护：本脚本只允许以 select 开头的语句。"""
    if not " ".join(sql.split()).lower().startswith("select"):
        raise ExperimentError(f"只允许只读 SELECT，收到：{sql[:60]!r}")
    return db.execute(text(sql), params or {}).fetchall()


def load_backtest_jsonl(path: Path) -> list[dict[str, Any]]:
    """读取旧回测 JSONL；每行须为含 key/gold/relation 的 object。"""
    if not path.exists():
        raise ExperimentError(f"回测明细文件不存在：{path}")
    rows: list[dict[str, Any]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ExperimentError(f"{path}:{lineno} 不是合法 JSON：{exc}") from exc
        if not isinstance(row, dict) or "key" not in row or "relation" not in row:
            raise ExperimentError(f"{path}:{lineno} 缺少 key/relation 字段")
        rows.append(row)
    if not rows:
        raise ExperimentError(f"{path} 没有任何数据行")
    return rows


# --- 纯函数：复核宇宙校验与 gold 生成（可单测，无 I/O） --------------------


def universe_errors(data: Any) -> list[str]:
    """review_universe 的冻结前校验；返回全部错误（空 list = 通过）。"""
    if not isinstance(data, dict):
        return ["review_universe.json 顶层必须是 JSON object"]
    items = data.get("items")
    if not isinstance(items, list):
        return ["review_universe.json 缺少 items list"]
    errors: list[str] = []
    if len(items) != EXPECTED_UNIVERSE_SIZE:
        errors.append(
            f"items 应为 {EXPECTED_UNIVERSE_SIZE} 项，实际 {len(items)} 项；"
            f"未确认/新增项不得进入 gold"
        )
    if data.get("product_scope") != "single_product_user_confirmed":
        errors.append(
            f"product_scope={data.get('product_scope')!r}，"
            f"应为 'single_product_user_confirmed'"
        )
    seen: set[str] = set()
    seen_review_ids: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"items[{index}] 不是 object")
            continue
        label = item.get("variant_id") or f"#{index}"
        for field in ("variant_id", "creative_id", "asset_id"):
            if not str(item.get(field) or "").strip():
                errors.append(f"{label}: 缺少 {field}")
        variant_id = str(item.get("variant_id") or "")
        if variant_id:
            if variant_id in seen:
                errors.append(f"{label}: variant_id 重复")
            seen.add(variant_id)
        review_id = str(item.get("review_id") or "").strip()
        if not review_id:
            errors.append(f"{label}: 缺少 review_id")
        elif review_id in seen_review_ids:
            errors.append(f"{label}: review_id {review_id} 重复")
        else:
            seen_review_ids.add(review_id)
        if item.get("confirmation_status") != "confirmed":
            errors.append(
                f"{label}: confirmation_status="
                f"{item.get('confirmation_status')!r}，应为 'confirmed'"
            )
        if not str(item.get("rationale") or "").strip():
            errors.append(f"{label}: rationale 为空（人工确认须留依据）")
        if not str(item.get("confirmed_family_id") or "").strip():
            errors.append(f"{label}: confirmed_family_id 为空")
        ending = str(item.get("confirmed_ending") or "").strip()
        if ending not in ENDING_VALUES:
            errors.append(
                f"{label}: confirmed_ending={ending!r} 非法"
                f"（合法值：{'/'.join(ENDING_VALUES)}）"
            )
    return errors


def build_gold_pairs(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """13 项两两组合（无序、去重）：同确认家族 → same，否则 → diff。"""
    ordered = sorted(items, key=lambda item: str(item["variant_id"]))
    pairs: list[dict[str, Any]] = []
    for item_a, item_b in itertools.combinations(ordered, 2):
        family_a = str(item_a["confirmed_family_id"]).strip()
        family_b = str(item_b["confirmed_family_id"]).strip()
        pairs.append({
            "pair_id": f"pair:{item_a['variant_id']}:{item_b['variant_id']}",
            "variant_a": item_a["variant_id"],
            "variant_b": item_b["variant_id"],
            "creative_a": item_a.get("creative_name", ""),
            "creative_b": item_b.get("creative_name", ""),
            "family_a": family_a,
            "family_b": family_b,
            "ending_a": str(item_a["confirmed_ending"]).strip(),
            "ending_b": str(item_b["confirmed_ending"]).strip(),
            "gold": "same" if family_a == family_b else "diff",
        })
    return pairs


def pair_stratum(pair: dict[str, Any]) -> str:
    """分层键 = gold : family pair（same 用单家族，diff 用排序家族对）: ending relation。"""
    if pair["gold"] == "same":
        family_key = str(pair["family_a"])
    else:
        family_key = "|".join(sorted((str(pair["family_a"]), str(pair["family_b"]))))
    ending_rel = (
        "same_ending"
        if pair["ending_a"] == pair["ending_b"]
        else "diff_ending"
    )
    return f"{pair['gold']}:{family_key}:{ending_rel}"


def _round_robin_take(
    strata: list[list[dict[str, Any]]], quota: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """跨层轮流取对（层内顺序已由调用方用固定种子洗过），直到配额或池尽。"""
    picked: list[dict[str, Any]] = []
    cursor = 0
    while len(picked) < quota and any(strata):
        bucket = strata[cursor % len(strata)]
        if bucket:
            picked.append(bucket.pop(0))
        cursor += 1
    rest = [pair for bucket in strata for pair in bucket]
    return picked, rest


def stratified_sample(
    pairs: list[dict[str, Any]],
    *,
    seed: int,
    target_total: int = SAMPLE_TARGET_TOTAL,
    target_per_gold: int = SAMPLE_TARGET_PER_GOLD,
) -> dict[str, Any]:
    """分层抽样纯函数：gold × family pair × ending relation。

    每个 gold 池内先按分层键分组，层内按 pair_id 排序后用 ``random.Random(seed)``
    洗牌，再跨层轮流取对直到该 gold 配额（默认 10）；两侧配额完成后若总数不足
    ``target_total``，用剩余对补齐（backfill）并在 disclosure 中披露。
    同层不打乱跨 gold 的顺序，结果完全由 (pairs, seed) 决定。disclosure 中
    ``selected_by_gold`` 是含 backfill 的最终计数，``stratified_by_gold`` 是
    分层配额阶段的计数，``backfilled_by_gold`` 记录补齐来源。
    """
    rng = random.Random(seed)
    by_gold: dict[str, list[dict[str, Any]]] = {"same": [], "diff": []}
    for pair in pairs:
        by_gold.setdefault(str(pair.get("gold")), []).append(pair)

    selected: list[dict[str, Any]] = []
    leftover: dict[str, list[dict[str, Any]]] = {}
    selected_by_gold: dict[str, int] = {}
    strata_coverage: dict[str, dict[str, int]] = {}
    for gold in ("same", "diff"):
        pool = by_gold.get(gold, [])
        strata: dict[str, list[dict[str, Any]]] = {}
        for pair in pool:
            strata.setdefault(pair_stratum(pair), []).append(pair)
        shuffled: list[list[dict[str, Any]]] = []
        coverage: dict[str, int] = {}
        for key in sorted(strata):
            bucket = sorted(strata[key], key=lambda p: str(p["pair_id"]))
            rng.shuffle(bucket)
            shuffled.append(bucket)
            coverage[key] = len(bucket)
        quota = min(target_per_gold, len(pool))
        picked, rest = _round_robin_take(shuffled, quota)
        selected_by_gold[gold] = len(picked)
        leftover[gold] = rest
        strata_coverage[gold] = coverage
        for rank, pair in enumerate(picked):
            selected.append({
                **pair,
                "stratum": pair_stratum(pair),
                "selection": "stratified",
                "selection_rank": rank,
            })

    stratified_by_gold = dict(selected_by_gold)
    backfilled_pair_ids: list[str] = []
    backfilled_by_gold: Counter[str] = Counter()
    while len(selected) < target_total:
        took = False
        for gold in ("same", "diff"):
            if leftover.get(gold):
                pair = leftover[gold].pop(0)
                selected.append({
                    **pair,
                    "stratum": pair_stratum(pair),
                    "selection": "backfill",
                    "selection_rank": len(backfilled_pair_ids),
                })
                backfilled_pair_ids.append(str(pair["pair_id"]))
                backfilled_by_gold[gold] += 1
                took = True
                break
        if not took:
            break

    final_by_gold = {"same": 0, "diff": 0}
    for pair in selected:
        gold = str(pair["gold"])
        final_by_gold[gold] = final_by_gold.get(gold, 0) + 1

    shortfalls = [
        f"{gold} 池仅 {len(by_gold.get(gold, []))} 对 < 目标 {target_per_gold}，已用另一侧补齐"
        for gold in ("same", "diff")
        if len(by_gold.get(gold, [])) < target_per_gold
    ]
    return {
        "selected": selected,
        "disclosure": {
            "seed": seed,
            "pool_size": {gold: len(by_gold.get(gold, [])) for gold in ("same", "diff")},
            "selected_by_gold": final_by_gold,
            "stratified_by_gold": stratified_by_gold,
            "backfilled_by_gold": dict(backfilled_by_gold),
            "backfilled_pair_ids": backfilled_pair_ids,
            "strata_coverage": strata_coverage,
            "shortfall": shortfalls or None,
        },
    }


# --- 纯函数：diagnose（零 LLM 重算） ---------------------------------------

# 命名规则消融：键为消融名，值为 ablate_match 参数。baseline 复刻生产
# match_cards 的短路顺序（skeleton None → 灰区 → family/track/skeleton/ending）。
ABLATION_PRESETS: dict[str, dict[str, Any]] = {
    "baseline_v4_1_rules": {"ignore_fields": ()},
    "no_track_split": {"ignore_fields": ("track",)},
    "no_family_split": {"ignore_fields": ("family",)},
    "no_track_family_split": {"ignore_fields": ("family", "track")},
    "no_ending_split": {"ignore_fields": ("ending",)},
    "no_track_family_ending_split": {"ignore_fields": ("family", "track", "ending")},
}


def ablate_match(
    card_a: dict[str, Any],
    card_b: dict[str, Any],
    *,
    ignore_fields: tuple[str, ...] = (),
) -> str | None:
    """match_cards 的纯函数复刻 + 可命名消融（输入为卡片 dict）。

    ignore_fields 可取 family/track/skeleton/ending 的任意组合，用于定位
    各轴的判异贡献；结局轴复刻生产行为：双方结局均非 unknown/none 且不同
    才判异，neutral 结局不与任何结局比较。返回 relation，None = 灰区。
    """
    if not isinstance(card_a, dict) or not isinstance(card_b, dict):
        return None
    skeleton_a, skeleton_b = card_a.get("skeleton"), card_b.get("skeleton")
    if "skeleton" not in ignore_fields and (skeleton_a is None or skeleton_b is None):
        return None
    if "family" not in ignore_fields and card_a.get("family") != card_b.get("family"):
        return "different_concept"
    if "track" not in ignore_fields and card_a.get("track") != card_b.get("track"):
        return "different_concept"
    if "skeleton" not in ignore_fields and skeleton_a != skeleton_b:
        return "different_concept"
    if "ending" not in ignore_fields:
        ending_a, ending_b = card_a.get("ending"), card_b.get("ending")
        if (
            ending_a not in _ENDING_NEUTRAL
            and ending_b not in _ENDING_NEUTRAL
            and ending_a != ending_b
        ):
            return "different_concept"
    return "same_variant_family"


def ablation_summary(
    rows: list[dict[str, Any]],
    presets: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """对每个消融预设重放卡片匹配，统计 relation / gold×relation / 有害错误。

    只用带 cards 的行；有害错误 = same gold 判异（over-split）+ diff gold 判同
    （over-merge）。消融产生的 grey 不计入确定判异错误（与主指标口径一致）。
    """
    summary: dict[str, Any] = {}
    for name, options in (presets or ABLATION_PRESETS).items():
        relation_counts: Counter[str] = Counter()
        gold_x_relation: dict[str, Counter[str]] = {}
        harmful = {"over_split_same_gold_to_diff": 0, "over_merge_diff_gold_to_same": 0}
        pairs_with_cards = 0
        for row in rows:
            cards = row.get("cards")
            if not isinstance(cards, dict):
                continue
            card_a, card_b = cards.get("a"), cards.get("b")
            if not isinstance(card_a, dict) or not isinstance(card_b, dict):
                continue
            pairs_with_cards += 1
            relation = ablate_match(card_a, card_b, **options) or "grey"
            relation_counts[relation] += 1
            gold = str(row.get("gold") or "unknown")
            gold_x_relation.setdefault(gold, Counter())[relation] += 1
            if gold == "same" and relation == "different_concept":
                harmful["over_split_same_gold_to_diff"] += 1
            elif gold == "diff" and relation == "same_variant_family":
                harmful["over_merge_diff_gold_to_same"] += 1
        summary[name] = {
            "options": options,
            "pairs_with_cards": pairs_with_cards,
            "relations": dict(relation_counts),
            "gold_x_relation": {gold: dict(c) for gold, c in gold_x_relation.items()},
            "harmful_errors": harmful,
        }
    return summary


def confusion_counts(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """gold × relation 重算（含未标注 gold/relation 的 unknown 桶）。"""
    counts: dict[str, Counter[str]] = {}
    for row in rows:
        gold = str(row.get("gold") or "unknown")
        relation = str(row.get("relation") or "unknown")
        counts.setdefault(gold, Counter())[relation] += 1
    return {gold: dict(counter) for gold, counter in counts.items()}


def harmful_error_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    """有害判定：gold same 判异（over-split）+ gold diff 判同（over-merge）。"""
    return {
        "over_split_same_gold_to_diff": sum(
            1 for row in rows
            if row.get("gold") == "same" and row.get("relation") == "different_concept"
        ),
        "over_merge_diff_gold_to_same": sum(
            1 for row in rows
            if row.get("gold") == "diff" and row.get("relation") == "same_variant_family"
        ),
    }


def relation_transitions(
    before: list[dict[str, Any]], after: list[dict[str, Any]],
) -> dict[str, Any]:
    """按 key 关联两版明细，重算 relation 迁移与灰区进出（v4 → v4.1）。"""
    before_idx = {str(row["key"]): row for row in before}
    after_idx = {str(row["key"]): row for row in after}
    common = sorted(set(before_idx) & set(after_idx))
    transitions: Counter[str] = Counter()
    into_grey: list[str] = []
    out_of_grey: list[str] = []
    for key in common:
        rel_before = before_idx[key].get("relation")
        rel_after = after_idx[key].get("relation")
        transitions[f"{rel_before}->{rel_after}"] += 1
        if rel_before != "grey" and rel_after == "grey":
            into_grey.append(key)
        elif rel_before == "grey" and rel_after != "grey":
            out_of_grey.append(key)
    return {
        "common_pairs": len(common),
        "only_in_v4": sorted(set(before_idx) - set(after_idx)),
        "only_in_v4_1": sorted(set(after_idx) - set(before_idx)),
        "transitions": dict(transitions),
        "into_grey": into_grey,
        "out_of_grey": out_of_grey,
    }


def label_concentration(
    rows: list[dict[str, Any]],
    *,
    gold: str | None = None,
    relations: tuple[str, ...] | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """按回测 label（creative 名）统计集中度，返回 [{label, count, share}]。"""
    counter: Counter[str] = Counter()
    total = 0
    for row in rows:
        if gold is not None and row.get("gold") != gold:
            continue
        if relations is not None and row.get("relation") not in relations:
            continue
        counter[str(row.get("label") or "unknown")] += 1
        total += 1
    return [
        {"label": label, "count": count, "share": (count / total) if total else 0.0}
        for label, count in counter.most_common(limit)
    ]


# --- 子命令：prepare -------------------------------------------------------


def _target_creatives(args: argparse.Namespace) -> list[tuple[str, int]]:
    path = getattr(args, "creatives", None)
    if path is None:
        raise ExperimentError("prepare 需要 --creatives 指定两组目标素材的 JSON 配置")
    data = load_json(path)
    if not isinstance(data, list) or len(data) != 2:
        raise ExperimentError("--creatives 必须是恰好两组的 JSON 数组")
    targets: list[tuple[str, int]] = []
    seen: set[str] = set()
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise ExperimentError(f"--creatives[{index}] 必须是 object")
        name = str(item.get("name") or "").strip()
        count = item.get("expected_variants")
        if not name:
            raise ExperimentError(f"--creatives[{index}] 缺少 name")
        if name in seen:
            raise ExperimentError(f"creative 名称重复：{name}")
        if not isinstance(count, int) or isinstance(count, bool) or count < 2:
            raise ExperimentError(f"{name}: expected_variants 必须是 ≥2 的整数")
        seen.add(name)
        targets.append((name, count))
    if sum(count for _, count in targets) != EXPECTED_UNIVERSE_SIZE:
        raise ExperimentError(f"两组 expected_variants 合计必须为 {EXPECTED_UNIVERSE_SIZE}")
    return targets


def cmd_prepare(args: argparse.Namespace) -> int:
    workdir: Path = args.workdir
    targets = _target_creatives(args)
    out_path = workdir / REVIEW_UNIVERSE_NAME
    if out_path.exists():
        raise ExperimentError(
            f"{out_path} 已存在，拒绝覆盖（可能已有人工确认进度）。"
            f"请人工确认后移走或删除该文件再重跑 prepare。"
        )
    workdir.mkdir(parents=True, exist_ok=True)

    names = [name for name, _ in targets]
    expected = dict(targets)
    stmt = text(
        "select c.id, c.name, v.id, v.name, a.id, a.filename, a.storage_key "
        "from creatives c "
        "join creative_variants v on v.creative_id = c.id "
        "join creative_assets a on a.id = v.asset_id "
        "where c.name in :names "
        "order by c.name, v.created_at, v.id"
    ).bindparams(bindparam("names", expanding=True))

    db = SessionLocal()
    try:
        rows = db.execute(stmt, {"names": names}).fetchall()
    finally:
        db.close()

    by_creative: dict[str, list[tuple[Any, ...]]] = {}
    for row in rows:
        by_creative.setdefault(str(row[1]), []).append(row)
    missing = [name for name in names if name not in by_creative]
    if missing:
        raise ExperimentError(
            f"prepare 未在 creatives 中找到目标 creative：{', '.join(missing)}"
        )
    count_errors = [
        f"{name}: 期望 {expected[name]} 个变体，实际 {len(by_creative[name])} 个"
        for name in names
        if len(by_creative[name]) != expected[name]
    ]
    if count_errors:
        raise ExperimentError("prepare 复核宇宙缺项/多项：\n- " + "\n- ".join(count_errors))

    items: list[dict[str, Any]] = []
    for name in names:
        for row in by_creative[name]:
            items.append({
                "review_id": f"S{len(items) + 1:02d}",
                "variant_id": str(row[2]),
                "creative_id": str(row[0]),
                "asset_id": str(row[4]),
                "creative_name": name,
                "variant_name": str(row[3]),
                "filename": str(row[5]),
                "storage_key": str(row[6]),
                "media_url": f"http://localhost:8000/assets/{row[4]}/media",
                "confirmed_family_id": "",
                "confirmed_ending": "",
                "confirmation_status": "pending",
                "rationale": "",
            })
    if len(items) != EXPECTED_UNIVERSE_SIZE:
        raise ExperimentError(
            f"prepare 生成 {len(items)} 项，期望 {EXPECTED_UNIVERSE_SIZE} 项，已中止"
        )

    universe = {
        "schema_version": 1,
        "kind": "review_universe",
        "generated_at": utc_now_iso(),
        "product_scope": "single_product_user_confirmed",
        "product_scope_note": (
            "当前实例经用户确认为单产品实例；家族口径 = 同产品 + 同主体骨架 + "
            "同结局（结局不同拆族，旧整簇结论不自动沿用）。"
        ),
        "editing_instructions": (
            "人工逐项确认：同族项填相同 confirmed_family_id（如 F1、F2）；"
            "confirmed_ending 取 success|fail|twist-up|twist-down|open|none|unknown；"
            "补 rationale；把 confirmation_status 改为 confirmed。"
            "本文件不含任何 AI 判定，仅作人工复核清单。"
        ),
        "items": items,
    }
    write_json_exclusive(out_path, universe)
    print(f"prepare 完成：{len(items)} 项复核清单 → {out_path}")
    print("请人工确认家族划分后运行 freeze。")
    return 0


# --- 子命令：freeze ---------------------------------------------------------


def _evidence_snapshot(db, items: list[dict[str, Any]]) -> dict[str, Any]:
    """逐变体取自己 asset 的首个分析（created_at 最早）并哈希证据文本。"""
    snapshot: dict[str, Any] = {}
    missing: list[str] = []
    for item in items:
        rows = run_query(
            db,
            "select id, created_at, " + ", ".join(_ANALYSIS_COLS)
            + " from analysis_results where asset_id = :asset_id "
            "order by created_at, id limit 1",
            {"asset_id": item["asset_id"]},
        )
        if not rows:
            missing.append(f"{item['variant_id']}（asset {item['asset_id']} 无分析）")
            continue
        row = rows[0]
        analysis = dict(zip(_ANALYSIS_COLS, row[2:], strict=True))
        full_text = evidence_from_analysis(analysis).full_text()
        if not full_text:
            missing.append(f"{item['variant_id']}（首个分析证据为空）")
            continue
        created_at = row[1]
        snapshot[str(item["variant_id"])] = {
            "asset_id": item["asset_id"],
            "analysis_id": str(row[0]),
            "analysis_created_at": (
                created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at)
            ),
            "evidence_hash": sha256_text(full_text),
        }
    if missing:
        raise ExperimentError("freeze 证据快照缺项：\n- " + "\n- ".join(missing))
    return snapshot


def cmd_freeze(args: argparse.Namespace) -> int:
    workdir: Path = args.workdir
    universe_path = workdir / REVIEW_UNIVERSE_NAME
    if not universe_path.exists():
        raise ExperimentError(f"缺少 {universe_path}，请先运行 prepare")
    manifest_path = workdir / GOLD_MANIFEST_NAME
    if manifest_path.exists():
        raise ExperimentError(
            f"{manifest_path} 已存在，拒绝覆盖（gold 已冻结）。"
            f"如确需重新冻结，请先人工删除该文件。"
        )

    universe = load_json(universe_path)
    errors = universe_errors(universe)
    if errors:
        raise ExperimentError(
            "review_universe 未全部 confirmed，freeze 中止：\n- " + "\n- ".join(errors)
        )
    items = universe["items"]

    db = SessionLocal()
    try:
        snapshot = _evidence_snapshot(db, items)
    finally:
        db.close()

    pairs = build_gold_pairs(items)
    if len(pairs) != EXPECTED_PAIR_COUNT:
        raise ExperimentError(
            f"gold 对数 {len(pairs)} != 期望 {EXPECTED_PAIR_COUNT}，freeze 中止"
        )
    snapshot_hash = sha256_text(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
    )
    manifest = {
        "schema_version": 1,
        "kind": "gold_manifest",
        "frozen_at": utc_now_iso(),
        "product_scope": "single_product_user_confirmed",
        "confirmation_source": "user_family_review",
        "review_universe_hash": sha256_text(universe_path.read_text(encoding="utf-8")),
        "universe_size": len(items),
        "evidence_snapshot_hash": snapshot_hash,
        "evidence_snapshot": snapshot,
        "items": [
            {
                "variant_id": item["variant_id"],
                "creative_id": item["creative_id"],
                "asset_id": item["asset_id"],
                "confirmed_family_id": str(item["confirmed_family_id"]).strip(),
                "confirmed_ending": str(item["confirmed_ending"]).strip(),
                "rationale": item.get("rationale", ""),
            }
            for item in items
        ],
        "pairs": pairs,
        "pair_count": len(pairs),
        "same_pair_count": sum(1 for pair in pairs if pair["gold"] == "same"),
        "diff_pair_count": sum(1 for pair in pairs if pair["gold"] == "diff"),
    }
    write_json_exclusive(manifest_path, manifest)
    print(
        f"freeze 完成：{len(pairs)} 对 gold"
        f"（same {manifest['same_pair_count']} / diff {manifest['diff_pair_count']}）"
        f" → {manifest_path}"
    )
    print(f"证据快照哈希：{snapshot_hash}")
    return 0


# --- 子命令：sample ---------------------------------------------------------


def cmd_sample(args: argparse.Namespace) -> int:
    workdir: Path = args.workdir
    manifest_path = workdir / GOLD_MANIFEST_NAME
    if not manifest_path.exists():
        raise ExperimentError(f"缺少 {manifest_path}，请先运行 prepare + freeze")
    out_path = workdir / SAMPLE_NAME
    if out_path.exists():
        raise ExperimentError(
            f"{out_path} 已存在，拒绝覆盖（抽样已在查看模型输出前冻结）。"
            f"如确需重抽，请先人工删除该文件。"
        )

    manifest = load_json(manifest_path)
    pairs = manifest.get("pairs")
    if not isinstance(pairs, list) or len(pairs) != EXPECTED_PAIR_COUNT:
        raise ExperimentError(
            f"gold_manifest 对数非法：期望 {EXPECTED_PAIR_COUNT}，"
            f"实际 {len(pairs) if isinstance(pairs, list) else 'n/a'}"
        )
    result = stratified_sample(pairs, seed=SEED)

    sample_doc = {
        "schema_version": 1,
        "kind": "sample20",
        "sampled_at": utc_now_iso(),
        "gold_manifest_hash": sha256_text(manifest_path.read_text(encoding="utf-8")),
        "protocol_note": (
            "抽样在查看任何模型输出前完成，seed 固定可复现；20 对是分层实验样本，"
            "不代表真实候选流分布。"
        ),
        "target_total": SAMPLE_TARGET_TOTAL,
        "target_per_gold": SAMPLE_TARGET_PER_GOLD,
        "selected": result["selected"],
        "disclosure": result["disclosure"],
    }
    write_json_exclusive(out_path, sample_doc)
    disc = result["disclosure"]
    print(
        f"sample 完成：{len(result['selected'])} 对"
        f"（same {disc['selected_by_gold'].get('same', 0)}"
        f" / diff {disc['selected_by_gold'].get('diff', 0)}"
        f" / backfill {len(disc['backfilled_pair_ids'])}）→ {out_path}"
    )
    if disc["shortfall"]:
        print("披露：" + "；".join(disc["shortfall"]))
    return 0


# --- 子命令：run -------------------------------------------------------------

# run 只写 workdir；LLM 走 app.services.llm_judge._client（包装预算计数），
# 生产 judge_pair / 实验 pipeline 均复用该入口，因此 100 次硬上限覆盖全部
# 真实请求（含 complete_json 内部 temperature 重试）。


def _budget_ok(budget: RequestBudget) -> bool:
    """预算耗尽 = not_run（绝不冒充 grey）；调用前必须检查。"""
    return budget.used < budget.limit


def _load_request_ledger(path: Path, expected_limit: int = REQUEST_BUDGET_LIMIT) -> int:
    """读取跨进程请求账本，返回累计 used；文件缺失按 0 起算。

    账本格式/范围校验失败一律报错（不静默重置，避免悄悄放大预算）。
    """
    if not path.exists():
        return 0
    data = load_json(path)
    if not isinstance(data, dict):
        raise ExperimentError(f"{path} 格式非法（应为 JSON object）")
    used = data.get("used")
    limit = data.get("limit")
    if not isinstance(used, int) or isinstance(used, bool) or used < 0:
        raise ExperimentError(f"{path} 的 used 非法：{used!r}（账本不可信，请人工处理）")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        raise ExperimentError(f"{path} 的 limit 非法：{limit!r}（账本不可信，请人工处理）")
    if used > limit:
        raise ExperimentError(
            f"{path} 的 used({used}) > limit({limit})（账本不可信，请人工处理）"
        )
    if limit != expected_limit:
        raise ExperimentError(
            f"{path} 的 limit={limit} 与当前协议上限 {expected_limit} 不一致；"
            f"如需变更预算请先获得用户确认并更换 workdir"
        )
    return used


def _save_request_ledger(path: Path, used: int, limit: int = REQUEST_BUDGET_LIMIT) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, {
        "schema_version": 1,
        "kind": "request_ledger",
        "limit": limit,
        "used": used,
        "updated_at": utc_now_iso(),
    })


class _BudgetedCompletions:
    """拦截 chat.completions.create：每次真实 create 之前 consume 预算。

    consume 在 HTTP 请求发出前触发——请求无论成败都计数；记账成功后立即
    原子写 request_ledger.json 再发 HTTP，进程崩溃也保守不超过硬上限；
    预算耗尽时 RequestBudgetExceeded 在请求前抛出，不会多烧真实调用。
    """

    def __init__(self, real: Any, budget: RequestBudget, ledger_path: Path) -> None:
        self._real = real
        self._budget = budget
        self._ledger_path = ledger_path

    def create(self, *args: Any, **kwargs: Any) -> Any:
        self._budget.consume()
        _save_request_ledger(self._ledger_path, self._budget.used, self._budget.limit)
        return self._real.create(*args, **kwargs)


class _BudgetedChat:
    def __init__(self, real: Any, budget: RequestBudget, ledger_path: Path) -> None:
        self._completions = _BudgetedCompletions(real.completions, budget, ledger_path)

    @property
    def completions(self) -> _BudgetedCompletions:
        return self._completions


class _BudgetedOpenAI:
    """透传 OpenAI client，仅 chat.completions.create 被预算包装。"""

    def __init__(self, real: Any, budget: RequestBudget, ledger_path: Path) -> None:
        self._real = real
        self._chat = _BudgetedChat(real.chat, budget, ledger_path)

    @property
    def chat(self) -> _BudgetedChat:
        return self._chat

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


def _install_budget_client(budget: RequestBudget, ledger_path: Path) -> Any:
    """包装 llm_judge._client；返回原函数供 finally 恢复。"""
    original = llm_judge._client

    def factory(config: Any) -> _BudgetedOpenAI:
        return _BudgetedOpenAI(original(config), budget, ledger_path)

    llm_judge._client = factory
    return original


def _install_recheck_recorder() -> tuple[Any, dict[str, str | None]]:
    """包装 merge_judge.recheck_split，记录三态结果供 grey 归因。

    judge_pair 从模块命名空间解析 recheck_split（同 judge_backtest 的 patch
    手法），因此 patch 模块属性即覆盖生产判定路径。返回 (原函数, 记录器)。
    """
    original = merge_judge.recheck_split
    recorder: dict[str, str | None] = {"outcome": None}

    def wrapped(*args: Any, **kwargs: Any) -> Any:
        outcome = original(*args, **kwargs)
        if outcome is None:
            recorder["outcome"] = "failed"
        elif outcome.get("split_confirmed"):
            recorder["outcome"] = "confirmed"
        else:
            recorder["outcome"] = "overturned"
        return outcome

    merge_judge.recheck_split = wrapped
    return original, recorder


def _taxonomy_content_hash() -> str:
    """骨架库内容指纹（SKELETONS/FAMILY_TRACKS/PRESENTATION_TAGS 全文）。

    prompt 版本号不变不代表库内容不变（收编 skeleton_new 不改版本号），
    因此基线卡缓存键必须直接绑定分类法内容。
    """
    material = json.dumps(
        {
            "creative_families": list(ip_pack.CREATIVE_FAMILIES),
            "family_tracks": {key: list(v) for key, v in sorted(ip_pack.FAMILY_TRACKS.items())},
            "skeletons": {fam: codes for fam, codes in sorted(ip_pack.SKELETONS.items())},
            "presentation_tags": list(ip_pack.PRESENTATION_TAGS),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return sha256_text(material)


def _cache_model_namespace(config: Any) -> str:
    return f"{config.vision_model}|temperature={config.judge_temperature}"


def _baseline_card_cache_key(evidence_text: str, model: str) -> str:
    """基线卡缓存键：证据哈希 + 模型 + 卡/merge-judge 版本 + 分类法内容哈希。"""
    material = {
        "stage": "baseline_card",
        "evidence_sha256": sha256_text(evidence_text),
        "model": model,
        "card_prompt_version": ip_pack.CARD_PROMPT_VERSION,
        "merge_judge_prompt_version": ip_pack.MERGE_JUDGE_PROMPT_VERSION,
        "taxonomy_content_sha256": _taxonomy_content_hash(),
    }
    return sha256_text(json.dumps(material, ensure_ascii=False, sort_keys=True))


def _vnext_card_cache_key(evidence_text: str, model: str) -> str:
    """vNext 卡缓存键：实验 service 的 versioned_cache_key（含 experiment/taxonomy 版本）。"""
    return versioned_cache_key("experiment_card", evidence_text, model)


def _fingerprint_card_from_dict(raw: dict[str, Any]) -> fingerprint_card.FingerprintCard:
    return fingerprint_card.FingerprintCard(
        family=raw["family"],
        track=raw["track"],
        skeleton=raw.get("skeleton"),
        skeleton_new=raw.get("skeleton_new"),
        ending=raw.get("ending", "unknown"),
        loop_signature=raw.get("loop_signature", ""),
        hook_fingerprint=raw.get("hook_fingerprint", ""),
        gameplay_fingerprint=raw.get("gameplay_fingerprint"),
        presentation_tags=list(raw.get("presentation_tags") or []),
        track_candidates=[dict(c) for c in (raw.get("track_candidates") or [])],
        variant_notes=raw.get("variant_notes", ""),
        misleading=bool(raw.get("misleading", False)),
    )


def _experiment_card_from_dict(raw: dict[str, Any]) -> ExperimentCard:
    return ExperimentCard(
        content_mode=raw["content_mode"],
        game_category=raw["game_category"],
        track_candidates=list(raw.get("track_candidates") or []),
        core_skeleton=raw.get("core_skeleton"),
        core_skeleton_new=raw.get("core_skeleton_new"),
        core_evidence=raw.get("core_evidence", ""),
        ending=raw.get("ending", "unknown"),
        ending_evidence=raw.get("ending_evidence", ""),
        loop_signature=raw.get("loop_signature", ""),
        hook_fingerprint=raw.get("hook_fingerprint", ""),
        gameplay_fingerprint=raw.get("gameplay_fingerprint"),
        presentation_tags=list(raw.get("presentation_tags") or []),
        variant_notes=raw.get("variant_notes", ""),
    )


def _load_card_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = load_json(path)
    if not isinstance(data, dict):
        raise ExperimentError(f"{path} 格式非法（应为 JSON object）")
    return data


def _load_results(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    results: list[dict[str, Any]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ExperimentError(f"{path}:{lineno} 不是合法 JSON：{exc}") from exc
        if not isinstance(row, dict) or not (row.get("pair_id") or row.get("source_pair_id")):
            raise ExperimentError(f"{path}:{lineno} 缺少 pair_id/source_pair_id")
        results.append(row)
    return results


def _write_results_atomic(path: Path, results: list[dict[str, Any]]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in results),
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _record_reusable(
    record: dict[str, Any],
    pair: dict[str, Any] | None,
    model: str,
    versions: dict[str, Any],
    evidence_map: dict[str, dict[str, Any]],
) -> bool:
    """成功结果且与当前运行上下文完全一致才可复用。

    除双系统终态（same/diff/grey）外，还必须逐对校验 model_id、prompt/
    taxonomy 版本、gold、evidence_hash_a/b 与当前一致——任一变化（换模型、
    改 prompt/骨架库、证据漂移）该对都必须重跑，并受剩余预算约束。
    """
    if pair is None:
        return False
    baseline = record.get("baseline") or {}
    vnext = record.get("vnext") or {}
    if not (
        baseline.get("status") in TERMINAL_STATUSES
        and vnext.get("status") in TERMINAL_STATUSES
    ):
        return False
    variant_a = str(pair["variant_a"])
    variant_b = str(pair["variant_b"])
    return (
        record.get("model_id") == model
        and record.get("versions") == versions
        and record.get("gold") == pair.get("gold")
        and record.get("evidence_hash_a") == evidence_map[variant_a]["evidence_hash"]
        and record.get("evidence_hash_b") == evidence_map[variant_b]["evidence_hash"]
    )


def _terminal(
    status: str,
    *,
    reason: str,
    attempts: int,
    source: str = "none",
    relation: str | None = None,
    cards: dict[str, Any] | None = None,
    recheck: dict[str, Any] | None = None,
    differences: dict[str, Any] | None = None,
    decisive_axis: str = "",
) -> dict[str, Any]:
    return {
        "status": status,
        "relation": relation,
        "attempts": attempts,
        "reason": reason,
        "cards": cards,
        "recheck": recheck,
        "differences": differences,
        "decisive_axis": decisive_axis,
        "decision_source": source,
    }


def _cards_from_baseline_cache(
    text_a: str, text_b: str, cache: dict[str, Any],
) -> dict[str, Any] | None:
    card_a = cache.get(fingerprint_card.cache_key(text_a))
    card_b = cache.get(fingerprint_card.cache_key(text_b))
    if card_a is None or card_b is None:
        return None
    return {"a": card_a.to_dict(), "b": card_b.to_dict()}


def _baseline_none_cause(
    text_a: str,
    text_b: str,
    cache: dict[str, Any],
    recorder: dict[str, str | None],
) -> str:
    """judge_pair 返回 None 的归因：语义 grey（不重试）或调用失败（可重试）。"""
    if not text_a.strip() or not text_b.strip():
        return "no_evidence"
    card_a = cache.get(fingerprint_card.cache_key(text_a))
    card_b = cache.get(fingerprint_card.cache_key(text_b))
    if card_a is None or card_b is None:
        return "card_call_failed"  # 打卡调用/解析失败 → error，可有限重试
    if card_a.skeleton is None or card_b.skeleton is None:
        return "skeleton_new"  # 库外新结构 → 语义 grey，绝不重试
    relation = fingerprint_card.match_cards(card_a, card_b)
    if relation is None:
        return "match_none"  # 语义 grey
    if relation == "different_concept":
        outcome = recorder.get("outcome")
        if outcome == "failed":
            return "recheck_call_failed"  # 调用失败 → error，可重试
        if outcome == "overturned":
            return "recheck_overturned"  # 复核推翻 → 语义 grey，绝不重试
        return "unexpected_none"  # 复核确认却又返回 None → 异常，直接记 error
    return "unexpected_none"  # match 判同不可能返回 None → 异常，直接记 error


def _retry_sleep(attempt: int) -> None:
    # 尝试节奏 0s/20s/40s：第 1 次立即，失败后等 20s，再失败等 40s。
    if 0 < attempt < MAX_CALL_FAILURE_ATTEMPTS:
        time.sleep(20 * attempt)


def _exhausted_not_run(
    budget: RequestBudget, attempts: int, **extra: Any,
) -> dict[str, Any] | None:
    """LLM 失败后若预算已耗尽：立即 not_run，不再 20/40s 睡眠等一个不会来的配额。"""
    if _budget_ok(budget):
        return None
    return _terminal("not_run", reason="LLM 请求预算耗尽", attempts=attempts, **extra)


def _ensure_baseline_card(
    config: Any, budget: RequestBudget, text: str, mem_cache: dict[str, Any],
) -> tuple[Any, str, int]:
    """预打基线卡（生产 get_card 路径，含 24 位证据哈希内存键）。

    返回 (card, status, attempts)；status ∈ ok/failed/not_run。失败不缓存：
    None 条目在重试前清除，成功才由调用方写入版本化磁盘缓存。
    """
    key = fingerprint_card.cache_key(text)
    for attempt in range(MAX_CALL_FAILURE_ATTEMPTS):
        if not _budget_ok(budget):
            mem_cache.pop(key, None)
            return None, "not_run", attempt
        card = fingerprint_card.get_card(
            config, text, mem_cache, max_tokens=BASELINE_CARD_MAX_TOKENS,
        )
        if card is not None:
            return card, "ok", attempt + 1
        mem_cache.pop(key, None)  # 调用失败不缓存，重试重打
        exhausted = _exhausted_not_run(budget, attempt + 1)
        if exhausted is not None:
            return None, "not_run", attempt + 1
        _retry_sleep(attempt + 1)
    mem_cache.pop(key, None)
    return None, "failed", MAX_CALL_FAILURE_ATTEMPTS


def _ensure_vnext_card(
    config: Any, budget: RequestBudget, text: str, mem_cache: dict[str, Any],
) -> tuple[Any, str, int]:
    """预打 vNext 实验卡（build_experiment_card 路径，键含版本）。"""
    key = _vnext_card_cache_key(text, _cache_model_namespace(config))
    for attempt in range(MAX_CALL_FAILURE_ATTEMPTS):
        if not _budget_ok(budget):
            mem_cache.pop(key, None)
            return None, "not_run", attempt
        card = mem_cache.get(key)
        if card is None and key not in mem_cache:
            card = build_experiment_card(config, text)
        if card is not None:
            mem_cache[key] = card
            return card, "ok", attempt + 1
        mem_cache.pop(key, None)  # 调用失败不缓存
        exhausted = _exhausted_not_run(budget, attempt + 1)
        if exhausted is not None:
            return None, "not_run", attempt + 1
        _retry_sleep(attempt + 1)
    mem_cache.pop(key, None)
    return None, "failed", MAX_CALL_FAILURE_ATTEMPTS


def _judge_baseline_pair(
    config: Any,
    budget: RequestBudget,
    evidence_a: PairEvidence,
    evidence_b: PairEvidence,
    mem_cache: dict[str, Any],
    recorder: dict[str, str | None],
) -> dict[str, Any]:
    """生产 judge_pair 判定（共享预打卡缓存 + 复核包装）。

    语义 grey（skeleton_new/复核推翻/无证据）绝不重试；调用失败（打卡 None /
    复核 None）最多 3 次尝试（0/20/40s）。
    """
    text_a, text_b = evidence_a.full_text(), evidence_b.full_text()
    if _baseline_none_cause(text_a, text_b, mem_cache, recorder) == "no_evidence":
        return _terminal("grey", reason="证据为空，不调用 LLM", attempts=0)
    attempts = 0
    for attempt in range(MAX_CALL_FAILURE_ATTEMPTS):
        if not _budget_ok(budget):
            return _terminal("not_run", reason="LLM 请求预算耗尽", attempts=attempts)
        attempts += 1
        recorder["outcome"] = None
        try:
            judgement = judge_pair(
                config, evidence_a=evidence_a, evidence_b=evidence_b,
                card_cache=mem_cache,
            )
        except Exception as exc:  # 生产判定的未预期异常 → 按调用失败处理
            exhausted = _exhausted_not_run(budget, attempts)
            if exhausted is not None:
                return exhausted
            if attempt < MAX_CALL_FAILURE_ATTEMPTS - 1:
                _retry_sleep(attempt + 1)
                continue
            return _terminal(
                "error", reason=f"judge_pair 异常：{exc}"[:200], attempts=attempts,
            )
        if judgement is not None:
            status = "same" if judgement.relation == "same_variant_family" else "diff"
            return _terminal(
                status,
                relation=judgement.relation,
                reason=judgement.reason,
                attempts=attempts,
                source="recheck" if judgement.recheck else "rule",
                cards=judgement.cards,
                recheck=judgement.recheck,
            )
        cause = _baseline_none_cause(text_a, text_b, mem_cache, recorder)
        if cause == "card_call_failed":
            for text in (text_a, text_b):
                mem_cache.pop(fingerprint_card.cache_key(text), None)
            exhausted = _exhausted_not_run(budget, attempts)
            if exhausted is not None:
                return exhausted
            _retry_sleep(attempt + 1)
            continue
        if cause == "recheck_call_failed":
            exhausted = _exhausted_not_run(budget, attempts)
            if exhausted is not None:
                return exhausted
            _retry_sleep(attempt + 1)
            continue
        if cause == "no_evidence":
            return _terminal("grey", reason="证据为空，不调用 LLM", attempts=attempts)
        if cause == "skeleton_new":
            return _terminal(
                "grey",
                reason="skeleton_new：库外新结构，人工命名后再判（语义灰区，不重试）",
                attempts=attempts,
                cards=_cards_from_baseline_cache(text_a, text_b, mem_cache),
            )
        if cause == "recheck_overturned":
            return _terminal(
                "grey",
                reason="判异复核推翻分歧点（语义灰区，不重试）",
                attempts=attempts,
                cards=_cards_from_baseline_cache(text_a, text_b, mem_cache),
            )
        if cause == "match_none":
            return _terminal(
                "grey", reason="规则匹配无结论（语义灰区）", attempts=attempts,
                cards=_cards_from_baseline_cache(text_a, text_b, mem_cache),
            )
        return _terminal(
            "error", reason=f"未预期判定失败（{cause}），重试次数用尽",
            attempts=attempts,
        )
    return _terminal(
        "error", reason="调用失败（None/异常），0/20/40s 重试 3 次用尽",
        attempts=attempts,
    )


def _differences_cache_key(text_a: str, text_b: str, model: str) -> str:
    """第一阶段缓存键：无序证据对（pair_cache_key 内部排序）+ model/版本。"""
    return pair_cache_key("experiment_differences", text_a, text_b, model)


def _adjudication_cache_key(differences: dict[str, Any], model: str) -> str:
    """第二阶段缓存键：differences canonical JSON + model/prompt/taxonomy 版本。"""
    canonical = json.dumps(differences, ensure_ascii=False, sort_keys=True)
    return versioned_cache_key("experiment_adjudication", canonical, model)


def _cache_result(cache: dict[str, Any], key: str) -> Any:
    """读成功缓存条目；缺失/残缺返回 None（与“调用失败不缓存”区分开）。"""
    entry = cache.get(key)
    if isinstance(entry, dict) and "result" in entry:
        return entry["result"]
    return None


def _store_cache(
    cache: dict[str, Any], key: str, result: Any, model: str,
) -> None:
    cache[key] = {"result": result, "model": model, "saved_at": utc_now_iso()}


def _judge_vnext_pair(
    config: Any,
    budget: RequestBudget,
    text_a: str,
    text_b: str,
    card_a: ExperimentCard | None,
    card_b: ExperimentCard | None,
    diff_cache: dict[str, Any],
    diff_cache_path: Path,
    adj_cache: dict[str, Any],
    adj_cache_path: Path,
) -> dict[str, Any]:
    """vNext 判定：规则 match → same/diff；uncertain → 盲态两步复判。

    第一步 generate_differences 不输入任何原判，成功立即写版本化缓存；
    第二步 adjudicate 只输入 differences + 空 confirmed_examples，失败只重试
    自身（不重打 differences）。adjudicate uncertain 是可缓存的语义终态；
    任一调用 None 失败不缓存、最多 3 次尝试（0/20/40s），预算耗尽即 not_run。
    """
    model = _cache_model_namespace(config)
    cards = None
    if card_a is not None and card_b is not None:
        cards = {"a": card_a.to_dict(), "b": card_b.to_dict()}
    if card_a is None or card_b is None:
        return _terminal(
            "error", reason="vNext 预打卡失败（见 run_metadata.card_prepass）",
            attempts=0, cards=cards,
        )
    dkey = _differences_cache_key(text_a, text_b, model)
    differences = _cache_result(diff_cache, dkey)
    attempts = 0
    for attempt in range(MAX_CALL_FAILURE_ATTEMPTS):
        if not _budget_ok(budget):
            return _terminal(
                "not_run", reason="LLM 请求预算耗尽", attempts=attempts, cards=cards,
            )
        attempts += 1
        decision = match_experiment_cards(card_a, card_b)
        if decision.relation != "uncertain":
            return _terminal(
                decision.relation, relation=decision.relation, reason=decision.reason,
                attempts=attempts, source="rule_match", cards=cards,
            )
        # 第一步：差异生成（不输入原判）。命中缓存则零请求。
        if differences is None:
            if not _budget_ok(budget):
                return _terminal(
                    "not_run", reason="LLM 请求预算耗尽（复判前）", attempts=attempts,
                    cards=cards,
                )
            differences = generate_differences(
                config, card_a=card_a, card_b=card_b,
                evidence_a=text_a, evidence_b=text_b,
            )
            if differences is None:
                exhausted = _exhausted_not_run(budget, attempts, cards=cards)
                if exhausted is not None:
                    return exhausted
                _retry_sleep(attempt + 1)
                continue
            _store_cache(diff_cache, dkey, differences, model)
            write_json(diff_cache_path, diff_cache)  # 第一阶段成功立即原子落盘
        # 第二步：口径裁决（differences + 空 confirmed_examples）。
        akey = _adjudication_cache_key(differences, model)
        adjudication = _cache_result(adj_cache, akey)
        if adjudication is None:
            if not _budget_ok(budget):
                return _terminal(
                    "not_run", reason="LLM 请求预算耗尽（裁决前）", attempts=attempts,
                    cards=cards, differences=differences,
                )
            result = adjudicate_differences(
                config, differences, confirmed_examples=None,
            )
            if result is None:
                exhausted = _exhausted_not_run(
                    budget, attempts, cards=cards, differences=differences,
                )
                if exhausted is not None:
                    return exhausted
                _retry_sleep(attempt + 1)
                continue
            adjudication = {
                "relation": result.relation,
                "decisive_axis": result.decisive_axis,
                "reason": result.reason,
            }
            _store_cache(adj_cache, akey, adjudication, model)
            write_json(adj_cache_path, adj_cache)  # 语义终态同样缓存，零请求复用
        if adjudication["relation"] == "uncertain":
            return _terminal(
                "grey", reason=adjudication["reason"], attempts=attempts,
                source="recheck", cards=cards, differences=differences,
                decisive_axis=adjudication["decisive_axis"],
            )
        return _terminal(
            adjudication["relation"], relation=adjudication["relation"],
            reason=adjudication["reason"], attempts=attempts, source="recheck",
            cards=cards, differences=differences,
            decisive_axis=adjudication["decisive_axis"],
        )
    return _terminal(
        "error", reason="复判调用失败（None），0/20/40s 重试 3 次用尽",
        attempts=attempts, cards=cards,
    )


def _load_run_evidence(db, manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """逐 variant 取自己 asset 的首个分析构造 PairEvidence；哈希必须匹配冻结快照。"""
    snapshot = manifest.get("evidence_snapshot")
    if not isinstance(snapshot, dict) or not snapshot:
        raise ExperimentError("gold_manifest.json 缺少 evidence_snapshot，无法核验证据")
    out: dict[str, dict[str, Any]] = {}
    problems: list[str] = []
    for item in manifest.get("items", []):
        variant_id = str(item["variant_id"])
        expected_entry = snapshot.get(variant_id)
        if not expected_entry:
            problems.append(f"{variant_id}（不在冻结快照中）")
            continue
        rows = run_query(
            db,
            "select id, created_at, " + ", ".join(_ANALYSIS_COLS)
            + " from analysis_results where asset_id = :asset_id "
            "order by created_at, id limit 1",
            {"asset_id": item["asset_id"]},
        )
        if not rows:
            problems.append(f"{variant_id}（asset {item['asset_id']} 无分析）")
            continue
        analysis = dict(zip(_ANALYSIS_COLS, rows[0][2:], strict=True))
        evidence = evidence_from_analysis(analysis)
        text = evidence.full_text()
        digest = sha256_text(text)
        if digest != expected_entry.get("evidence_hash"):
            problems.append(f"{variant_id}（证据哈希与冻结快照不一致，分析被更新？）")
            continue
        out[variant_id] = {
            "evidence": evidence,
            "text": text,
            "evidence_hash": digest,
            "empty": not text.strip(),
        }
    if problems:
        raise ExperimentError(
            "run 证据核验失败（需重新 freeze 后再 run）：\n- " + "\n- ".join(problems)
        )
    return out


def _seed_card_caches(
    involved: list[str],
    evidence_map: dict[str, dict[str, Any]],
    model: str,
    baseline_mem: dict[str, Any],
    vnext_mem: dict[str, Any],
    baseline_disk: dict[str, Any],
    vnext_disk: dict[str, Any],
) -> None:
    """用磁盘成功缓存（键含模型/prompt/taxonomy 版本）预热内存缓存。"""
    for variant_id in involved:
        info = evidence_map[variant_id]
        if info["empty"]:
            continue
        text = info["text"]
        key24 = fingerprint_card.cache_key(text)
        bkey = _baseline_card_cache_key(text, model)
        if key24 not in baseline_mem and bkey in baseline_disk:
            entry = baseline_disk[bkey]
            if isinstance(entry, dict) and isinstance(entry.get("card"), dict):
                try:
                    baseline_mem[key24] = _fingerprint_card_from_dict(entry["card"])
                except (KeyError, TypeError):
                    pass  # 旧格式/残缺条目：忽略，重打
        vkey = _vnext_card_cache_key(text, model)
        if vkey not in vnext_mem and vkey in vnext_disk:
            entry = vnext_disk[vkey]
            if isinstance(entry, dict) and isinstance(entry.get("card"), dict):
                try:
                    vnext_mem[vkey] = _experiment_card_from_dict(entry["card"])
                except (KeyError, TypeError):
                    pass


def _variant_status_terminal(system: str, status: str) -> dict[str, Any]:
    """预打卡非 ok（failed/not_run/no_evidence）时对该系统所有对的短路终态。"""
    if status == "no_evidence":
        return _terminal("grey", reason="证据为空，不调用 LLM", attempts=0)
    if status == "not_run":
        return _terminal("not_run", reason="LLM 请求预算耗尽（预打卡阶段）", attempts=0)
    label = "基线" if system == "baseline" else "vNext"
    return _terminal(
        "error",
        reason=f"{label}预打卡调用失败（0/20/40s 重试 3 次用尽），对级不重试",
        attempts=0,
    )


def _lock_exclusive(handle: Any) -> None:
    if fcntl is not None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    elif msvcrt is not None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        raise ExperimentError("当前平台没有可用的文件锁实现")


def _unlock_exclusive(handle: Any) -> None:
    if fcntl is not None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    elif msvcrt is not None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


@contextmanager
def _workdir_run_lock(workdir: Path) -> Any:
    """同一实验目录只允许一个 run 进程；进程退出后由 OS 自动释放。"""
    workdir.mkdir(parents=True, exist_ok=True)
    lock_path = workdir / "run.lock"
    handle = lock_path.open("w", encoding="utf-8")
    try:
        _lock_exclusive(handle)
    except OSError as exc:
        handle.close()
        raise ExperimentError(
            f"{workdir} 已有另一个 run 在运行或发生锁冲突；"
            "拒绝并发执行以保护预算和结果文件"
        ) from exc
    try:
        handle.write(str(os.getpid()))
        handle.flush()
        yield
    finally:
        _unlock_exclusive(handle)
        handle.close()


def cmd_run(args: argparse.Namespace) -> int:
    with _workdir_run_lock(args.workdir):
        return _cmd_run_locked(args)


def _cmd_run_locked(args: argparse.Namespace) -> int:
    workdir: Path = args.workdir
    manifest_path = workdir / GOLD_MANIFEST_NAME
    sample_path = workdir / SAMPLE_NAME
    if not manifest_path.exists():
        raise ExperimentError(f"缺少 {manifest_path}，请先运行 prepare + freeze")
    if not sample_path.exists():
        raise ExperimentError(f"缺少 {sample_path}，请先运行 sample")

    manifest = load_json(manifest_path)
    sample = load_json(sample_path)
    manifest_hash = sha256_text(manifest_path.read_text(encoding="utf-8"))
    if sample.get("gold_manifest_hash") != manifest_hash:
        raise ExperimentError(
            "sample20.json 的 gold_manifest_hash 与当前 gold_manifest.json 不一致"
            "（gold 冻结后被动过？）；请核对后重新 sample"
        )
    pairs = sample.get("selected")
    if not isinstance(pairs, list) or not pairs:
        raise ExperimentError("sample20.json 缺少 selected 对")
    pair_ids = [str(pair.get("pair_id")) for pair in pairs]
    if len(pair_ids) != len(set(pair_ids)):
        raise ExperimentError("sample20.json 存在重复 pair_id")

    db = SessionLocal()
    try:
        config = resolve_ai_config(db, get_settings())
        evidence_map = _load_run_evidence(db, manifest)
    finally:
        db.close()
    if not config.vision_model:
        raise ExperimentError("未配置 vision_model，无法运行实验")

    model = config.vision_model
    cache_model = _cache_model_namespace(config)
    versions = {
        "merge_judge_prompt": ip_pack.MERGE_JUDGE_PROMPT_VERSION,
        "card_prompt": ip_pack.CARD_PROMPT_VERSION,
        "baseline_taxonomy": _taxonomy_content_hash(),
        "experiment": EXPERIMENT_VERSION,
        "taxonomy": TAXONOMY_VERSION,
        "judge_temperature": config.judge_temperature,
        "baseline_card_max_tokens": BASELINE_CARD_MAX_TOKENS,
    }
    involved = sorted({
        str(pair["variant_a"]) for pair in pairs
    } | {
        str(pair["variant_b"]) for pair in pairs
    })

    ledger_path = workdir / REQUEST_LEDGER_NAME
    budget = RequestBudget(limit=REQUEST_BUDGET_LIMIT)
    budget.used = _load_request_ledger(ledger_path)  # 跨进程累计，续跑不重置配额
    baseline_disk = _load_card_cache(workdir / BASELINE_CARD_CACHE_NAME)
    vnext_disk = _load_card_cache(workdir / VNEXT_CARD_CACHE_NAME)
    diff_cache_path = workdir / DIFFERENCES_CACHE_NAME
    adj_cache_path = workdir / ADJUDICATIONS_CACHE_NAME
    diff_cache = _load_card_cache(diff_cache_path)
    adj_cache = _load_card_cache(adj_cache_path)
    results_path = workdir / RESULTS_NAME
    results = _load_results(results_path)
    pair_by_id = {str(p["pair_id"]): p for p in pairs}
    reusable = {
        r["pair_id"]: r for r in results
        if _record_reusable(
            r, pair_by_id.get(r["pair_id"]), model, versions, evidence_map,
        )
    }
    remaining = [p for p in pairs if str(p["pair_id"]) not in reusable]

    started_at = utc_now_iso()
    card_stats: dict[str, dict[str, int]] = {
        system: {"requested": 0, "ok": 0, "failed": 0, "not_run": 0, "no_evidence": 0}
        for system in ("baseline", "vnext")
    }
    variant_status: dict[str, dict[str, str]] = {"baseline": {}, "vnext": {}}
    interrupted: str | None = None

    original_client = _install_budget_client(budget, ledger_path)
    original_recheck, recorder = _install_recheck_recorder()
    try:
        if remaining:
            baseline_mem: dict[str, Any] = {}
            vnext_mem: dict[str, Any] = {}
            _seed_card_caches(
                involved, evidence_map, cache_model,
                baseline_mem, vnext_mem, baseline_disk, vnext_disk,
            )
            # 预打两套卡：只打涉及素材；失败不缓存，成功原子写版本化磁盘缓存。
            for variant_id in involved:
                info = evidence_map[variant_id]
                if info["empty"]:
                    for system in ("baseline", "vnext"):
                        variant_status[system][variant_id] = "no_evidence"
                        card_stats[system]["no_evidence"] += 1
                    continue
                text = info["text"]
                card_stats["baseline"]["requested"] += 1
                card, status, _ = _ensure_baseline_card(config, budget, text, baseline_mem)
                variant_status["baseline"][variant_id] = status
                card_stats["baseline"][status] += 1
                if status == "ok":
                    bkey = _baseline_card_cache_key(text, cache_model)
                    if bkey not in baseline_disk:
                        baseline_disk[bkey] = {
                            "card": card.to_dict(), "model": model,
                            "saved_at": utc_now_iso(),
                        }
                        write_json(workdir / BASELINE_CARD_CACHE_NAME, baseline_disk)
                card_stats["vnext"]["requested"] += 1
                card, status, _ = _ensure_vnext_card(config, budget, text, vnext_mem)
                variant_status["vnext"][variant_id] = status
                card_stats["vnext"][status] += 1
                if status == "ok":
                    vkey = _vnext_card_cache_key(text, cache_model)
                    if vkey not in vnext_disk:
                        vnext_disk[vkey] = {
                            "card": card.to_dict(), "model": model,
                            "saved_at": utc_now_iso(),
                        }
                        write_json(workdir / VNEXT_CARD_CACHE_NAME, vnext_disk)

            # 逐对判定：基线走生产 judge_pair，vNext 规则 match + 盲态两步复判。
            known = {str(r["pair_id"]): r for r in results}
            for pair in pairs:
                pair_id = str(pair["pair_id"])
                if pair_id in reusable:
                    continue
                variant_a = str(pair["variant_a"])
                variant_b = str(pair["variant_b"])
                info_a = evidence_map[variant_a]
                info_b = evidence_map[variant_b]
                record = {
                    "pair_id": pair_id,
                    "gold": pair.get("gold"),
                    "variant_a": variant_a,
                    "variant_b": variant_b,
                    "evidence_hash_a": info_a["evidence_hash"],
                    "evidence_hash_b": info_b["evidence_hash"],
                    "model_id": model,
                    "versions": versions,
                    "baseline": {},
                    "vnext": {},
                }
                baseline_states = (
                    variant_status["baseline"].get(variant_a),
                    variant_status["baseline"].get(variant_b),
                )
                vnext_states = (
                    variant_status["vnext"].get(variant_a),
                    variant_status["vnext"].get(variant_b),
                )
                if "no_evidence" in baseline_states:
                    record["baseline"] = _variant_status_terminal("baseline", "no_evidence")
                elif "not_run" in baseline_states:
                    record["baseline"] = _variant_status_terminal("baseline", "not_run")
                elif "failed" in baseline_states:
                    record["baseline"] = _variant_status_terminal("baseline", "failed")
                else:
                    record["baseline"] = _judge_baseline_pair(
                        config, budget, info_a["evidence"], info_b["evidence"],
                        baseline_mem, recorder,
                    )
                if "no_evidence" in vnext_states:
                    record["vnext"] = _variant_status_terminal("vnext", "no_evidence")
                elif "not_run" in vnext_states:
                    record["vnext"] = _variant_status_terminal("vnext", "not_run")
                elif "failed" in vnext_states:
                    record["vnext"] = _variant_status_terminal("vnext", "failed")
                else:
                    record["vnext"] = _judge_vnext_pair(
                        config, budget, info_a["text"], info_b["text"],
                        vnext_mem.get(_vnext_card_cache_key(info_a["text"], cache_model)),
                        vnext_mem.get(_vnext_card_cache_key(info_b["text"], cache_model)),
                        diff_cache, diff_cache_path, adj_cache, adj_cache_path,
                    )
                known[pair_id] = record
                results = [
                    known[str(p["pair_id"])] for p in pairs
                    if str(p["pair_id"]) in known
                ]
                _write_results_atomic(results_path, results)
        else:
            print("全部对均已有成功结果，零 LLM 调用完成（复用 results.jsonl）。")
    except Exception as exc:  # noqa: BLE001
        interrupted = f"{type(exc).__name__}: {exc}"[:300]
    finally:
        merge_judge.recheck_split = original_recheck
        llm_judge._client = original_client

    baseline_counts = dict(Counter((r.get("baseline") or {}).get("status") for r in results))
    vnext_counts = dict(Counter((r.get("vnext") or {}).get("status") for r in results))
    # completed 要求结果数等于样本数且全部终态；空结果/任何非终端一律 partial。
    all_terminal = (
        len(results) == len(pairs)
        and all(
            (r.get("baseline") or {}).get("status") in TERMINAL_STATUSES for r in results
        )
        and all(
            (r.get("vnext") or {}).get("status") in TERMINAL_STATUSES for r in results
        )
    )
    run_status = "completed" if all_terminal else "partial"
    if interrupted is not None:
        run_status = "interrupted"
    metadata = {
        "schema_version": 1,
        "kind": "run_metadata",
        "status": run_status,
        "started_at": started_at,
        "finished_at": utc_now_iso(),
        "model_id": model,
        "judge_temperature": config.judge_temperature,
        "baseline_card_max_tokens": BASELINE_CARD_MAX_TOKENS,
        "versions": versions,
        "budget": {
            "limit": budget.limit,
            "used": budget.used,
            "remaining": budget.remaining,
        },
        "inputs": {
            "gold_manifest_hash": manifest_hash,
            "sample_pairs": len(pairs),
            "sample_seed": (sample.get("disclosure") or {}).get("seed"),
            "resumed_reused_pairs": len(reusable),
            "remaining_pairs_this_run": len(remaining),
            "involved_variants": len(involved),
        },
        "card_prepass": card_stats,
        "pair_status_counts": {
            "baseline": baseline_counts,
            "vnext": vnext_counts,
        },
        "request_plan_note": (
            "理论请求上界 ≈ 26 卡 + 20 基线复核 + 40 复判 = 86；硬上限 100 含瞬时"
            "失败重试。达到上限后当前/剩余对一律 not_run（不计入质量分母）。"
            "budget.used 为 request_ledger.json 的跨进程累计值（每次真实请求"
            "consume 后立即原子记账），续跑不重置配额。"
        ),
        "privacy_note": "本文件不含 API key / base_url。",
    }
    if interrupted is not None:
        metadata["interrupted"] = interrupted
    write_json(workdir / RUN_METADATA_NAME, metadata)

    print(
        f"run 完成：状态={run_status}；真实请求 {budget.used}/{budget.limit}；"
        f"复用 {len(reusable)} 对，本轮新判 {len(remaining)} 对。"
    )
    print(f"baseline 状态分布：{baseline_counts}")
    print(f"vnext 状态分布：{vnext_counts}")
    print(f"results → {results_path}")
    print(f"metadata → {workdir / RUN_METADATA_NAME}")
    if interrupted is not None:
        raise ExperimentError(f"run 中断（已完成对仍保留，可重跑续判）：{interrupted}")
    return 0


# --- 子命令：report -----------------------------------------------------------

# report 是纯汇总：零 LLM、零 DB，只读 workdir 内 run 产物，覆盖写
# pilot_metrics.json / pilot_report.md（派生产物，允许覆盖）。

PILOT_METRICS_NAME = "pilot_metrics.json"
PILOT_REPORT_NAME = "pilot_report.md"

REPORT_STATUSES = ("same", "diff", "grey", "error", "not_run")
# 家族聚类 bootstrap 的最小独立单元数：低于此数不伪造区间。
_MIN_BOOTSTRAP_UNITS = 3
_BOOTSTRAP_RESAMPLES = 1000

_METRIC_ROWS = (
    ("over_split", "over-split（gold same 判 diff ÷ 已运行 same）"),
    ("over_merge", "over-merge（gold diff 判 same ÷ 已运行 diff）"),
    ("precision_same", "判同精度（判 same 中真 same）"),
    ("precision_diff", "判异精度（判 diff 中真 diff）"),
    ("decided_coverage", "明确判定覆盖率（same+diff ÷ 已运行）"),
    ("error_rate", "基础设施错误率（error ÷ 已运行）"),
    ("manual_review_rate", "潜在人工复核率（grey+error ÷ 已运行）"),
)


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _frac(numerator: int, denominator: int) -> dict[str, Any]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": _rate(numerator, denominator),
    }


def _record_context_ok(
    record: dict[str, Any],
    pair: dict[str, Any],
    snapshot: dict[str, Any],
    model: str,
    versions: dict[str, Any],
) -> bool:
    """report 侧上下文校验：与 run 复用口径一致，但不要求 terminal。

    error/not_run 记录同样可通过校验进入指标（它们不是上下文错误）。
    """
    if record.get("model_id") != model or record.get("versions") != versions:
        return False
    if record.get("gold") != pair.get("gold"):
        return False
    if (
        str(record.get("variant_a")) != str(pair.get("variant_a"))
        or str(record.get("variant_b")) != str(pair.get("variant_b"))
    ):
        return False
    entry_a = snapshot.get(str(pair["variant_a"])) or {}
    entry_b = snapshot.get(str(pair["variant_b"])) or {}
    return (
        record.get("evidence_hash_a") == entry_a.get("evidence_hash")
        and record.get("evidence_hash_b") == entry_b.get("evidence_hash")
    )


def _classify_report_records(
    sample_pairs: list[dict[str, Any]],
    results: list[dict[str, Any]],
    snapshot: dict[str, Any],
    model: str,
    versions: dict[str, Any],
) -> dict[str, Any]:
    """把样本对分为 included（上下文一致）/ missing / invalid_context。"""
    by_id = {str(r["pair_id"]): r for r in results}
    included: list[tuple[dict[str, Any], dict[str, Any]]] = []
    missing: list[dict[str, Any]] = []
    invalid_context: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for pair in sample_pairs:
        record = by_id.get(str(pair.get("pair_id")))
        if record is None:
            missing.append(pair)
        elif _record_context_ok(record, pair, snapshot, model, versions):
            included.append((pair, record))
        else:
            invalid_context.append((pair, record))
    return {
        "included": included,
        "missing": missing,
        "invalid_context": invalid_context,
    }


def _confusion(
    included: list[tuple[dict[str, Any], dict[str, Any]]], system: str,
) -> dict[str, dict[str, int]]:
    counts: dict[str, Counter[str]] = {}
    for pair, record in included:
        gold = str(pair.get("gold") or "unknown")
        status = str((record.get(system) or {}).get("status") or "unknown")
        counts.setdefault(gold, Counter())[status] += 1
    return {gold: dict(counter) for gold, counter in counts.items()}


def _system_metrics(
    included: list[tuple[dict[str, Any], dict[str, Any]]],
    system: str,
    selected_total: int,
) -> dict[str, Any]:
    """对级指标。分母口径：已运行 = 除 not_run 外全部（grey/error 保留）。"""
    confusion = _confusion(included, system)
    same_cells = confusion.get("same", {})
    diff_cells = confusion.get("diff", {})
    same_ran = sum(n for s, n in same_cells.items() if s != "not_run")
    diff_ran = sum(n for s, n in diff_cells.items() if s != "not_run")
    ran_total = same_ran + diff_ran
    not_run_total = sum(c.get("not_run", 0) for c in confusion.values())
    judged_same = same_cells.get("same", 0)
    judged_diff = diff_cells.get("diff", 0)
    same_judged_total = same_cells.get("same", 0) + diff_cells.get("same", 0)
    diff_judged_total = same_cells.get("diff", 0) + diff_cells.get("diff", 0)
    grey_total = sum(c.get("grey", 0) for c in confusion.values())
    error_total = sum(c.get("error", 0) for c in confusion.values())
    decided = same_judged_total + diff_judged_total
    return {
        "confusion_gold_x_status": confusion,
        "selected_total": selected_total,
        "included_total": len(included),
        "ran_total": ran_total,
        "not_run_total": not_run_total,
        "over_split": _frac(same_cells.get("diff", 0), same_ran),
        "over_merge": _frac(diff_cells.get("same", 0), diff_ran),
        "precision_same": _frac(judged_same, same_judged_total),
        "precision_diff": _frac(judged_diff, diff_judged_total),
        "decided_coverage": _frac(decided, ran_total),
        "error_rate": _frac(error_total, ran_total),
        "manual_review_rate": _frac(grey_total + error_total, ran_total),
    }


def _harmful_rate(tuples: list[tuple[dict[str, Any], dict[str, Any]]]) -> float | None:
    ran = 0
    harmful = 0
    for pair, record in tuples:
        status = (record.get("__system__") or {}).get("status")
        if status == "not_run":
            continue
        ran += 1
        if (pair.get("gold") == "same" and status == "diff") or (
            pair.get("gold") == "diff" and status == "same"
        ):
            harmful += 1
    return harmful / ran if ran else None


def _family_bootstrap(
    included: list[tuple[dict[str, Any], dict[str, Any]]],
    family_of: dict[str, str],
    system: str,
    n_boot: int = _BOOTSTRAP_RESAMPLES,
    seed: int = SEED,
    min_units: int = _MIN_BOOTSTRAP_UNITS,
) -> dict[str, Any]:
    """按确认 family 单元聚类 bootstrap 有害错误率（探索性）。

    独立单元不足（如本批仅 2 个确认家族）返回 insufficient_independent_families，
    不伪造区间。
    """
    scoped = [
        (pair, {"__system__": record.get(system) or {}})
        for pair, record in included
    ]
    if not scoped:
        return {"status": "no_data"}
    families = sorted({
        str(family_of.get(str(pair["variant_a"]), "?"))
        for pair, _record in scoped
    } | {
        str(family_of.get(str(pair["variant_b"]), "?"))
        for pair, _record in scoped
    })
    if len(families) < min_units:
        return {
            "status": "insufficient_independent_families",
            "n_units": len(families),
            "min_required": min_units,
        }
    rng = random.Random(seed)
    values: list[float] = []
    for _ in range(n_boot):
        drawn = {rng.choice(families) for _ in families}
        sample = [
            (pair, record) for pair, record in scoped
            if str(family_of.get(str(pair["variant_a"]), "?")) in drawn
            or str(family_of.get(str(pair["variant_b"]), "?")) in drawn
        ]
        value = _harmful_rate(sample)
        if value is not None:
            values.append(value)
    if not values:
        return {"status": "insufficient_resamples", "n_resamples": 0}
    values.sort()
    point = _harmful_rate(scoped)
    lo = values[min(len(values) - 1, int(0.025 * len(values)))]
    hi = values[min(len(values) - 1, int(0.975 * len(values)))]
    return {
        "status": "ok",
        "metric": "harmful_error_rate",
        "system": system,
        "n_boot": n_boot,
        "n_units": len(families),
        "point": point,
        "ci95": [lo, hi],
        "note": "按确认 family 重采样（对级记录按端点家族命中计入）；探索性，不构成正式区间结论",
    }


def _paired_migration(
    included: list[tuple[dict[str, Any], dict[str, Any]]],
) -> dict[str, Any]:
    """两系统并列变化：baseline→vnext 状态迁移（跳过含 not_run 的对）。"""
    transitions: Counter[str] = Counter()
    both_ran = 0
    for _pair, record in included:
        baseline = (record.get("baseline") or {}).get("status")
        vnext = (record.get("vnext") or {}).get("status")
        if baseline == "not_run" or vnext == "not_run":
            continue
        both_ran += 1
        transitions[f"{baseline}->{vnext}"] += 1
    return {"pairs_both_ran": both_ran, "transitions": dict(transitions)}


def _ending_profile(manifest_items: list[dict[str, Any]]) -> dict[str, Any]:
    endings = [str(item.get("confirmed_ending") or "").strip() for item in manifest_items]
    distinct = sorted(set(endings))
    return {
        "endings": dict(Counter(endings)),
        "all_fail": bool(endings) and distinct == ["fail"],
    }


def _connected_components(
    variants: list[str], same_edges: list[tuple[str, str]],
) -> dict[str, int]:
    parent = {v: v for v in variants}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in same_edges:
        if a in parent and b in parent:
            parent[find(a)] = find(b)
    roots: dict[str, int] = {}
    out: dict[str, int] = {}
    for v in variants:
        root = find(v)
        if root not in roots:
            roots[root] = len(roots)
        out[v] = roots[root]
    return out


def _bcubed(
    items: list[str], truth: dict[str, str], pred: dict[str, str],
) -> dict[str, float]:
    truth_sizes = Counter(truth[v] for v in items)
    pred_sizes = Counter(pred[v] for v in items)
    precision_sum = 0.0
    recall_sum = 0.0
    for item in items:
        t, p = truth[item], pred[item]
        inter = sum(
            1 for other in items if truth[other] == t and pred[other] == p
        )
        precision_sum += inter / pred_sizes[p]
        recall_sum += inter / truth_sizes[t]
    n = len(items) or 1
    precision = precision_sum / n
    recall = recall_sum / n
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1}


def _ari(items: list[str], truth: dict[str, str], pred: dict[str, str]) -> float:
    contingency: Counter[tuple[str, str]] = Counter(
        (truth[v], pred[v]) for v in items
    )
    n = len(items)
    total = math.comb(n, 2) if n >= 2 else 0
    if not total:
        return 0.0
    sum_cont = sum(math.comb(c, 2) for c in contingency.values())
    sum_truth = sum(math.comb(c, 2) for c in Counter(truth[v] for v in items).values())
    sum_pred = sum(math.comb(c, 2) for c in Counter(pred[v] for v in items).values())
    expected = sum_truth * sum_pred / total
    denom = 0.5 * (sum_truth + sum_pred) - expected
    if not denom:
        return 0.0
    return (sum_cont - expected) / denom


def _cluster_metrics(
    manifest: dict[str, Any],
    included: list[tuple[dict[str, Any], dict[str, Any]]],
    system: str,
) -> dict[str, Any]:
    """B³/LEA/ARI：只有 13 项宇宙 78/78 全判才计算，否则一律未验证。

    grey 对不产生同族边（保守不合并）；not_run/error 存在时也不计算，
    避免用不完整聚类制造假簇指标。
    """
    universe_pairs = manifest.get("pairs") or []
    universe_complete = (
        len(universe_pairs) == EXPECTED_PAIR_COUNT
        and len(included) == len(universe_pairs)
        and all(
            (record.get(system) or {}).get("status") in ("same", "diff", "grey")
            for _pair, record in included
        )
    )
    if not universe_complete:
        return {
            "b3": "unverified",
            "lea": "unverified",
            "ari": "unverified",
            "reason": (
                f"仅 {len(included)}/{len(universe_pairs) or EXPECTED_PAIR_COUNT} 对完成判定"
                "（非 13 项宇宙 78/78 全判）；簇级指标一律不计算，避免假指标"
            ),
        }
    items = [str(item["variant_id"]) for item in manifest.get("items", [])]
    truth = {
        str(item["variant_id"]): f"fam-{item.get('confirmed_family_id')}"
        for item in manifest.get("items", [])
    }
    same_edges = [
        (str(pair["variant_a"]), str(pair["variant_b"]))
        for pair, record in included
        if (record.get(system) or {}).get("status") == "same"
    ]
    pred = _connected_components(items, same_edges)
    return {
        "b3": _bcubed(items, truth, pred),
        "ari": _ari(items, truth, pred),
        "lea": "unverified_not_implemented",
        "note": "grey 对按非同族边处理（保守）；LEA 未实现，不给假值",
    }


def _fmt_frac(block: dict[str, Any]) -> str:
    value = block.get("value")
    if value is None:
        return f"n/a（分母 0；分子 {block.get('numerator', 0)}）"
    return f"{value:.1%}（{block['numerator']}/{block['denominator']}）"


def _metrics_markdown(system: str, label: str, metrics: dict[str, Any]) -> list[str]:
    lines = [
        f"## {label}（{system}）",
        "",
        f"- selected 总数：{metrics['selected_total']}；计入指标：{metrics['included_total']}；"
        f"已运行（剔除 not_run）：{metrics['ran_total']}；not_run：{metrics['not_run_total']}",
        "",
        "| 指标 | 值 |",
        "|---|---|",
    ]
    for key, label_text in _METRIC_ROWS:
        lines.append(f"| {label_text} | {_fmt_frac(metrics[key])} |")
    lines += [
        "",
        "混淆（gold × status）：",
        "",
        "| gold \\ 判定 | same | diff | grey | error | not_run |",
        "|---|---|---|---|---|---|",
    ]
    confusion = metrics["confusion_gold_x_status"]
    for gold in ("same", "diff"):
        cells = confusion.get(gold, {})
        lines.append(
            f"| {gold} | {cells.get('same', 0)} | {cells.get('diff', 0)} |"
            f" {cells.get('grey', 0)} | {cells.get('error', 0)} | {cells.get('not_run', 0)} |"
        )
    lines.append("")
    return lines


def cmd_report(args: argparse.Namespace) -> int:
    workdir: Path = args.workdir
    sample = load_json(workdir / SAMPLE_NAME)
    manifest = load_json(workdir / GOLD_MANIFEST_NAME)
    metadata = load_json(workdir / RUN_METADATA_NAME)
    results = _load_results(workdir / RESULTS_NAME)

    pairs = sample.get("selected")
    if not isinstance(pairs, list) or not pairs:
        raise ExperimentError("sample20.json 缺少 selected 对")
    manifest_hash = sha256_text(
        (workdir / GOLD_MANIFEST_NAME).read_text(encoding="utf-8")
    )
    if sample.get("gold_manifest_hash") != manifest_hash:
        raise ExperimentError("sample 与当前 gold_manifest 不一致，拒绝生成报告")
    metadata_hash = (metadata.get("inputs") or {}).get("gold_manifest_hash")
    if metadata_hash != manifest_hash:
        raise ExperimentError("run_metadata 与当前 gold_manifest 不一致，拒绝生成报告")
    snapshot = manifest.get("evidence_snapshot") or {}
    model = str(metadata.get("model_id") or "")
    versions = metadata.get("versions")
    if not model or not isinstance(versions, dict) or not versions:
        raise ExperimentError(
            "run_metadata.json 缺少 model_id/versions，无法校验结果上下文（先运行 run）"
        )

    classes = _classify_report_records(pairs, results, snapshot, model, versions)
    included = classes["included"]
    missing = classes["missing"]
    invalid = classes["invalid_context"]
    selected_total = len(pairs)
    family_of = {
        str(item["variant_id"]): str(item.get("confirmed_family_id") or "?")
        for item in manifest.get("items", [])
    }

    systems: dict[str, Any] = {}
    for system in ("baseline", "vnext"):
        block = _system_metrics(included, system, selected_total)
        block["family_bootstrap_harmful_rate"] = _family_bootstrap(
            included, family_of, system,
        )
        block["cluster_metrics"] = _cluster_metrics(manifest, included, system)
        systems[system] = block

    migration = _paired_migration(included)
    ending = _ending_profile(manifest.get("items", []))
    budget = metadata.get("budget") or {}
    not_run_pairs = sum(
        1
        for _pair, record in included
        if any(
            (record.get(system) or {}).get("status") == "not_run"
            for system in ("baseline", "vnext")
        )
    )
    error_pairs = sum(
        1
        for _pair, record in included
        if any(
            (record.get(system) or {}).get("status") == "error"
            for system in ("baseline", "vnext")
        )
    )
    partial = (
        bool(missing or invalid)
        or not_run_pairs > 0
        or error_pairs > 0
        or len(included) < selected_total
        or metadata.get("status") != "completed"
    )

    caveats = [
        "样本为按确认 gold 的分层 20 对 pilot（非自然候选流），属 taxonomy 开发性实验；"
        "不声称 ≥99% 判异精度、新族泛化或自然候选流真实人力节省。",
    ]
    if error_pairs:
        caveats.append(f"{error_pairs} 对含基础设施 error，报告保持 partial，不作成功声明。")
    if missing:
        caveats.append(f"{len(missing)} 对缺记录（run 未完成或结果丢失），已醒目标注。")
    if invalid:
        caveats.append(
            f"{len(invalid)} 对上下文失效（model/versions/gold/证据哈希与当前不一致），"
            "未计入指标。"
        )
    if not_run_pairs:
        caveats.append(f"{not_run_pairs} 对含 not_run（预算等原因），不计入质量分母。")
    if ending["all_fail"]:
        caveats.append(
            "本批 confirmed ending 全为 fail：结局不同拆族规则在本批未验证。"
        )
    bootstrap_note = all(
        systems[system]["family_bootstrap_harmful_rate"].get("status")
        == "insufficient_independent_families"
        for system in ("baseline", "vnext")
    )
    if bootstrap_note:
        caveats.append(
            "按确认 family 的探索 bootstrap：独立家族不足"
            f"（{systems['baseline']['family_bootstrap_harmful_rate'].get('n_units')} 个 < "
            f"{_MIN_BOOTSTRAP_UNITS}），返回 insufficient_independent_families，不伪造区间。"
        )
    caveats.append(
        "B³/LEA/ARI：" + systems["baseline"]["cluster_metrics"].get("reason", "未验证")
        if systems["baseline"]["cluster_metrics"].get("b3") == "unverified"
        else "B³/LEA/ARI：仅在 78/78 全判分支计算（见 pilot_metrics.json）。"
    )

    status = "partial" if partial else "complete"
    metrics_doc = {
        "schema_version": 1,
        "kind": "pilot_metrics",
        "generated_at": utc_now_iso(),
        "status": status,
        "inputs": {
            "selected_total": selected_total,
            "included_pairs": len(included),
            "missing_pairs": len(missing),
            "invalid_context_pairs": len(invalid),
            "model_id": model,
            "versions": versions,
            "budget_used": budget.get("used"),
            "budget_limit": budget.get("limit"),
            "run_metadata_status": metadata.get("status"),
            "gold_manifest_hash": (metadata.get("inputs") or {}).get("gold_manifest_hash"),
        },
        "systems": systems,
        "paired_migration_baseline_to_vnext": migration,
        "ending_profile": ending,
        "caveats": caveats,
        "claims_note": (
            "小样本 20 对为分层实验样本，不代表候选流分布；不声称 99%、新族泛化"
            "或真实人力节省；partial 状态下不做任何成功声明。"
        ),
    }

    lines = [
        "# vNext 归族 pilot 报告",
        "",
        f"- 生成时间：{metrics_doc['generated_at']}",
        f"- 状态：**{status}**"
        + ("（部分完成，不做成功声明）" if partial else ""),
        f"- 模型：{model}；版本：`{json.dumps(versions, ensure_ascii=False, sort_keys=True)}`",
        f"- 样本：selected {selected_total} 对；计入指标 {len(included)}；"
        f"缺记录 {len(missing)}；上下文失效 {len(invalid)}",
        f"- 真实请求（跨进程累计）：{budget.get('used')}/{budget.get('limit')}",
        "",
    ]
    if partial:
        warn = ["## ⚠️ 部分完成警示", ""]
        if missing:
            warn.append(f"- 缺记录 {len(missing)} 对：" + ", ".join(
                str(p.get("pair_id")) for p in missing[:5]) + (" …" if len(missing) > 5 else ""))
        if invalid:
            warn.append(f"- 上下文失效 {len(invalid)} 对（旧版本/换模型/证据漂移）。")
        if error_pairs:
            warn.append(f"- {error_pairs} 对含基础设施 error。")
        if not_run_pairs:
            warn.append(f"- {not_run_pairs} 对含 not_run。")
        lines += warn + [""]
    baseline_label = (
        "系统 1：merge-judge-v4.1（生产规则，"
        f"{metadata.get('baseline_card_max_tokens', 1200)} token 兼容运行）"
    )
    lines += _metrics_markdown("baseline", baseline_label, systems["baseline"])
    lines += _metrics_markdown(
        "vnext", "系统 2：vNext 证据卡 + 盲态复判", systems["vnext"],
    )
    lines += [
        "## 两系统并列变化（paired migration：baseline → vnext）",
        "",
        f"- 双方均已运行对数：{migration['pairs_both_ran']}",
        "",
        "| baseline \\ vnext | 对数 |",
        "|---|---|",
    ]
    for name in sorted(migration["transitions"]):
        lines.append(f"| {name} | {migration['transitions'][name]} |")
    lines += [
        "",
        "## 簇级指标（B³ / LEA / ARI）",
        "",
        (
            "- 未验证：" + systems["baseline"]["cluster_metrics"].get(
                "reason", "未实现/未触发",
            )
            if systems["baseline"]["cluster_metrics"].get("b3") == "unverified"
            else "- B³/ARI 已计算；LEA 未实现，具体见 pilot_metrics.json。"
        ),
        "",
        "## 按确认 family 的探索 bootstrap（有害错误率）",
        "",
    ]
    for system in ("baseline", "vnext"):
        boot = systems[system]["family_bootstrap_harmful_rate"]
        if boot.get("status") == "ok":
            lines.append(
                f"- {system}：point={boot['point']:.1%}，ci95=[{boot['ci95'][0]:.1%}, "
                f"{boot['ci95'][1]:.1%}]（n_boot={boot['n_boot']}，探索性）"
            )
        else:
            lines.append(f"- {system}：**{boot.get('status')}**（{boot}）")
    lines += ["", "## Caveats 与声明纪律", ""]
    for caveat in caveats:
        lines.append(f"- {caveat}")
    lines += ["", f"> {metrics_doc['claims_note']}", ""]

    metrics_path = workdir / PILOT_METRICS_NAME
    report_path = workdir / PILOT_REPORT_NAME
    write_json(metrics_path, metrics_doc)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"report 完成（status={status}）：")
    print(f"- {metrics_path}")
    print(f"- {report_path}")
    for system in ("baseline", "vnext"):
        block = systems[system]
        print(
            f"{system}: over-split {_fmt_frac(block['over_split'])}；"
            f"over-merge {_fmt_frac(block['over_merge'])}；"
            f"coverage {_fmt_frac(block['decided_coverage'])}"
        )
    return 0


# --- 子命令：diagnose --------------------------------------------------------


def _confusion_markdown(counts: dict[str, dict[str, int]]) -> list[str]:
    relations = [
        rel for rel in BACKTEST_RELATIONS
        if any(rel in per_gold for per_gold in counts.values())
    ]
    lines = ["| gold | " + " | ".join(relations + ["total"]) + " |"]
    lines.append("|" + "---|" * (len(relations) + 2))
    for gold in sorted(counts):
        per_gold = counts[gold]
        total = sum(per_gold.values())
        row = [str(per_gold.get(rel, 0)) for rel in relations] + [str(total)]
        lines.append(f"| {gold} | " + " | ".join(row) + " |")
    return lines


def cmd_diagnose(args: argparse.Namespace) -> int:
    workdir: Path = args.workdir
    workdir.mkdir(parents=True, exist_ok=True)
    v4_rows = load_backtest_jsonl(args.v4)
    v41_rows = load_backtest_jsonl(args.v4_1)

    v4_counts = confusion_counts(v4_rows)
    v41_counts = confusion_counts(v41_rows)
    transitions = relation_transitions(v4_rows, v41_rows)
    ablations = ablation_summary(v41_rows)
    same_concentration = label_concentration(v4_rows, gold="same")
    harmful_keys = {
        ("same", "different_concept"),
        ("diff", "same_variant_family"),
    }
    harmful_rows = [
        row for row in v41_rows
        if (row.get("gold"), row.get("relation")) in harmful_keys
    ]
    harmful_concentration = label_concentration(harmful_rows)

    report = {
        "schema_version": 1,
        "kind": "diagnose",
        "generated_at": utc_now_iso(),
        "status": "development_contested",
        "scope_note": (
            "旧 216 对为开发/诊断数据，非独立测试集；本诊断零 LLM 调用、纯本地重算，"
            "不构成正式测试结论，也不用于任何达标声明。"
        ),
        "inputs": {
            "v4": str(args.v4),
            "v4_1": str(args.v4_1),
            "v4_rows": len(v4_rows),
            "v4_1_rows": len(v41_rows),
        },
        "v4": {
            "confusion_gold_x_relation": v4_counts,
            "harmful_errors": harmful_error_counts(v4_rows),
        },
        "v4_1": {
            "confusion_gold_x_relation": v41_counts,
            "harmful_errors": harmful_error_counts(v41_rows),
        },
        "transitions_v4_to_v4_1": transitions,
        "label_concentration": {
            "same_gold_pairs_v4": same_concentration,
            "harmful_errors_v4_1": harmful_concentration,
        },
        "rule_ablation_on_v4_1_cards": ablations,
    }

    lines = [
        "# v4 / v4.1 旧回测诊断（development / contested）",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- 输入：v4 `{args.v4}`（{len(v4_rows)} 行）"
        f" / v4.1 `{args.v4_1}`（{len(v41_rows)} 行）",
        "- 状态：**development_contested** —— 旧 216 对是开发/诊断数据，非独立"
        "测试集；本诊断零 LLM 调用，不构成正式测试结论。",
        "",
        "## v4 混淆（gold × relation，重算）",
        "",
        *_confusion_markdown(v4_counts),
        "",
        "## v4.1 混淆（gold × relation，重算）",
        "",
        *_confusion_markdown(v41_counts),
        "",
        "## v4 → v4.1 关系迁移（按 key 关联）",
        "",
        f"- 共同 key 对：{transitions['common_pairs']}",
        f"- 新进入灰区：{len(transitions['into_grey'])} 对；离开灰区："
        f"{len(transitions['out_of_grey'])} 对",
        "",
        "| 迁移 | 对数 |",
        "|---|---|",
    ]
    for name in sorted(transitions["transitions"]):
        lines.append(f"| {name} | {transitions['transitions'][name]} |")
    lines += [
        "",
        "## 有害错误（v4.1：same→diff + diff→same）按 label 集中度",
        "",
        "| label | count | share |",
        "|---|---|---|",
    ]
    for entry in harmful_concentration:
        lines.append(
            f"| {entry['label']} | {entry['count']} | {entry['share']:.1%} |"
        )
    lines += [
        "",
        "## same gold 对（v4）按 label 集中度",
        "",
        "| label | count | share |",
        "|---|---|---|",
    ]
    for entry in same_concentration:
        lines.append(
            f"| {entry['label']} | {entry['count']} | {entry['share']:.1%} |"
        )
    lines += [
        "",
        "## 规则消融（基于 v4.1 保留 cards，development/contested）",
        "",
        "| 消融 | pairs_with_cards | same_variant_family | different_concept | grey |"
        " same_gold→diff | diff_gold→same |",
        "|---|---|---|---|---|---|---|",
    ]
    for name in ablations:
        block = ablations[name]
        relations = block["relations"]
        harmful = block["harmful_errors"]
        lines.append(
            f"| {name} | {block['pairs_with_cards']} |"
            f" {relations.get('same_variant_family', 0)} |"
            f" {relations.get('different_concept', 0)} | {relations.get('grey', 0)} |"
            f" {harmful['over_split_same_gold_to_diff']} |"
            f" {harmful['over_merge_diff_gold_to_same']} |"
        )
    lines += [
        "",
        "> 消融仅用于定位 track/ending/skeleton 各轴的权衡，"
        "不得把开发集反应当测试成绩。",
        "",
    ]

    json_path = workdir / DIAGNOSE_JSON_NAME
    md_path = workdir / DIAGNOSE_MD_NAME
    write_json(json_path, report)
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("diagnose 完成（零 LLM 调用，development/contested）：")
    print(f"- {json_path}")
    print(f"- {md_path}")
    print(
        f"v4 有害错误 {report['v4']['harmful_errors']}"
        f" / v4.1 有害错误 {report['v4_1']['harmful_errors']}"
    )
    return 0


# --- CLI --------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_merge_family_experiment",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name, handler, help_text in (
        ("prepare", cmd_prepare, "生成 13 项人工复核清单 review_universe.json"),
        ("freeze", cmd_freeze, "校验确认后冻结 78 对 gold_manifest.json"),
        ("sample", cmd_sample, "固定 seed 分层抽取 20 对写 sample20.json"),
        ("run", cmd_run, "20 对双系统判定（真实 LLM 请求，硬上限 100）"),
        ("report", cmd_report, "汇总 run 产物生成 pilot_metrics.json + pilot_report.md"),
        ("diagnose", cmd_diagnose, "v4/v4.1 旧明细零 LLM 诊断与规则消融"),
    ):
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument(
            "--workdir",
            type=Path,
            default=DEFAULT_WORKDIR,
            help=f"实验输出目录（默认 {DEFAULT_WORKDIR}；只写此目录）",
        )
        cmd.set_defaults(func=handler)
    sub.choices["prepare"].add_argument(
        "--creatives",
        required=True,
        type=Path,
        help="两组目标素材 JSON：[{name, expected_variants}, ...]，合计 13 项",
    )
    sub.choices["diagnose"].add_argument(
        "--v4", required=True, type=Path, help="v4 回测明细 JSONL 路径"
    )
    sub.choices["diagnose"].add_argument(
        "--v4-1", required=True, type=Path, help="v4.1 回测明细 JSONL 路径"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except ExperimentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
