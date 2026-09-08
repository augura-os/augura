"""Integration tests for the DNA (Pattern) layer: repository + backfill data."""
from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models import Creative
from app.repositories.dna import DnaRepository
from scripts.backfill_dnas import DNAS


def _make_dna(db: Session, code: str = "D99") -> object:
    return DnaRepository(db).create(code=code, name="测试家族", hook_prototype="H99")


class TestDnaRepository:
    def test_create_and_get_by_code(self, db_session: Session) -> None:
        repo = DnaRepository(db_session)
        dna = repo.create(code="D90", name="测试", hook_prototype="H01")
        assert repo.get_by_code("D90").id == dna.id
        assert repo.get(dna.id).name == "测试"
        assert dna.status == "active"

    def test_assign_and_list_creatives(self, db_session: Session) -> None:
        repo = DnaRepository(db_session)
        dna = repo.create(code="D91", name="测试", hook_prototype="H01")
        creative = Creative(id=str(uuid.uuid4()), name="test-creative")
        db_session.add(creative)
        db_session.flush()

        repo.assign_creative(creative, dna)
        assert creative.dna_id == dna.id
        assert repo.list_creatives(dna.id) == [creative]

        repo.assign_creative(creative, None)
        assert creative.dna_id is None
        assert repo.list_creatives(dna.id) == []

    def test_list_all_ordered_by_code(self, db_session: Session) -> None:
        repo = DnaRepository(db_session)
        repo.create(code="D93", name="c", hook_prototype="H01")
        repo.create(code="D92", name="b", hook_prototype="H01")
        assert [d.code for d in repo.list_all()] == ["D92", "D93"]


class TestBackfillMapping:
    def test_codes_unique_and_sequential(self) -> None:
        codes = [code for code, *_ in DNAS]
        assert len(codes) == len(set(codes))
        assert codes == [f"D{i:02d}" for i in range(1, len(DNAS) + 1)]

    def test_every_dna_has_hook_and_mechanic(self) -> None:
        for code, name, hook, mechanic, narrative, creatives in DNAS:
            assert hook, code
            assert mechanic, code
            assert narrative, code
            assert creatives, code

    def test_no_creative_in_two_families(self) -> None:
        names = [name for *_rest, creatives in DNAS for name in creatives]
        assert len(names) == len(set(names))
