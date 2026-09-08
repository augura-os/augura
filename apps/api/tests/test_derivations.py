"""Tests for derivations: backfill factor inference + evolution endpoint logic."""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy.orm import Session

from app.models import Creative, CreativeAsset, CreativeVariant, Performance
from app.repositories.derivations import DerivationRepository
from scripts.backfill_derivations import VariantInfo, infer_factor


def _vi(filename: str, factors: set[str] | None = None) -> VariantInfo:
    return VariantInfo(
        variant_id=str(uuid.uuid4()),
        name=filename[:20],
        filename=filename,
        date_key="260101",
        factors=factors or set(),
    )


class TestInferFactor:
    def test_market_prefix_wins(self) -> None:
        source = _vi("KS_EN-260630-a-竖.mp4", {"language-market", "aspect-ratio"})
        target = _vi("KS_KR-260630-a-竖.mp4", {"language-market"})
        assert infer_factor(source, target) == "language-market"

    def test_aspect_ratio_diff(self) -> None:
        source = _vi("KS_EN-a.mp4", {"language-market"})
        target = _vi("KS_EN-a-竖.mp4", {"language-market", "aspect-ratio"})
        assert infer_factor(source, target) == "aspect-ratio"

    def test_both_intro_sticker_is_sticker_swap(self) -> None:
        source = _vi("KS_EN-a-V1-竖.mp4", {"language-market", "intro-sticker"})
        target = _vi("KS_EN-a-V2-竖.mp4", {"language-market", "intro-sticker"})
        assert infer_factor(source, target) == "intro-sticker"

    def test_voiceover_diff(self) -> None:
        source = _vi("KS_EN-a.mp4", {"language-market"})
        target = _vi("KS_EN-b.mp4", {"language-market", "voiceover-copy"})
        assert infer_factor(source, target) == "voiceover-copy"

    def test_unknown_fallback(self) -> None:
        source = _vi("KS_EN-a.mp4", {"language-market"})
        target = _vi("KS_EN-b.mp4", {"language-market"})
        assert infer_factor(source, target) == "unknown"


def _seed_creative_two_variants(db: Session) -> tuple[Creative, list[CreativeVariant]]:
    creative = Creative(id=str(uuid.uuid4()), name="evo-test")
    db.add(creative)
    db.flush()
    variants: list[CreativeVariant] = []
    for index, suffix in enumerate(("V1", "V2")):
        asset = CreativeAsset(
            id=str(uuid.uuid4()),
            filename=f"KS_EN-260630-58-测试素材名称很长的素材{suffix}-竖.mp4",
            file_type="video",
            storage_key=f"test/{uuid.uuid4()}",
        )
        db.add(asset)
        db.flush()
        variant = CreativeVariant(
            id=str(uuid.uuid4()), creative_id=creative.id, asset_id=asset.id, name=suffix
        )
        db.add(variant)
        variants.append(variant)
        db.add(
            Performance(
                id=str(uuid.uuid4()),
                creative_name=f"ks_en-260630-58-测试素材名称很长的素材{suffix}",
                date=date(2026, 7, 1),
                spend=100.0 * (index + 1),
                installs=10,
                raw={"付费人数": 2 * (index + 1), "D1_Roas": 0.02 * (index + 1)},
            )
        )
    db.flush()
    return creative, variants


class TestDerivationRepository:
    def test_create_idempotent_and_same_creative_list(
        self, db_session: Session
    ) -> None:
        creative, (v1, v2) = _seed_creative_two_variants(db_session)
        repo = DerivationRepository(db_session)
        first = repo.create(v1.id, v2.id, factor="aspect-ratio")
        second = repo.create(v1.id, v2.id, factor="other")
        assert first.id == second.id
        assert second.factor == "aspect-ratio"  # get-or-create 不覆盖
        assert repo.list_for_creative(creative.id) == [first]

    def test_verdict_flow(self, db_session: Session) -> None:
        _, (v1, v2) = _seed_creative_two_variants(db_session)
        repo = DerivationRepository(db_session)
        derivation = repo.create(v1.id, v2.id)
        assert derivation.verdict == "pending"
        assert repo.list_pending_verdict() == [derivation]
        derivation.verdict = "positive"
        db_session.flush()
        assert repo.list_pending_verdict() == []


class TestDeleteDerivationRoute:
    """DELETE /derivations/{id}：解链（写 edit_logs + Neo4j 失败不阻断）。"""

    def test_delete_removes_row_and_logs(self, db_session: Session) -> None:
        from sqlalchemy import select

        from app.api.routes.derivations import delete_derivation
        from app.config import Settings
        from app.models import EditLog, GraphEdge

        _, (v1, v2) = _seed_creative_two_variants(db_session)
        repo = DerivationRepository(db_session)
        derivation = repo.create(v1.id, v2.id, factor="aspect-ratio")

        # 测试环境无 Neo4j（或节点不存在）——异常被吞即覆盖"不阻断"路径
        result = delete_derivation(derivation.id, db=db_session, settings=Settings())

        assert result.success is True
        assert repo.get(derivation.id) is None
        log = db_session.scalars(
            select(EditLog).where(
                EditLog.entity_type == "derivation",
                EditLog.entity_id == derivation.id,
            )
        ).one()
        assert log.action == "delete"
        assert log.field == "factor"
        assert "-[aspect-ratio]->" in log.old_value
        assert db_session.scalars(
            select(GraphEdge).where(GraphEdge.type == "DERIVED_FROM")
        ).all() == []

    def test_delete_missing_is_404(self, db_session: Session) -> None:
        import pytest

        from app.api.routes.derivations import delete_derivation
        from app.config import Settings
        from app.exceptions import ApiError

        with pytest.raises(ApiError) as excinfo:
            delete_derivation(str(uuid.uuid4()), db=db_session, settings=Settings())
        assert excinfo.value.status_code == 404


class TestFactorReviewed:
    """factor_reviewed 置位：人工拍板因子（POST 显式指定 / PUT 修改）。"""

    def test_post_with_explicit_factor_marks_reviewed(
        self, db_session: Session
    ) -> None:
        from fastapi import BackgroundTasks

        from app.api.routes.derivations import create_derivation
        from app.config import Settings
        from app.schemas.derivation import DerivationCreatePayload

        _, (v1, v2) = _seed_creative_two_variants(db_session)
        payload = DerivationCreatePayload(
            source_variant_id=v1.id, target_variant_id=v2.id, factor="remake"
        )
        create_derivation(
            payload, BackgroundTasks(), db=db_session, settings=Settings()
        )
        derivation = DerivationRepository(db_session).list_for_creative(
            v1.creative_id
        )[0]
        assert derivation.factor == "remake"
        assert derivation.factor_reviewed is True

    def test_post_with_default_factor_stays_unreviewed(
        self, db_session: Session
    ) -> None:
        from fastapi import BackgroundTasks

        from app.api.routes.derivations import create_derivation
        from app.config import Settings
        from app.schemas.derivation import DerivationCreatePayload

        _, (v1, v2) = _seed_creative_two_variants(db_session)
        payload = DerivationCreatePayload(
            source_variant_id=v1.id, target_variant_id=v2.id
        )
        create_derivation(
            payload, BackgroundTasks(), db=db_session, settings=Settings()
        )
        derivation = DerivationRepository(db_session).list_for_creative(
            v1.creative_id
        )[0]
        assert derivation.factor == "unknown"
        assert derivation.factor_reviewed is False

    def test_put_factor_marks_reviewed(self, db_session: Session) -> None:
        from app.api.routes.derivations import update_derivation
        from app.config import Settings
        from app.models import VariantDerivation
        from app.schemas.derivation import DerivationUpdatePayload

        _, (v1, v2) = _seed_creative_two_variants(db_session)
        repo = DerivationRepository(db_session)
        derivation = repo.create(v1.id, v2.id)
        assert derivation.factor_reviewed is False

        update_derivation(
            derivation.id,
            DerivationUpdatePayload(factor="intro-sticker"),
            db=db_session,
            settings=Settings(),
        )
        assert (
            db_session.get(VariantDerivation, derivation.id).factor_reviewed is True
        )

    def test_put_verdict_does_not_mark_reviewed(self, db_session: Session) -> None:
        from app.api.routes.derivations import update_derivation
        from app.config import Settings
        from app.models import VariantDerivation
        from app.schemas.derivation import DerivationUpdatePayload

        _, (v1, v2) = _seed_creative_two_variants(db_session)
        derivation = DerivationRepository(db_session).create(v1.id, v2.id)

        update_derivation(
            derivation.id,
            DerivationUpdatePayload(verdict="positive"),
            db=db_session,
            settings=Settings(),
        )
        assert db_session.get(VariantDerivation, derivation.id).factor_reviewed is False
