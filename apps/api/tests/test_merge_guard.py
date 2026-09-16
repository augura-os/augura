"""Tests for the merge guard (services/merge_guard) + case-rulings consistency."""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.models import Creative
from app.services.merge_guard import check_merge, find_prior_ruling

# docs/case-rulings.json 是私有部署的业务裁决录（公开版无此文件）——
# 依赖它的用例在文件缺失时跳过，其余用例不受影响。
_RULINGS_FILE = Path(__file__).resolve().parents[3] / "docs" / "case-rulings.json"
requires_rulings_file = pytest.mark.skipif(
    not _RULINGS_FILE.exists(),
    reason="docs/case-rulings.json 为私有文件（公开版不存在）",
)


def _creative(name: str, dna_id: str | None = None) -> Creative:
    return Creative(id=str(uuid.uuid4()), name=name, dna_id=dna_id)


@requires_rulings_file
class TestPriorRuling:
    def test_case10_pair_is_blocked(self) -> None:
        ruling = find_prior_ruling(
            "no-ads-vip-pass-island-settlement", "no-ads-pure-building"
        )
        assert ruling is not None
        assert ruling["case"] == 10

    def test_order_insensitive(self) -> None:
        assert find_prior_ruling(
            "no-ads-pure-building", "no-ads-vip-pass-island-settlement"
        ) is not None

    def test_unknown_pair_not_blocked(self) -> None:
        assert find_prior_ruling("foo", "bar") is None


class TestCheckMerge:
    @requires_rulings_file
    def test_block_hit_for_ruled_pair(self) -> None:
        hits = check_merge(
            _creative("no-ads-pure-building"),
            _creative("no-ads-vip-pass-island-settlement"),
        )
        assert any(hit.level == "block" and hit.check == "prior_ruling" for hit in hits)

    def test_cross_dna_warn(self) -> None:
        hits = check_merge(
            _creative("alpha-beta-gamma", dna_id="d1"),
            _creative("alpha-beta-delta", dna_id="d2"),
        )
        assert any(hit.check == "cross_dna" and hit.level == "warn" for hit in hits)

    def test_same_dna_no_warn(self) -> None:
        hits = check_merge(
            _creative("alpha-beta-gamma", dna_id="d1"),
            _creative("alpha-beta-delta", dna_id="d1"),
        )
        assert all(hit.check != "cross_dna" for hit in hits)

    def test_low_similarity_warn(self) -> None:
        hits = check_merge(_creative("aaa-bbb"), _creative("zzz-yyy"))
        assert any(hit.check == "low_similarity" for hit in hits)

    def test_clean_merge_no_hits(self) -> None:
        hits = check_merge(
            _creative("fail-retry-island-survival", dna_id="d1"),
            _creative("fail-retry-island-survival-es", dna_id="d1"),
        )
        assert hits == []


class TestMissingRulingsFile:
    def test_missing_file_degrades_to_empty(self, monkeypatch, tmp_path) -> None:
        """Docker images without docs/ mounted must not 500 — degrade to no rulings."""
        from app.services import merge_guard

        monkeypatch.setattr(merge_guard, "_RULINGS_PATH", tmp_path / "nope.json")
        merge_guard._rulings.cache_clear()
        try:
            assert find_prior_ruling(
                "no-ads-pure-building", "no-ads-vip-pass-island-settlement"
            ) is None
            hits = check_merge(
                _creative("no-ads-pure-building"),
                _creative("no-ads-vip-pass-island-settlement"),
            )
            assert all(hit.check != "prior_ruling" for hit in hits)
        finally:
            merge_guard._rulings.cache_clear()


class TestDbRuling:
    """split_rulings 表（收件箱结案沉淀）与 JSON 裁决录并行生效。"""

    def _rule(self, db: Session, a: str, b: str) -> None:
        from app.models import SplitRuling

        low, high = sorted((a, b))
        db.add(SplitRuling(id=str(uuid.uuid4()), name_a=low, name_b=high,
                           reason="测试结案", source="inbox_close"))
        db.flush()

    def test_find_db_ruling_order_insensitive(self, db_session: Session) -> None:
        from app.services.merge_guard import find_db_ruling

        self._rule(db_session, "alpha-one", "beta-two")
        assert find_db_ruling(db_session, "beta-two", "alpha-one") is not None
        assert find_db_ruling(db_session, "alpha-one", "zzz") is None

    def test_check_merge_blocks_db_ruled_pair(self, db_session: Session) -> None:
        self._rule(db_session, "gamma-a", "gamma-b")
        hits = check_merge(
            _creative("gamma-a"), _creative("gamma-b"), db_session
        )
        assert any(hit.level == "block" and hit.check == "prior_ruling" for hit in hits)

    def test_check_merge_without_db_ignores_table(self, db_session: Session) -> None:
        # 不传 db 时保持旧行为（只读 JSON 裁决录）
        self._rule(db_session, "delta-a", "delta-b")
        hits = check_merge(_creative("delta-a"), _creative("delta-b"))
        assert all(hit.check != "prior_ruling" for hit in hits)

    def test_stale_ruling_degrades_to_warn(self, db_session: Session) -> None:
        """超过 90 天的结案裁决降级为 warn（不再强制理由）。"""
        from datetime import datetime, timedelta, timezone

        from app.models import SplitRuling

        low, high = sorted(("old-a", "old-b"))
        ruling = SplitRuling(
            id=str(uuid.uuid4()), name_a=low, name_b=high,
            reason="旧结案", source="inbox_close",
        )
        ruling.created_at = datetime.now(timezone.utc) - timedelta(days=120)
        db_session.add(ruling)
        db_session.flush()
        hits = check_merge(_creative("old-a"), _creative("old-b"), db_session)
        ruling_hits = [hit for hit in hits if hit.check == "prior_ruling"]
        assert len(ruling_hits) == 1
        assert ruling_hits[0].level == "warn"
        assert "120" in ruling_hits[0].message


@requires_rulings_file
class TestCaseRulingsConsistency:
    def test_json_covers_documented_split_cases(self) -> None:
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[3]
        rulings = json.loads(
            (repo_root / "docs" / "case-rulings.json").read_text(encoding="utf-8")
        )
        cases = {ruling["case"] for ruling in rulings}
        # 边界规则文档中的"维持拆分/拆分"案例必须在机器可读裁决录里
        assert {2, 5, 7, 8, 9, 10} <= cases
        doc = (repo_root / "docs" / "creative-boundary-rules.md").read_text(
            encoding="utf-8"
        )
        for case in (2, 5, 7, 8, 9, 10):
            assert re.search(rf"案例 {case} ", doc), f"文档缺案例 {case}"
