"""Tests for missed-merge scan（services/missed_merge_scan）+ 合并后重扫。

视觉签名 / LLM 判定 / Neo4j 全部 mock；召回逻辑用合成数据直接测。
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import (
    AnalysisResult,
    Creative,
    CreativeAsset,
    CreativeDNA,
    CreativeVariant,
    JudgeSuggestion,
    SplitRuling,
)
from app.services import merge_ops, missed_merge_scan
from app.services.merge_judge import MergeJudgement
from app.services.missed_merge_scan import (
    cleanup_orphan_merge_suggestions,
    recall_pairs,
    scan_missed_merges,
    visual_overlap,
)


def _creative(
    db: Session,
    name: str,
    *,
    dna_id: str | None = None,
    state: str = "active",
    hook: str = "",
    gameplay: str = "",
) -> Creative:
    creative = Creative(
        id=str(uuid.uuid4()), name=name, dna_id=dna_id, lifecycle_state=state
    )
    asset = CreativeAsset(
        id=str(uuid.uuid4()), filename=f"KS_EN-{name}.mp4", file_type="video",
        storage_key=f"test/{uuid.uuid4()}",
    )
    db.add_all([creative, asset])
    db.flush()
    db.add(
        CreativeVariant(
            id=str(uuid.uuid4()), creative_id=creative.id,
            asset_id=asset.id, name=name,
        )
    )
    if hook or gameplay:
        db.add(
            AnalysisResult(
                id=str(uuid.uuid4()), asset_id=asset.id,
                hook=hook, gameplay=gameplay,
            )
        )
    db.flush()
    return creative


@pytest.fixture(autouse=True)
def _no_visual(monkeypatch) -> None:
    """默认不跑真实视频下载/抽帧；视觉测试自行覆盖本 fixture。"""
    monkeypatch.setattr(
        missed_merge_scan, "_visual_signatures",
        lambda db, settings, ids, emit: {},
    )


@pytest.fixture(autouse=True)
def _no_neo4j(monkeypatch) -> None:
    """本机 Neo4j 可能开着——patch 掉避免测试数据写进开发库。"""
    from app.services import graph_sync

    def _boom(_settings):  # noqa: ANN001, ANN202
        raise RuntimeError("no neo4j in tests")

    monkeypatch.setattr(graph_sync, "get_graph_repository", _boom)


class TestRecall:
    def test_text_band_hit(self, db_session: Session) -> None:
        a = _creative(db_session, "alpha-beta-gamma-delta-epsilon")
        b = _creative(db_session, "alpha-beta-x1-x2-x3")
        pairs = recall_pairs(db_session, Settings())
        pair = next(
            (p for p in pairs if {p.left.id, p.right.id} == {a.id, b.id}), None
        )
        assert pair is not None
        assert pair.text_score is not None
        assert 0.20 <= pair.text_score < 0.34

    def test_analysis_channel_hit(self, db_session: Session) -> None:
        # 名称零交集（文本路不命中），但分析文本高度重合（猪三兄弟模式）
        a = _creative(
            db_session, "alpha-x1-y1-z1",
            hook="养猪场经营生产链", gameplay="屠宰流水线传送带升级",
        )
        b = _creative(
            db_session, "omega-q9-w8-e7",
            hook="养猪生产链致富", gameplay="屠宰传送带经营升级",
        )
        pairs = recall_pairs(db_session, Settings())
        pair = next(
            (p for p in pairs if {p.left.id, p.right.id} == {a.id, b.id}), None
        )
        assert pair is not None
        assert pair.text_score is None
        assert pair.analysis_score is not None
        assert pair.analysis_score >= 0.25

    def test_visual_channel_hit(self, db_session: Session, monkeypatch) -> None:
        a = _creative(db_session, "alpha-x1-y1-z1")
        b = _creative(db_session, "omega-q9-w8-e7")
        monkeypatch.setattr(
            missed_merge_scan, "_visual_signatures",
            lambda db, settings, ids, emit: {
                a.id: [0x00, 0xFF, 0x0F0F, 0x123456789],
                b.id: [0x00, 0xFF, 0x0F0F, 0xFFFFFFFFFF],
            },
        )
        pairs = recall_pairs(db_session, Settings())
        pair = next(
            (p for p in pairs if {p.left.id, p.right.id} == {a.id, b.id}), None
        )
        assert pair is not None
        assert pair.visual_score == 0.75
        assert pair.evidence == "视觉 0.75"

    def test_exclusions(self, db_session: Session) -> None:
        dna = CreativeDNA(id=str(uuid.uuid4()), code="D1", name="同族")
        db_session.add(dna)
        db_session.flush()
        # 同族排除
        a = _creative(db_session, "alpha-beta-gamma-delta-epsilon", dna_id=dna.id)
        b = _creative(db_session, "alpha-beta-x1-x2-x3", dna_id=dna.id)
        # split_rulings 排除
        c = _creative(db_session, "gamma-one-two-three-four")
        d = _creative(db_session, "gamma-one-x7-x8-x9")
        db_session.add(
            SplitRuling(
                id=str(uuid.uuid4()), name_a=c.name, name_b=d.name,
                reason="结案", source="inbox_close",
            )
        )
        # archived 排除
        e = _creative(
            db_session, "alpha-beta-gamma-delta-archived", state="archived"
        )
        db_session.flush()
        pairs = recall_pairs(db_session, Settings())
        ids = {frozenset((p.left.id, p.right.id)) for p in pairs}
        assert frozenset((a.id, b.id)) not in ids
        assert frozenset((c.id, d.id)) not in ids
        assert all(e.id not in pair for pair in ids)

    def test_scoped_recall_only_anchor_pairs(self, db_session: Session) -> None:
        a = _creative(db_session, "alpha-beta-gamma-delta-epsilon")
        _creative(db_session, "alpha-beta-x1-x2-x3")
        c = _creative(db_session, "gamma-one-two-three-four")
        _creative(db_session, "gamma-one-x7-x8-x9")
        pairs = recall_pairs(db_session, Settings(), creative_id=c.id)
        # scoped：只有涉及 c 的对（gamma 对），不含 a 的对
        assert len(pairs) == 1
        assert c.id in (pairs[0].left.id, pairs[0].right.id)
        pairs_a = recall_pairs(db_session, Settings(), creative_id=a.id)
        assert len(pairs_a) == 1
        assert a.id in (pairs_a[0].left.id, pairs_a[0].right.id)


class TestVisualOverlap:
    def test_hamming_threshold(self) -> None:
        a = [0b0000, 0b1111]
        b = [0b0001, 0b1010]  # 0b0001 与 0b0000 距离 1；0b1010 与 0b1111 距离 2
        assert visual_overlap(a, b) == 1.0

    def test_empty(self) -> None:
        assert visual_overlap([], [1]) == 0.0
        assert visual_overlap([1], []) == 0.0


class TestOrphanCleanup:
    def test_removes_dead_side(self, db_session: Session) -> None:
        alive = _creative(db_session, "alive-one")
        orphan = JudgeSuggestion(
            id=str(uuid.uuid4()), kind="merge_pair",
            left_id=alive.id, right_id=str(uuid.uuid4()),
            verdict="merge", votes=3, reason="脏数据",
        )
        valid = JudgeSuggestion(
            id=str(uuid.uuid4()), kind="merge_pair",
            left_id=alive.id,
            right_id=_creative(db_session, "alive-two").id,
            verdict="split", votes=2, reason="正常",
        )
        db_session.add_all([orphan, valid])
        db_session.flush()
        assert cleanup_orphan_merge_suggestions(db_session) == 1
        remaining = db_session.scalars(
            select(JudgeSuggestion).where(JudgeSuggestion.kind == "merge_pair")
        ).all()
        assert [row.id for row in remaining] == [valid.id]


class TestScan:
    def _patch_judge(self, monkeypatch, same: bool) -> None:
        monkeypatch.setattr(
            missed_merge_scan, "judge_pair",
            lambda config, *, analysis_a, analysis_b: MergeJudgement(
                same_creative=same, votes=3, reason="判定理由",
            ),
        )

    def test_suggestion_written_once_idempotent(
        self, db_session: Session, monkeypatch
    ) -> None:
        _creative(db_session, "alpha-beta-gamma-delta-epsilon")
        _creative(db_session, "alpha-beta-x1-x2-x3")
        self._patch_judge(monkeypatch, same=False)
        stats1 = scan_missed_merges(
            db_session, Settings(), None, emit=lambda _m: None
        )
        assert stats1.recalled == 1
        assert stats1.suggested == 1
        stats2 = scan_missed_merges(
            db_session, Settings(), None, emit=lambda _m: None
        )
        assert stats2.suggested == 1
        rows = db_session.scalars(
            select(JudgeSuggestion).where(JudgeSuggestion.kind == "merge_pair")
        ).all()
        assert len(rows) == 1  # upsert 幂等，不重复出件
        assert "文本 0.25" in rows[0].reason

    def test_dry_run_writes_nothing(
        self, db_session: Session, monkeypatch
    ) -> None:
        _creative(db_session, "alpha-beta-gamma-delta-epsilon")
        _creative(db_session, "alpha-beta-x1-x2-x3")
        self._patch_judge(monkeypatch, same=True)
        stats = scan_missed_merges(
            db_session, Settings(), None, dry_run=True, emit=lambda _m: None
        )
        assert stats.judged_merge == 1
        assert db_session.scalars(select(JudgeSuggestion)).all() == []
        assert stats.orphans_cleaned == 0

    def test_auto_merge_with_dual_evidence(
        self, db_session: Session, monkeypatch
    ) -> None:
        from app.repositories.settings import SettingsRepository

        a = _creative(db_session, "alpha-beta-gamma-delta-epsilon")
        b = _creative(db_session, "alpha-beta-x1-x2-x3")
        self._patch_judge(monkeypatch, same=True)
        monkeypatch.setattr(
            missed_merge_scan, "measure_pair_alignment",
            lambda db, settings, x, y: 0.95,
        )
        SettingsRepository(db_session).set("merge_auto_enabled", "true")
        db_session.flush()
        stats = scan_missed_merges(
            db_session, Settings(), None, emit=lambda _m: None
        )
        assert stats.merged == 1
        # right（source）被并入 left（target）
        assert db_session.get(Creative, b.id) is None
        assert db_session.get(Creative, a.id) is not None

    def test_category_gate_blocks_auto_merge(
        self, db_session: Session, monkeypatch
    ) -> None:
        """P0-1 回归：merge_pair 类别闸踩下后，双证据齐全也只写建议。"""
        from app.repositories.settings import SettingsRepository

        a = _creative(db_session, "alpha-beta-gamma-delta-epsilon")
        b = _creative(db_session, "alpha-beta-x1-x2-x3")
        self._patch_judge(monkeypatch, same=True)
        monkeypatch.setattr(
            missed_merge_scan, "measure_pair_alignment",
            lambda db, settings, x, y: 0.95,
        )
        SettingsRepository(db_session).set("merge_auto_enabled", "true")
        SettingsRepository(db_session).set(
            "judge_auto_enabled:merge_pair", "false"
        )
        db_session.flush()
        stats = scan_missed_merges(
            db_session, Settings(), None, emit=lambda _m: None
        )
        assert stats.merged == 0
        assert stats.suggested == 1
        assert db_session.get(Creative, a.id) is not None
        assert db_session.get(Creative, b.id) is not None


class TestMergeTriggersRescan:
    def test_merge_creatives_rescans_target(
        self, db_session: Session, monkeypatch
    ) -> None:
        source = _creative(db_session, "src-one")
        target = _creative(db_session, "tgt-one")
        calls: list[str] = []
        monkeypatch.setattr(
            missed_merge_scan, "scan_for_creative",
            lambda db, settings, creative_id: calls.append(creative_id),
        )
        merge_ops.merge_creatives(
            db_session, Settings(), source.id, target.id, auto=False,
        )
        assert calls == [target.id]


class TestEmbeddingRecall:
    """第 4 路召回：representative_embedding 余弦 top-k（E2，shadow-only）。"""

    def _embed(self, db: Session, creative: Creative, vector: list[float]) -> None:
        creative.representative_embedding = vector
        db.flush()

    def test_embedding_channel_hit(self, db_session: Session) -> None:
        # 名称/分析零交集（前三路不命中），但代表向量同向 → 向量路召回
        a = _creative(db_session, "alpha-x1-y1-z1")
        b = _creative(db_session, "omega-q9-w8-e7")
        self._embed(db_session, a, [1.0, 0.0])
        self._embed(db_session, b, [1.0, 0.0])
        pairs = recall_pairs(db_session, Settings())
        pair = next(
            (p for p in pairs if {p.left.id, p.right.id} == {a.id, b.id}), None
        )
        assert pair is not None
        assert pair.text_score is None
        assert pair.analysis_score is None
        assert pair.embedding_score == pytest.approx(1.0)
        assert pair.evidence == "向量 1.00"

    def test_topk_budget(self, db_session: Session, monkeypatch) -> None:
        """top-k 预算：k=1 时每个 creative 只带最近的一个近邻进候选池。"""
        monkeypatch.setattr(missed_merge_scan, "EMBEDDING_RECALL_TOP_K", 1)
        anchor = _creative(db_session, "alpha-x1-y1-z1")
        near = _creative(db_session, "omega-q9-w8-e7")
        other = _creative(db_session, "delta-m3-n4-o5")
        self._embed(db_session, anchor, [1.0, 0.0])
        self._embed(db_session, near, [0.9, 0.1])
        self._embed(db_session, other, [0.8, 0.6])
        pairs = recall_pairs(db_session, Settings())
        keys = {frozenset((p.left.id, p.right.id)) for p in pairs}
        assert frozenset((anchor.id, near.id)) in keys
        # k=1：anchor 与 other 互为第二近邻，不进候选池
        assert frozenset((anchor.id, other.id)) not in keys

        monkeypatch.setattr(missed_merge_scan, "EMBEDDING_RECALL_TOP_K", 2)
        keys = {
            frozenset((p.left.id, p.right.id))
            for p in recall_pairs(db_session, Settings())
        }
        assert frozenset((anchor.id, other.id)) in keys

    def test_zero_cosine_not_recalled(self, db_session: Session) -> None:
        # 正交向量（余弦 0）不是近邻；无向量时行为与现状一致（三路之外无新增）
        a = _creative(db_session, "alpha-x1-y1-z1")
        b = _creative(db_session, "omega-q9-w8-e7")
        c = _creative(db_session, "delta-m3-n4-o5")
        self._embed(db_session, a, [1.0, 0.0])
        self._embed(db_session, b, [0.0, 1.0])
        # c 无向量——不影响其他人，也不被召回
        pairs = recall_pairs(db_session, Settings())
        assert pairs == []
        assert c.id not in {pid for p in pairs for pid in (p.left.id, p.right.id)}

    def test_union_with_text_channel(self, db_session: Session) -> None:
        # 文本带内 + 向量近邻双命中：同一对两路证据都在（并集语义）
        a = _creative(db_session, "alpha-beta-gamma-delta-epsilon")
        b = _creative(db_session, "alpha-beta-x1-x2-x3")
        self._embed(db_session, a, [1.0, 0.0])
        self._embed(db_session, b, [0.9, 0.1])
        pairs = recall_pairs(db_session, Settings())
        pair = next(
            (p for p in pairs if {p.left.id, p.right.id} == {a.id, b.id}), None
        )
        assert pair is not None
        assert pair.text_score is not None
        assert pair.embedding_score is not None
        assert "文本" in pair.evidence and "向量" in pair.evidence

    def test_exclusions_apply(self, db_session: Session) -> None:
        # 同族（同 DNA）即使向量同向也永不召回——排除规则四路一致
        dna = CreativeDNA(id=str(uuid.uuid4()), code="D1", name="同族")
        db_session.add(dna)
        db_session.flush()
        a = _creative(db_session, "alpha-x1-y1-z1", dna_id=dna.id)
        b = _creative(db_session, "omega-q9-w8-e7", dna_id=dna.id)
        self._embed(db_session, a, [1.0, 0.0])
        self._embed(db_session, b, [1.0, 0.0])
        assert recall_pairs(db_session, Settings()) == []

    def test_scoped_only_anchor_pairs(self, db_session: Session) -> None:
        # scoped 局部重扫：向量路也只产出 anchor 相关对（b ↔ c 不进候选池）
        a = _creative(db_session, "alpha-x1-y1-z1")
        b = _creative(db_session, "omega-q9-w8-e7")
        c = _creative(db_session, "delta-m3-n4-o5")
        for creative in (a, b, c):
            self._embed(db_session, creative, [1.0, 0.0])
        pairs = recall_pairs(db_session, Settings(), creative_id=a.id)
        assert len(pairs) == 2
        assert all(a.id in (p.left.id, p.right.id) for p in pairs)
        assert all(p.embedding_score is not None for p in pairs)


class TestEmbeddingScanFlow:
    """向量召回的后续流程：同一 judge_pair + 双证据闸门（shadow 不接管判定）。"""

    def _patch_judge(self, monkeypatch, same: bool, votes: int) -> None:
        monkeypatch.setattr(
            missed_merge_scan, "judge_pair",
            lambda config, *, analysis_a, analysis_b: MergeJudgement(
                same_creative=same, votes=votes, reason="判定理由",
            ),
        )

    def _embedded_pair(self, db_session: Session) -> tuple[Creative, Creative]:
        a = _creative(db_session, "alpha-x1-y1-z1")
        b = _creative(db_session, "omega-q9-w8-e7")
        a.representative_embedding = [1.0, 0.0]
        b.representative_embedding = [1.0, 0.0]
        db_session.flush()
        return a, b

    def test_dual_evidence_gate_still_required(
        self, db_session: Session, monkeypatch
    ) -> None:
        """向量召回的对走同一自动合并闸门：3/3 + pHash 对齐 + 双闸开才合并。"""
        from app.repositories.settings import SettingsRepository

        a, b = self._embedded_pair(db_session)
        self._patch_judge(monkeypatch, same=True, votes=3)
        monkeypatch.setattr(
            missed_merge_scan, "measure_pair_alignment",
            lambda db, settings, x, y: 0.95,
        )
        SettingsRepository(db_session).set("merge_auto_enabled", "true")
        db_session.flush()
        stats = scan_missed_merges(
            db_session, Settings(), None, emit=lambda _m: None
        )
        assert stats.recalled == 1
        assert stats.merged == 1
        assert db_session.get(Creative, b.id) is None

    def test_split_verdict_writes_suggestion_with_evidence(
        self, db_session: Session, monkeypatch
    ) -> None:
        """shadow 语义：向量路只进候选池，judge 判 split → 收件箱建议，
        reason 带向量证据分。"""
        self._embedded_pair(db_session)
        self._patch_judge(monkeypatch, same=False, votes=0)
        stats = scan_missed_merges(
            db_session, Settings(), None, emit=lambda _m: None
        )
        assert stats.merged == 0
        assert stats.suggested == 1
        row = db_session.scalars(
            select(JudgeSuggestion).where(JudgeSuggestion.kind == "merge_pair")
        ).one()
        assert "向量 1.00" in row.reason


class TestMergeRecomputesRepresentative:
    """E2 写站点口径：合并改变成员构成 → target 代表向量按成员重算均值。"""

    def test_merge_recomputes_target_representative(
        self, db_session: Session, monkeypatch
    ) -> None:
        source = _creative(db_session, "src-embed")
        target = _creative(db_session, "tgt-embed")
        # target 原代表向量来自其唯一 variant；source 的 variant 向量正交
        target.representative_embedding = [1.0, 0.0]
        target.embedding_count = 1
        for creative, vector in ((source, [0.0, 1.0]), (target, [1.0, 0.0])):
            variant = db_session.scalars(
                select(CreativeVariant).where(
                    CreativeVariant.creative_id == creative.id
                )
            ).one()
            variant.embedding = vector
        db_session.flush()
        monkeypatch.setattr(
            missed_merge_scan, "scan_for_creative",
            lambda db, settings, creative_id: None,
        )
        merge_ops.merge_creatives(
            db_session, Settings(), source.id, target.id, auto=False,
        )
        # 按成员重算（非增量）：[1,0] 与 [0,1] 的均值
        assert target.representative_embedding == [0.5, 0.5]
        assert target.embedding_count == 2
