"""Tests for the review queue heuristics (services/review)."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AnalysisResult,
    Creative,
    CreativeAsset,
    CreativeDNA,
    CreativeVariant,
    JudgeSuggestion,
    VariantDerivation,
)
from app.services.review import (
    _market_key,
    derivation_review_items,
    dna_unassigned_items,
    low_confidence_items,
    merge_candidate_items,
    short_label,
)


def _seed(
    db: Session,
    *,
    creative_name: str,
    filename: str,
    dna_id: str | None = None,
    confidence: float | None = None,
) -> tuple[Creative, CreativeAsset]:
    creative = Creative(id=str(uuid.uuid4()), name=creative_name, dna_id=dna_id)
    asset = CreativeAsset(
        id=str(uuid.uuid4()), filename=filename, file_type="video",
        storage_key=f"test/{uuid.uuid4()}",
    )
    db.add_all([creative, asset])
    db.flush()
    db.add(
        CreativeVariant(
            id=str(uuid.uuid4()), creative_id=creative.id, asset_id=asset.id, name="v1"
        )
    )
    if confidence is not None:
        db.add(
            AnalysisResult(
                id=str(uuid.uuid4()), asset_id=asset.id, confidence=confidence
            )
        )
    db.flush()
    return creative, asset


class TestMarketKey:
    def test_strips_market_prefix(self) -> None:
        pt = _market_key("KS_EN-260611-58-制作人甲-屡次失败重开Ai片头V1-竖.mp4")
        es = _market_key("KS_KR-260611-58-制作人甲-屡次失败重开Ai片头V1-竖.mp4")
        assert pt == es

    def test_different_names_stay_different(self) -> None:
        assert _market_key("KS_EN-a-混乱管理.mp4") != _market_key("KS_KR-a-围栏防御.mp4")


class TestShortLabel:
    def test_market_and_tail(self) -> None:
        label = short_label("KS_EN-260611-58-制作人甲-模拟经营-AI-制作人丁-屡次失败重开Ai片头V1-竖.mp4")
        # 市场码口径：KS_EN → 后缀 EN → 别名归一 US
        assert label == "US …屡次失败重开Ai片头V1-竖"

    def test_es_market(self) -> None:
        label = short_label("KS_KR-260630-58-制作人己-模拟经营-AI前贴_抓马-制作人乙-黑心老板.mp4")
        assert label == "KR …制作人乙-黑心老板"

    def test_no_market_prefix(self) -> None:
        assert short_label("foo-bar-baz.mp4") == "…bar-baz"

    def test_short_tail_expands_to_three_segments(self) -> None:
        assert short_label("KS_KR-260603-58-a-四季解说Ai片头1-竖.mp4") == "KR …四季解说Ai片头1-竖"


class TestLowConfidence:
    def test_flags_below_threshold(self, db_session: Session) -> None:
        _seed(db_session, creative_name="c-low", filename="KS_EN-low.mp4", confidence=0.55)
        _seed(db_session, creative_name="c-high", filename="KS_EN-high.mp4", confidence=0.9)
        items = low_confidence_items(db_session)
        assert [item.title for item in items] == ["KS_EN-low.mp4"]
        assert items[0].asset_id is not None


class TestDnaUnassigned:
    def test_flags_null_dna_only(self, db_session: Session) -> None:
        dna = CreativeDNA(id=str(uuid.uuid4()), code="D98", name="测试家族")
        db_session.add(dna)
        db_session.flush()
        _seed(db_session, creative_name="c-no-dna", filename="KS_EN-a.mp4")
        _seed(db_session, creative_name="c-has-dna", filename="KS_EN-b.mp4",
              dna_id=dna.id)
        items = dna_unassigned_items(db_session)
        assert [item.creative_name for item in items] == ["c-no-dna"]


class TestMergeCandidates:
    def test_market_pair_across_creatives_flagged(self, db_session: Session) -> None:
        pt, _ = _seed(db_session, creative_name="c-pt",
              filename="KS_EN-260611-58-制作人甲-屡次失败重开Ai片头V1-竖.mp4")
        es, _ = _seed(db_session, creative_name="c-es",
              filename="KS_KR-260611-58-制作人甲-屡次失败重开Ai片头V1-竖.mp4")
        items = [i for i in merge_candidate_items(db_session)
                 if "语言对" in i.reason]
        assert len(items) == 1
        assert "↔" in items[0].title
        # 内联合并操作需要双方 id（收件箱直操 UX）
        assert items[0].creative_id == pt.id
        assert items[0].related_creative_id == es.id

    def test_market_pair_same_creative_not_flagged(self, db_session: Session) -> None:
        creative = Creative(id=str(uuid.uuid4()), name="c-same")
        db_session.add(creative)
        db_session.flush()
        for market in ("KS_EN", "KS_KR"):
            asset = CreativeAsset(
                id=str(uuid.uuid4()),
                filename=f"{market}-260611-58-制作人甲-屡次失败重开Ai片头V1-竖.mp4",
                file_type="video", storage_key=f"test/{uuid.uuid4()}",
            )
            db_session.add(asset)
            db_session.flush()
            db_session.add(
                CreativeVariant(
                    id=str(uuid.uuid4()), creative_id=creative.id,
                    asset_id=asset.id, name=market,
                )
            )
        db_session.flush()
        assert [i for i in merge_candidate_items(db_session)
                if "语言对" in i.reason] == []

    def test_borderline_similarity_window(self, db_session: Session) -> None:
        # Jaccard = 2 shared / 8 union ≈ 0.25 → inside [0.20, 0.34).
        _seed(db_session, creative_name="alpha-beta-gamma-delta-epsilon",
              filename="KS_EN-1.mp4")
        _seed(db_session, creative_name="alpha-beta-x1-x2-x3", filename="KS_EN-2.mp4")
        items = merge_candidate_items(db_session)
        borderline = [i for i in items if "相似度" in i.reason]
        assert len(borderline) == 1

    def test_identical_names_not_borderline(self, db_session: Session) -> None:
        _seed(db_session, creative_name="same-name", filename="KS_EN-1.mp4")
        _seed(db_session, creative_name="same-name", filename="KS_EN-2.mp4")
        items = [i for i in merge_candidate_items(db_session) if "相似度" in i.reason]
        # score 1.0 is above BORDERLINE_HIGH, must not be reported
        assert items == []

    def test_generic_suffix_alone_not_candidate(self, db_session: Session) -> None:
        # 仅共享 "-village-builder" 通用后缀（peasant-son ↔ sick-girl 误报案）
        _seed(db_session, creative_name="peasant-son-confronts-father-village-builder",
              filename="KS_EN-a1.mp4")
        _seed(db_session, creative_name="sick-girl-infirmary-village-builder",
              filename="KS_EN-a2.mp4")
        items = [i for i in merge_candidate_items(db_session) if "相似度" in i.reason]
        assert items == []

    def test_unrelated_names_not_borderline(self, db_session: Session) -> None:
        _seed(db_session, creative_name="aaa", filename="KS_EN-1.mp4")
        _seed(db_session, creative_name="zzz", filename="KS_EN-2.mp4")
        items = [i for i in merge_candidate_items(db_session) if "相似度" in i.reason]
        assert items == []


class TestObservationPairExclusion:
    """已登记"维持拆分"（SIMILAR_TO）的组合不得再回候选列表（刷新还原 bug）。"""

    def _stub_repo(self, monkeypatch, pairs: list[tuple[str, str]]) -> None:
        from app.services import graph_sync

        class _Repo:
            def read_similar_pairs(self) -> list[tuple[str, str]]:
                return pairs

        monkeypatch.setattr(
            graph_sync, "get_graph_repository", lambda _settings: _Repo()
        )

    def test_observed_market_pair_not_flagged(
        self, db_session: Session, monkeypatch
    ) -> None:
        pt, _ = _seed(db_session, creative_name="c-pt",
              filename="KS_EN-260611-58-制作人甲-屡次失败重开Ai片头V1-竖.mp4")
        es, _ = _seed(db_session, creative_name="c-es",
              filename="KS_KR-260611-58-制作人甲-屡次失败重开Ai片头V1-竖.mp4")
        self._stub_repo(monkeypatch, [(pt.id, es.id)])
        assert [i for i in merge_candidate_items(db_session)
                if "语言对" in i.reason] == []

    def test_observed_borderline_pair_not_flagged(
        self, db_session: Session, monkeypatch
    ) -> None:
        left, _ = _seed(db_session, creative_name="alpha-beta-gamma-delta-epsilon",
              filename="KS_EN-1.mp4")
        right, _ = _seed(db_session, creative_name="alpha-beta-x1-x2-x3",
              filename="KS_EN-2.mp4")
        self._stub_repo(monkeypatch, [(left.id, right.id)])
        assert [i for i in merge_candidate_items(db_session)
                if "相似度" in i.reason] == []

    def test_other_pairs_still_flagged(
        self, db_session: Session, monkeypatch
    ) -> None:
        pt, _ = _seed(db_session, creative_name="c-pt",
              filename="KS_EN-260611-58-制作人甲-屡次失败重开Ai片头V1-竖.mp4")
        _seed(db_session, creative_name="c-es",
              filename="KS_KR-260611-58-制作人甲-屡次失败重开Ai片头V1-竖.mp4")
        # 观察对里是不相关的另一对，不影响本对浮出水面
        self._stub_repo(monkeypatch, [(pt.id, str(uuid.uuid4()))])
        assert len([i for i in merge_candidate_items(db_session)
                    if "语言对" in i.reason]) == 1


class TestSplitRulingExclusion:
    """已结案（split_rulings）的组合永久排除出候选（结案回流漏洞回归测试）。"""

    def _rule(self, db: Session, a: str, b: str) -> None:
        from app.models import SplitRuling

        low, high = sorted((a, b))
        db.add(SplitRuling(id=str(uuid.uuid4()), name_a=low, name_b=high,
                           reason="结案", source="inbox_close"))
        db.flush()

    def test_ruled_market_pair_not_flagged(self, db_session: Session) -> None:
        _seed(db_session, creative_name="c-pt",
              filename="KS_EN-260611-58-制作人甲-屡次失败重开Ai片头V1-竖.mp4")
        _seed(db_session, creative_name="c-es",
              filename="KS_KR-260611-58-制作人甲-屡次失败重开Ai片头V1-竖.mp4")
        self._rule(db_session, "c-pt", "c-es")
        assert [i for i in merge_candidate_items(db_session)
                if "语言对" in i.reason] == []

    def test_ruled_borderline_pair_not_flagged(self, db_session: Session) -> None:
        _seed(db_session, creative_name="alpha-beta-gamma-delta-epsilon",
              filename="KS_EN-1.mp4")
        _seed(db_session, creative_name="alpha-beta-x1-x2-x3", filename="KS_EN-2.mp4")
        self._rule(db_session, "alpha-beta-gamma-delta-epsilon", "alpha-beta-x1-x2-x3")
        assert [i for i in merge_candidate_items(db_session)
                if "相似度" in i.reason] == []


class TestRecentCreativeIds:
    def test_returns_only_recent(self, db_session: Session) -> None:
        from datetime import datetime, timedelta, timezone

        from app.api.routes.creatives import recent_creative_ids

        fresh = Creative(id=str(uuid.uuid4()), name="fresh-one")
        stale = Creative(id=str(uuid.uuid4()), name="stale-one")
        stale.created_at = datetime.now(timezone.utc) - timedelta(days=7)
        db_session.add_all([fresh, stale])
        db_session.flush()
        result = recent_creative_ids(db_session, hours=48)
        assert result.success is True
        assert fresh.id in result.data
        assert stale.id not in result.data


class TestDerivationReviewItems:
    """derivation-factor 建议的收件箱条目（测量层/LLM 复核产出）。"""

    def _seed_derivation(
        self, db: Session, *, factor: str = "unknown"
    ) -> tuple[Creative, VariantDerivation]:
        creative = Creative(id=str(uuid.uuid4()), name="deriv-review")
        db.add(creative)
        db.flush()
        variants: list[CreativeVariant] = []
        for tag in ("V1", "V2"):
            asset = CreativeAsset(
                id=str(uuid.uuid4()),
                filename=f"KS_EN-260630-58-制作人甲-测试素材{tag}-竖.mp4",
                file_type="video",
                storage_key=f"test/{uuid.uuid4()}",
            )
            db.add(asset)
            db.flush()
            variant = CreativeVariant(
                id=str(uuid.uuid4()), creative_id=creative.id,
                asset_id=asset.id, name=tag,
            )
            db.add(variant)
            variants.append(variant)
        derivation = VariantDerivation(
            id=str(uuid.uuid4()),
            source_variant_id=variants[0].id,
            target_variant_id=variants[1].id,
            factor=factor,
        )
        db.add(derivation)
        db.flush()
        return creative, derivation

    def _suggest(self, db: Session, derivation_id: str, verdict: str) -> None:
        db.add(
            JudgeSuggestion(
                id=str(uuid.uuid4()), kind="derivation-factor",
                left_id=derivation_id, right_id=None,
                verdict=verdict, votes=3, reason="测量证据abc",
            )
        )
        db.flush()

    def test_not_a_derivation_reason(self, db_session: Session) -> None:
        creative, derivation = self._seed_derivation(db_session)
        self._suggest(db_session, derivation.id, "not-a-derivation")
        items = derivation_review_items(db_session)
        assert len(items) == 1
        item = items[0]
        assert item.kind == "derivation_review"
        assert item.reason.startswith("疑似误链：测量证据abc")
        assert item.derivation_id == derivation.id
        assert item.creative_id == creative.id
        assert item.factor == "unknown"
        assert item.suggestion == "not-a-derivation"
        assert item.suggestion_votes == 3
        assert item.source_label and item.target_label

    def test_factor_suggestion_reason(self, db_session: Session) -> None:
        _, derivation = self._seed_derivation(db_session)
        self._suggest(db_session, derivation.id, "language-market")
        items = derivation_review_items(db_session)
        assert len(items) == 1
        assert items[0].reason == "因子建议改为 language-market：测量证据abc"

    def test_adopted_suggestion_skipped(self, db_session: Session) -> None:
        _, derivation = self._seed_derivation(db_session, factor="language-market")
        self._suggest(db_session, derivation.id, "language-market")
        assert derivation_review_items(db_session) == []

    def test_orphan_suggestion_cleaned(self, db_session: Session) -> None:
        self._suggest(db_session, str(uuid.uuid4()), "not-a-derivation")
        assert derivation_review_items(db_session) == []
        remaining = db_session.scalars(
            select(JudgeSuggestion).where(
                JudgeSuggestion.kind == "derivation-factor"
            )
        ).all()
        assert remaining == []

    def test_scanner_suggested_pair_union_into_inbox(self, db_session: Session) -> None:
        """扫描器召回的对（视觉/分析通道，文本分低于候选下限）有 merge 建议时
        必须出现在收件箱——否则建议躺在 judge_suggestions 里没人看见
        （猪三兄弟教训：slaughterhouse 对文本 0.10 从未浮出）。"""
        left, _ = _seed(db_session, creative_name="zz-alpha-pond",
                        filename="KS_EN-a1.mp4")
        right, _ = _seed(db_session, creative_name="qq-omega-fjord",
                         filename="KS_EN-b2.mp4")
        db_session.add(
            JudgeSuggestion(
                id=str(uuid.uuid4()), kind="merge_pair", left_id=left.id,
                right_id=right.id, verdict="merge", votes=3, reason="视觉 0.81",
            )
        )
        db_session.flush()
        items = merge_candidate_items(db_session)
        hit = [
            i for i in items
            if {left.id, right.id} == {i.creative_id, i.related_creative_id}
        ]
        assert len(hit) == 1
        assert hit[0].suggestion == "merge"
        assert hit[0].suggestion_votes == 3
        assert "扫描器召回" in hit[0].reason

    def test_scanner_split_suggestion_not_surfaced(self, db_session: Session) -> None:
        """split 建议的对不并入（确认不同，无收件箱价值）。"""
        left, _ = _seed(db_session, creative_name="zz-quartz-anvil",
                        filename="KS_EN-a1.mp4")
        right, _ = _seed(db_session, creative_name="qq-velvet-orchid",
                         filename="KS_EN-b2.mp4")
        db_session.add(
            JudgeSuggestion(
                id=str(uuid.uuid4()), kind="merge_pair", left_id=left.id,
                right_id=right.id, verdict="split", votes=3, reason="机制不同",
            )
        )
        db_session.flush()
        assert [
            i for i in merge_candidate_items(db_session)
            if {left.id, right.id} == {i.creative_id, i.related_creative_id}
        ] == []
