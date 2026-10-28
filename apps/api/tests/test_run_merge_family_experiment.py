"""Gold-freezing, sampling and zero-call diagnostics for the offline experiment."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.services import merge_family_experiment as experiment
from scripts import run_merge_family_experiment as runner


def _items() -> list[dict[str, Any]]:
    items = []
    for index in range(13):
        family = "F1" if index < 7 else "F2"
        items.append({
            "review_id": f"S{index + 1:02d}",
            "variant_id": f"v{index:02d}",
            "creative_id": f"c{index:02d}",
            "creative_name": "creative-a" if family == "F1" else "creative-b",
            "asset_id": f"a{index:02d}",
            "confirmed_family_id": family,
            "confirmed_ending": "success" if family == "F1" else "fail",
            "confirmation_status": "confirmed",
            "rationale": "用户全片复核主体骨架与结局",
        })
    return items


def _universe() -> dict[str, Any]:
    return {
        "product_scope": "single_product_user_confirmed",
        "items": _items(),
    }


def _card(**overrides: Any) -> dict[str, Any]:
    card = {
        "family": "slg",
        "track": "sim-builder",
        "skeleton": "slg-zero-to-prosperity",
        "ending": "success",
    }
    card.update(overrides)
    return card


class TestGoldFreeze:
    def test_valid_universe_builds_complete_78_pair_partition(self) -> None:
        assert runner.universe_errors(_universe()) == []
        pairs = runner.build_gold_pairs(_items())
        assert len(pairs) == 78
        assert sum(pair["gold"] == "same" for pair in pairs) == 36
        assert sum(pair["gold"] == "diff" for pair in pairs) == 42
        assert len({pair["pair_id"] for pair in pairs}) == 78

    def test_confirmation_product_and_rationale_are_required(self) -> None:
        universe = _universe()
        universe["product_scope"] = "unknown"
        universe["items"][0]["confirmation_status"] = "pending"
        universe["items"][1]["rationale"] = ""
        errors = runner.universe_errors(universe)
        assert any("product_scope" in error for error in errors)
        assert any("confirmation_status" in error for error in errors)
        assert any("rationale" in error for error in errors)


class TestStratifiedSample:
    def test_deterministic_balanced_sample_and_final_counts(self) -> None:
        pairs = runner.build_gold_pairs(_items())
        first = runner.stratified_sample(pairs, seed=runner.SEED)
        second = runner.stratified_sample(pairs, seed=runner.SEED)
        assert first == second
        assert len(first["selected"]) == 20
        assert len({pair["pair_id"] for pair in first["selected"]}) == 20
        counts = first["disclosure"]["selected_by_gold"]
        assert counts == {"same": 10, "diff": 10}
        actual = {
            gold: sum(pair["gold"] == gold for pair in first["selected"])
            for gold in ("same", "diff")
        }
        assert counts == actual

    def test_backfill_is_included_in_selected_counts(self) -> None:
        items = _items()
        pairs = runner.build_gold_pairs([items[0], items[1], items[7]])
        result = runner.stratified_sample(
            pairs, seed=7, target_total=3, target_per_gold=1,
        )
        assert len(result["selected"]) == 3
        assert len(result["disclosure"]["backfilled_pair_ids"]) == 1
        assert result["disclosure"]["selected_by_gold"] == {"same": 1, "diff": 2}


class TestRuleAblation:
    def test_baseline_replicates_production_ending_wildcard(self) -> None:
        assert runner.ablate_match(_card(), _card(ending="unknown")) == "same_variant_family"
        assert runner.ablate_match(_card(), _card(ending="fail")) == "different_concept"

    def test_track_and_ending_ablation_change_only_their_own_rule(self) -> None:
        a, b = _card(), _card(track="idle-growth")
        assert runner.ablate_match(a, b) == "different_concept"
        assert runner.ablate_match(
            a, b, ignore_fields=("track",),
        ) == "same_variant_family"
        a, b = _card(), _card(ending="fail")
        assert runner.ablate_match(
            a, b, ignore_fields=("ending",),
        ) == "same_variant_family"

    def test_ablation_reports_harmful_tradeoff(self) -> None:
        rows = [
            {"gold": "same", "cards": {"a": _card(), "b": _card(track="other")}},
            {"gold": "diff", "cards": {"a": _card(), "b": _card(ending="fail")}},
        ]
        presets = {
            "baseline": {"ignore_fields": ()},
            "no_track": {"ignore_fields": ("track",)},
            "no_ending": {"ignore_fields": ("ending",)},
        }
        result = runner.ablation_summary(rows, presets)
        assert result["baseline"]["harmful_errors"] == {
            "over_split_same_gold_to_diff": 1,
            "over_merge_diff_gold_to_same": 0,
        }
        assert result["no_track"]["harmful_errors"]["over_split_same_gold_to_diff"] == 0
        assert result["no_ending"]["harmful_errors"]["over_merge_diff_gold_to_same"] == 1
        assert result["baseline"]["gold_x_relation"]["same"]["different_concept"] == 1


class TestPersistentRequestLedger:
    def test_resume_keeps_cumulative_budget_and_blocks_101(self, tmp_path: Path) -> None:
        path = tmp_path / "request_ledger.json"
        calls: list[str] = []
        real = SimpleNamespace(create=lambda **kwargs: calls.append(kwargs) or {"ok": True})
        budget = experiment.RequestBudget(limit=100)
        completions = runner._BudgetedCompletions(real, budget, path)
        completions.create(model="m")
        assert budget.used == 1
        assert json.loads(path.read_text(encoding="utf-8"))["used"] == 1

        resumed = experiment.RequestBudget(limit=100, used=runner._load_request_ledger(path))
        resumed_calls = runner._BudgetedCompletions(real, resumed, path)
        resumed_calls.create(model="m")
        assert resumed.used == 2
        assert len(calls) == 2

        exhausted = experiment.RequestBudget(limit=100, used=100)
        blocked = runner._BudgetedCompletions(real, exhausted, path)
        with pytest.raises(experiment.RequestBudgetExceeded):
            blocked.create(model="m")
        assert len(calls) == 2
        assert exhausted.used == 100

    def test_invalid_ledger_cannot_silently_reset_budget(self, tmp_path: Path) -> None:
        path = tmp_path / "request_ledger.json"
        path.write_text(json.dumps({"used": 101, "limit": 100}), encoding="utf-8")
        with pytest.raises(runner.ExperimentError):
            runner._load_request_ledger(path)

    def test_custom_budget_limit_is_persistent(self, tmp_path: Path) -> None:
        path = tmp_path / "request_ledger.json"
        real = SimpleNamespace(create=lambda **kwargs: {"ok": True})
        budget = experiment.RequestBudget(limit=20)
        completions = runner._BudgetedCompletions(real, budget, path)
        completions.create(model="m")
        assert json.loads(path.read_text(encoding="utf-8"))["limit"] == 20
        assert runner._load_request_ledger(path, expected_limit=20) == 1
        with pytest.raises(runner.ExperimentError):
            runner._load_request_ledger(path)


class TestResultReuse:
    def _context(self) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str, dict[str, str]]:
        pair = {"variant_a": "a", "variant_b": "b", "gold": "same"}
        evidence = {"a": {"evidence_hash": "ha"}, "b": {"evidence_hash": "hb"}}
        versions = {"experiment": "v1", "taxonomy": "v2", "baseline_taxonomy": "hash"}
        record = {
            "pair_id": "pair:a:b",
            "gold": "same",
            "model_id": "model-a",
            "versions": versions,
            "evidence_hash_a": "ha",
            "evidence_hash_b": "hb",
            "baseline": {"status": "same"},
            "vnext": {"status": "grey"},
        }
        return record, pair, evidence, "model-a", versions

    def test_reuse_requires_terminal_and_identical_context(self) -> None:
        record, pair, evidence, model, versions = self._context()
        assert runner._record_reusable(record, pair, model, versions, evidence)

    @pytest.mark.parametrize("mutation", ["model", "versions", "gold", "evidence", "status"])
    def test_any_context_change_invalidates_reuse(self, mutation: str) -> None:
        record, pair, evidence, model, versions = self._context()
        if mutation == "model":
            record["model_id"] = "model-b"
        elif mutation == "versions":
            record["versions"] = {**versions, "taxonomy": "changed"}
        elif mutation == "gold":
            record["gold"] = "diff"
        elif mutation == "evidence":
            record["evidence_hash_a"] = "changed"
        else:
            record["vnext"] = {"status": "not_run"}
        assert not runner._record_reusable(record, pair, model, versions, evidence)


class TestVNextStageCache:
    def _card(self) -> experiment.ExperimentCard:
        return experiment.ExperimentCard(
            content_mode="gameplay",
            game_category="slg",
            track_candidates=["sim-builder"],
            core_skeleton=None,
            core_skeleton_new="novel-loop",
            core_evidence="",
            ending="fail",
            ending_evidence="明确失败",
            loop_signature="choice-fail",
            hook_fingerprint="",
            gameplay_fingerprint="二选一玩法",
        )

    def test_adjudication_retry_reuses_differences_and_second_run_uses_both(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        diff_path = tmp_path / "differences.json"
        adj_path = tmp_path / "adjudications.json"
        diff_cache: dict[str, Any] = {}
        adj_cache: dict[str, Any] = {}
        differences = {
            "core_differences": [],
            "variant_differences": ["换前贴"],
            "evidence_gaps": [],
        }
        diff_calls: list[str] = []
        adjudication_calls: list[str] = []

        def fake_differences(*args: Any, **kwargs: Any) -> dict[str, Any]:
            diff_calls.append("called")
            return differences

        outcomes = [
            None,
            experiment.Adjudication("same", "core", "主体与结局一致"),
        ]

        def fake_adjudication(*args: Any, **kwargs: Any) -> Any:
            adjudication_calls.append("called")
            return outcomes.pop(0)

        monkeypatch.setattr(runner, "generate_differences", fake_differences)
        monkeypatch.setattr(runner, "adjudicate_differences", fake_adjudication)
        monkeypatch.setattr(runner, "_retry_sleep", lambda _attempt: None)
        config = SimpleNamespace(vision_model="model-a", judge_temperature=1.0)
        budget = experiment.RequestBudget(limit=100)
        first = runner._judge_vnext_pair(
            config, budget, "证据甲", "证据乙", self._card(), self._card(),
            diff_cache, diff_path, adj_cache, adj_path,
        )
        assert first["status"] == "same"
        assert len(diff_calls) == 1
        assert len(adjudication_calls) == 2
        assert diff_path.exists() and adj_path.exists()

        def unexpected(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("成功复判缓存不应再次调用 LLM")

        monkeypatch.setattr(runner, "generate_differences", unexpected)
        monkeypatch.setattr(runner, "adjudicate_differences", unexpected)
        second = runner._judge_vnext_pair(
            config, experiment.RequestBudget(limit=100), "证据乙", "证据甲",
            self._card(), self._card(),
            runner._load_card_cache(diff_path), diff_path,
            runner._load_card_cache(adj_path), adj_path,
        )
        assert second["status"] == "same"
        assert second["differences"] == differences


class TestPilotMetrics:
    def test_metric_denominators_keep_grey_error_but_exclude_not_run(self) -> None:
        included = [
            ({"gold": "same"}, {
                "baseline": {"status": "diff"}, "vnext": {"status": "same"},
            }),
            ({"gold": "same"}, {
                "baseline": {"status": "grey"}, "vnext": {"status": "same"},
            }),
            ({"gold": "diff"}, {
                "baseline": {"status": "same"}, "vnext": {"status": "diff"},
            }),
            ({"gold": "diff"}, {
                "baseline": {"status": "not_run"}, "vnext": {"status": "error"},
            }),
        ]
        baseline = runner._system_metrics(included, "baseline", 4)
        assert baseline["over_split"]["value"] == 0.5
        assert baseline["over_merge"]["value"] == 1.0
        assert baseline["decided_coverage"]["value"] == pytest.approx(2 / 3)
        assert baseline["manual_review_rate"]["value"] == pytest.approx(1 / 3)
        assert baseline["not_run_total"] == 1

        vnext = runner._system_metrics(included, "vnext", 4)
        assert vnext["over_split"]["value"] == 0.0
        assert vnext["over_merge"]["value"] == 0.0
        assert vnext["decided_coverage"]["value"] == 0.75
        assert vnext["error_rate"]["value"] == 0.25
        assert vnext["precision_same"]["value"] == 1.0
        assert vnext["precision_diff"]["value"] == 1.0

    def test_two_families_cannot_produce_bootstrap_interval(self) -> None:
        included = [({
            "variant_a": "a", "variant_b": "b", "gold": "same",
        }, {"baseline": {"status": "same"}})]
        result = runner._family_bootstrap(
            included, {"a": "F1", "b": "F2"}, "baseline",
        )
        assert result["status"] == "insufficient_independent_families"
        assert result["n_units"] == 2


def _report_fixture(root: Path) -> dict[str, Any]:
    pair = {
        "pair_id": "pair:a:b", "variant_a": "a", "variant_b": "b", "gold": "diff",
        "family_a": "F1", "family_b": "F2", "ending_a": "fail", "ending_b": "fail",
    }
    manifest = {
        "pairs": [pair],
        "items": [
            {"variant_id": "a", "confirmed_family_id": "F1", "confirmed_ending": "fail"},
            {"variant_id": "b", "confirmed_family_id": "F2", "confirmed_ending": "fail"},
        ],
        "evidence_snapshot": {
            "a": {"evidence_hash": "ha"}, "b": {"evidence_hash": "hb"},
        },
    }
    runner.write_json(root / runner.GOLD_MANIFEST_NAME, manifest)
    digest = runner.sha256_text((root / runner.GOLD_MANIFEST_NAME).read_text(encoding="utf-8"))
    runner.write_json(root / runner.SAMPLE_NAME, {
        "selected": [pair], "gold_manifest_hash": digest,
    })
    versions = {"experiment": "v1", "taxonomy": "v2"}
    metadata = {
        "status": "partial",
        "model_id": "model-a",
        "versions": versions,
        "budget": {"used": 1, "limit": 100},
        "inputs": {"gold_manifest_hash": digest},
    }
    runner.write_json(root / runner.RUN_METADATA_NAME, metadata)
    record = {
        "pair_id": "pair:a:b", "gold": "diff", "variant_a": "a", "variant_b": "b",
        "model_id": "model-a", "versions": versions,
        "evidence_hash_a": "ha", "evidence_hash_b": "hb",
        "baseline": {"status": "error"}, "vnext": {"status": "same"},
    }
    runner._write_results_atomic(root / runner.RESULTS_NAME, [record])
    return record


class TestReportContract:
    def test_error_record_is_included_but_report_stays_partial(self, tmp_path: Path) -> None:
        _report_fixture(tmp_path)
        assert runner.cmd_report(SimpleNamespace(workdir=tmp_path)) == 0
        metrics = json.loads((tmp_path / runner.PILOT_METRICS_NAME).read_text(encoding="utf-8"))
        assert metrics["status"] == "partial"
        assert metrics["systems"]["baseline"]["error_rate"]["value"] == 1.0
        assert metrics["systems"]["baseline"]["over_merge"]["value"] == 0.0
        assert metrics["systems"]["vnext"]["over_merge"]["value"] == 1.0
        assert any("error" in caveat for caveat in metrics["caveats"])

    def test_stale_sample_hash_is_rejected(self, tmp_path: Path) -> None:
        _report_fixture(tmp_path)
        sample_path = tmp_path / runner.SAMPLE_NAME
        sample = json.loads(sample_path.read_text(encoding="utf-8"))
        sample["gold_manifest_hash"] = "stale"
        runner.write_json(sample_path, sample)
        with pytest.raises(runner.ExperimentError, match="sample"):
            runner.cmd_report(SimpleNamespace(workdir=tmp_path))


class TestRunLock:
    def test_concurrent_run_is_rejected_and_lock_releases(self, tmp_path: Path) -> None:
        handle = (tmp_path / "run.lock").open("w", encoding="utf-8")
        runner._lock_exclusive(handle)
        try:
            with pytest.raises(runner.ExperimentError, match="另一个 run"):
                runner.cmd_run(SimpleNamespace(workdir=tmp_path))
        finally:
            runner._unlock_exclusive(handle)
            handle.close()
        with runner._workdir_run_lock(tmp_path):
            pass


class TestTemperatureCacheNamespace:
    def test_temperature_changes_all_vnext_cache_keys(self) -> None:
        old = SimpleNamespace(vision_model="kimi-for-coding", judge_temperature=0.7)
        new = SimpleNamespace(vision_model="kimi-for-coding", judge_temperature=1.0)
        old_model = runner._cache_model_namespace(old)
        new_model = runner._cache_model_namespace(new)
        assert old_model != new_model
        assert old_model == "kimi-for-coding|temperature=0.7"
        assert new_model == "kimi-for-coding|temperature=1.0"
        assert runner._vnext_card_cache_key("证据", old_model) != (
            runner._vnext_card_cache_key("证据", new_model)
        )
        assert runner._baseline_card_cache_key("证据", old_model) != (
            runner._baseline_card_cache_key("证据", new_model)
        )


class TestBaselineTokenBudget:
    def test_explicit_compat_budget_and_unchanged_production_default(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: list[int] = []

        def fake_complete(*args: Any, **kwargs: Any) -> dict[str, Any]:
            captured.append(kwargs["max_tokens"])
            return _card()

        monkeypatch.setattr(runner.fingerprint_card, "complete_json", fake_complete)
        config = SimpleNamespace()
        card = runner.fingerprint_card.build_card(
            config, "证据", max_tokens=runner.BASELINE_CARD_MAX_TOKENS,
        )
        assert card is not None
        assert runner.fingerprint_card.build_card(config, "证据") is not None
        assert captured == [2400, 1200]


class TestResultLoading:
    def test_source_pair_id_is_accepted(self, tmp_path: Path) -> None:
        path = tmp_path / "guided_results.jsonl"
        path.write_text('{"source_pair_id": "pair:a:b", "guided_status": "same"}\n', encoding="utf-8")
        rows = runner._load_results(path)
        assert rows == [{"source_pair_id": "pair:a:b", "guided_status": "same"}]

    def test_missing_pair_id_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.jsonl"
        path.write_text('{"guided_status": "same"}\n', encoding="utf-8")
        with pytest.raises(runner.ExperimentError, match="source_pair_id"):
            runner._load_results(path)


class TestCreativeConfig:
    def test_external_two_group_config_is_supported(self, tmp_path: Path) -> None:
        path = tmp_path / "creatives.json"
        path.write_text(json.dumps([
            {"name": "demo-alpha", "expected_variants": 7},
            {"name": "demo-beta", "expected_variants": 6},
        ]), encoding="utf-8")
        assert runner._target_creatives(SimpleNamespace(creatives=path)) == [
            ("demo-alpha", 7), ("demo-beta", 6),
        ]

    def test_config_is_required(self) -> None:
        with pytest.raises(runner.ExperimentError, match="--creatives"):
            runner._target_creatives(SimpleNamespace(creatives=None))

    def test_duplicate_names_and_wrong_total_are_rejected(self, tmp_path: Path) -> None:
        duplicate = tmp_path / "duplicate.json"
        duplicate.write_text(json.dumps([
            {"name": "demo-alpha", "expected_variants": 7},
            {"name": "demo-alpha", "expected_variants": 6},
        ]), encoding="utf-8")
        with pytest.raises(runner.ExperimentError, match="重复"):
            runner._target_creatives(SimpleNamespace(creatives=duplicate))
        wrong_total = tmp_path / "wrong-total.json"
        wrong_total.write_text(json.dumps([
            {"name": "demo-alpha", "expected_variants": 4},
            {"name": "demo-beta", "expected_variants": 4},
        ]), encoding="utf-8")
        with pytest.raises(runner.ExperimentError, match="合计"):
            runner._target_creatives(SimpleNamespace(creatives=wrong_total))
