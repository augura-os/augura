"""DNA family routes: list families + assign a creative to one.

Assignments are human decisions (registry §2) — every change is audit-logged
and synced to Neo4j (:CreativeDNA)-[:HAS_CREATIVE] + the SQL mirror.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.deps import DbDep, SettingsDep
from app.exceptions import ApiError
from app.repositories.creatives import CreativeRepository
from app.repositories.dna import DnaRepository
from app.repositories.edit_logs import EditLogRepository
from app.repositories.graph_mirror import rebuild_mirror
from app.schemas.common import Envelope, ok
from app.services import graph_sync

router = APIRouter()


class DnaOut(BaseModel):
    id: str
    code: str
    name: str


class DnaAssignPayload(BaseModel):
    dna_id: str | None  # None = 取消归族


@router.get("/dnas", response_model=Envelope[list[DnaOut]])
def list_dnas(db: DbDep) -> Envelope[list[DnaOut]]:
    dnas = DnaRepository(db).list_all()
    return ok([DnaOut(id=d.id, code=d.code, name=d.name) for d in dnas])


@router.put("/creatives/{creative_id}/dna", response_model=Envelope[DnaOut | None])
def assign_creative_dna(
    creative_id: str,
    payload: DnaAssignPayload,
    db: DbDep,
    settings: SettingsDep,
) -> Envelope[DnaOut | None]:
    creative = CreativeRepository(db).get(creative_id)
    if creative is None:
        raise ApiError(404, f"Creative 不存在：{creative_id}")

    dna_repo = DnaRepository(db)
    old_label = ""
    if creative.dna_id:
        old_dna = dna_repo.get(creative.dna_id)
        old_label = f"{old_dna.code} {old_dna.name}" if old_dna else str(creative.dna_id)

    dna = dna_repo.get(payload.dna_id) if payload.dna_id else None
    if payload.dna_id and dna is None:
        raise ApiError(404, f"DNA 家族不存在：{payload.dna_id}")

    dna_repo.assign_creative(creative, dna)
    new_label = f"{dna.code} {dna.name}" if dna else ""
    EditLogRepository(db).record(
        entity_type="creative",
        entity_id=creative.id,
        action="update",
        field="dna_id",
        old_value=old_label,
        new_value=new_label,
    )
    db.commit()

    if dna is not None:
        graph_sync.sync_dna_subgraph(
            settings, dna=dna, creatives=dna_repo.list_creatives(dna.id)
        )
    rebuild_mirror(db)

    if dna is None:
        return ok(None, message=f"{creative.name} 已取消归族")
    return ok(DnaOut(id=dna.id, code=dna.code, name=dna.name), message="已归族")
