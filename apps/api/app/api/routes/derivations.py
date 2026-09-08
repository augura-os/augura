"""Derivation routes: evolution chain read + derivation write.

GET /creatives/{id}/evolution returns the creative's derivation chain with
per-step performance deltas (results computed on read, never stored).
POST/PUT manage derivation edges; every write is audit-logged (P06) and
synced to Neo4j + the SQL mirror.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks

from app.api.deps import DbDep, SettingsDep
from app.exceptions import ApiError
from app.repositories.creatives import CreativeRepository, VariantRepository
from app.repositories.derivations import DerivationRepository
from app.repositories.edit_logs import EditLogRepository
from app.repositories.graph_mirror import rebuild_mirror
from app.schemas.common import Envelope, ok
from app.schemas.derivation import (
    DerivationCreatePayload,
    DerivationOut,
    DerivationUpdatePayload,
    EvolutionChain,
    EvolutionStep,
    VariantBrief,
)
from app.services import graph_sync
from app.services.derivation_review import run_derivation_review
from app.services.evolution import variant_brief

router = APIRouter()


def _to_out(derivation) -> DerivationOut:  # noqa: ANN001 — ORM row
    return DerivationOut(
        id=derivation.id,
        source_variant_id=derivation.source_variant_id,
        target_variant_id=derivation.target_variant_id,
        factor=derivation.factor,
        verdict=derivation.verdict,
        note=derivation.note,
    )


@router.get(
    "/creatives/{creative_id}/evolution",
    response_model=Envelope[EvolutionChain],
)
def creative_evolution(creative_id: str, db: DbDep) -> Envelope[EvolutionChain]:
    creative = CreativeRepository(db).get(creative_id)
    if creative is None:
        raise ApiError(404, f"Creative 不存在：{creative_id}")

    variants = {v.id: v for v in VariantRepository(db).list_by_creative(creative_id)}
    derivations = DerivationRepository(db).list_for_creative(creative_id)
    briefs: dict[str, VariantBrief] = {}

    steps: list[EvolutionStep] = []
    for derivation in derivations:
        source = variants.get(derivation.source_variant_id)
        target = variants.get(derivation.target_variant_id)
        if source is None or target is None:
            continue
        for variant in (source, target):
            if variant.id not in briefs:
                briefs[variant.id] = variant_brief(db, variant)
        source_brief = briefs[source.id]
        target_brief = briefs[target.id]
        cpp_delta = (
            target_brief.cpp - source_brief.cpp
            if source_brief.cpp is not None and target_brief.cpp is not None
            else None
        )
        roas_delta = (
            target_brief.roas - source_brief.roas
            if source_brief.roas is not None and target_brief.roas is not None
            else None
        )
        steps.append(
            EvolutionStep(
                derivation=_to_out(derivation),
                source=source_brief,
                target=target_brief,
                cpp_delta=cpp_delta,
                roas_delta=roas_delta,
            )
        )

    return ok(
        EvolutionChain(
            creative_id=creative_id,
            steps=steps,
            pending_count=sum(1 for s in steps if s.derivation.verdict == "pending"),
        )
    )


@router.post("/derivations", response_model=Envelope[DerivationOut])
def create_derivation(
    payload: DerivationCreatePayload,
    background_tasks: BackgroundTasks,
    db: DbDep,
    settings: SettingsDep,
) -> Envelope[DerivationOut]:
    variant_repo = VariantRepository(db)
    source = variant_repo.get(payload.source_variant_id)
    target = variant_repo.get(payload.target_variant_id)
    if source is None or target is None:
        raise ApiError(404, "Variant 不存在")
    if source.creative_id != target.creative_id:
        raise ApiError(400, "裂变边只能连接同一 Creative 内的 Variant")

    derivation = DerivationRepository(db).create(
        payload.source_variant_id,
        payload.target_variant_id,
        factor=payload.factor,
        note=payload.note,
    )
    # 创建时显式给了非 unknown 因子 = 人工拍板，复核不再动这条边
    if payload.factor not in (None, "unknown"):
        derivation.factor_reviewed = True
    EditLogRepository(db).record(
        entity_type="derivation",
        entity_id=derivation.id,
        action="create",
        field="factor",
        old_value="",
        new_value=f"{source.name} -[{derivation.factor}]-> {target.name}",
    )
    db.commit()
    try:
        graph_sync.get_graph_repository(settings).link_derivation(
            source.id, target.id, derivation.factor
        )
    except Exception:  # noqa: BLE001 — Neo4j down 不阻断主流程
        pass
    rebuild_mirror(db)
    # 边一创建就异步跑测量复核（文件名猜测的因子可能被修正；失败不影响边）
    background_tasks.add_task(run_derivation_review, derivation.id)
    return ok(_to_out(derivation), message="已创建")


@router.delete("/derivations/{derivation_id}", response_model=Envelope[None])
def delete_derivation(
    derivation_id: str, db: DbDep, settings: SettingsDep
) -> Envelope[None]:
    repo = DerivationRepository(db)
    derivation = repo.get(derivation_id)
    if derivation is None:
        raise ApiError(404, f"Derivation 不存在：{derivation_id}")

    variants = {
        v.id: v
        for v in VariantRepository(db).get_many(
            [derivation.source_variant_id, derivation.target_variant_id]
        )
    }
    source = variants.get(derivation.source_variant_id)
    target = variants.get(derivation.target_variant_id)
    description = (
        f"{source.name if source else derivation.source_variant_id} "
        f"-[{derivation.factor}]-> "
        f"{target.name if target else derivation.target_variant_id}"
    )
    source_variant_id = derivation.source_variant_id
    target_variant_id = derivation.target_variant_id
    EditLogRepository(db).record(
        entity_type="derivation",
        entity_id=derivation.id,
        action="delete",
        field="factor",
        old_value=description,
        new_value="",
    )
    repo.delete(derivation)
    db.commit()
    try:
        graph_sync.get_graph_repository(settings).unlink_derivation(
            source_variant_id, target_variant_id
        )
    except Exception:  # noqa: BLE001 — Neo4j down 不阻断主流程
        pass
    rebuild_mirror(db)
    return ok(None, message="已解链")


@router.put("/derivations/{derivation_id}", response_model=Envelope[DerivationOut])
def update_derivation(
    derivation_id: str,
    payload: DerivationUpdatePayload,
    db: DbDep,
    settings: SettingsDep,
) -> Envelope[DerivationOut]:
    repo = DerivationRepository(db)
    derivation = repo.get(derivation_id)
    if derivation is None:
        raise ApiError(404, f"Derivation 不存在：{derivation_id}")

    log_repo = EditLogRepository(db)
    if payload.factor is not None and payload.factor != derivation.factor:
        log_repo.record(
            entity_type="derivation",
            entity_id=derivation.id,
            action="update",
            field="factor",
            old_value=derivation.factor,
            new_value=payload.factor,
        )
        derivation.factor = payload.factor
        # 人工改因子（含收件箱"采纳"）= 人工拍板，复核不再动这条边
        derivation.factor_reviewed = True
    if payload.verdict is not None and payload.verdict != derivation.verdict:
        log_repo.record(
            entity_type="derivation",
            entity_id=derivation.id,
            action="update",
            field="verdict",
            old_value=derivation.verdict,
            new_value=payload.verdict,
        )
        derivation.verdict = payload.verdict
    if payload.note is not None and payload.note != derivation.note:
        derivation.note = payload.note
    db.commit()

    if payload.factor is not None:
        try:
            graph_sync.get_graph_repository(settings).link_derivation(
                derivation.source_variant_id,
                derivation.target_variant_id,
                derivation.factor,
            )
        except Exception:  # noqa: BLE001
            pass
    return ok(_to_out(derivation), message="已更新")
