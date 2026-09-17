"""智能建族（family bootstrap）：suggest → 收件箱提案 → confirm/dismiss。"""
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routes.dnas import (
    FamilyConfirmPayload,
    FamilyDismissPayload,
    confirm_family,
    dismiss_family,
    suggest_families_route,
)
from app.config import Settings
from app.exceptions import ApiError
from app.models import (
    AnalysisResult,
    Creative,
    CreativeAsset,
    CreativeDNA,
    CreativeVariant,
    EditLog,
    JudgeSuggestion,
)
from app.repositories.settings import SettingsRepository
from app.services import family_bootstrap
from app.services import review as review_service
from app.services.judge_suggestions import upsert_suggestion
from app.services.settings import AIConfig


def _config(api_key: str = "test-key") -> AIConfig:
    return AIConfig(
        api_key=api_key, base_url="", vision_model="m", embedding_model=""
    )


def _seed_creative(
    db: Session, name: str, *, with_analysis: bool = True
) -> Creative:
    creative = Creative(id=str(uuid.uuid4()), name=name)
    asset = CreativeAsset(
        id=str(uuid.uuid4()), filename=f"{name}.mp4", file_type="video",
        storage_key=f"test/{uuid.uuid4()}",
    )
    db.add_all([creative, asset])
    db.flush()
    db.add(
        CreativeVariant(
            id=str(uuid.uuid4()), creative_id=creative.id,
            asset_id=asset.id, name="v1",
        )
    )
    db.flush()
    if with_analysis:
        db.add(
            AnalysisResult(
                id=str(uuid.uuid4()), asset_id=asset.id,
                hook=f"{name} 钩子", gameplay=f"{name} 玩法",
                summary=f"{name} 摘要",
            )
        )
        db.flush()
    return creative


def _fake_llm(
    families: list[dict[str, Any]], assigns: list[dict[str, Any]] | None = None
):
    def fake(
        config: AIConfig, *, system: str, user: str, max_tokens: int = 800
    ) -> dict[str, Any]:
        return {"new_families": families, "assign_to_existing": assigns or []}

    return fake


def _bootstrap_rows(db: Session) -> list[JudgeSuggestion]:
    return list(
        db.scalars(
            select(JudgeSuggestion).where(
                JudgeSuggestion.kind == family_bootstrap.KIND
            )
        ).all()
    )


class TestSuggestFamilies:
    def test_writes_proposals(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        a = _seed_creative(db_session, "素材甲")
        b = _seed_creative(db_session, "素材乙")
        _seed_creative(db_session, "素材丙")  # 散点：不进任何族
        monkeypatch.setattr(
            family_bootstrap,
            "complete_json",
            _fake_llm(
                [
                    {
                        "name": "围栏防御",
                        "core_mechanic": "建栏挡敌",
                        "hook_prototype": "绝境求生",
                        "narrative_structure": "三幕反转",
                        "member_ids": [a.id, b.id],
                        "note": "都是围栏抵御入侵者的塔防机制",
                    }
                ]
            ),
        )

        count = family_bootstrap.suggest_families(db_session, _config())
        assert count == 1
        rows = _bootstrap_rows(db_session)
        assert len(rows) == 1
        row = rows[0]
        assert row.verdict == "围栏防御"
        assert row.votes == 2
        payload = json.loads(row.reason)
        assert payload["member_ids"] == [a.id, b.id]
        assert payload["core_mechanic"] == "建栏挡敌"

        items = review_service.family_bootstrap_items(db_session)
        assert len(items) == 1
        item = items[0]
        assert item.kind == "family_bootstrap"
        assert item.title == "围栏防御"
        assert item.reason == "都是围栏抵御入侵者的塔防机制"
        assert item.family is not None
        assert item.family.suggestion_id == row.id
        assert [m.name for m in item.family.members] == ["素材甲", "素材乙"]

    def test_no_api_key_returns_zero(self, db_session: Session) -> None:
        _seed_creative(db_session, "素材甲")
        assert family_bootstrap.suggest_families(db_session, _config("")) == 0
        assert _bootstrap_rows(db_session) == []

    def test_no_unassigned_returns_zero(self, db_session: Session) -> None:
        # 有分析但已归族 + 未归族但无分析：都不可提案
        assigned = _seed_creative(db_session, "已归族")
        dna = CreativeDNA(id=str(uuid.uuid4()), code="D01", name="已有族")
        db_session.add(dna)
        db_session.flush()
        assigned.dna_id = dna.id
        _seed_creative(db_session, "无分析", with_analysis=False)
        assert family_bootstrap.suggest_families(db_session, _config()) == 0

    def test_single_member_proposal_filtered(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        a = _seed_creative(db_session, "素材甲")
        _seed_creative(db_session, "素材乙")
        monkeypatch.setattr(
            family_bootstrap,
            "complete_json",
            _fake_llm(
                [{"name": "独苗", "core_mechanic": "", "hook_prototype": "",
                  "narrative_structure": "", "member_ids": [a.id], "note": ""}]
            ),
        )
        assert family_bootstrap.suggest_families(db_session, _config()) == 0
        assert _bootstrap_rows(db_session) == []

    def test_rerun_replaces_not_duplicates(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        a = _seed_creative(db_session, "素材甲")
        b = _seed_creative(db_session, "素材乙")
        monkeypatch.setattr(
            family_bootstrap,
            "complete_json",
            _fake_llm(
                [{"name": "围栏防御", "core_mechanic": "建栏挡敌",
                  "hook_prototype": "", "narrative_structure": "",
                  "member_ids": [a.id, b.id], "note": ""}]
            ),
        )
        assert family_bootstrap.suggest_families(db_session, _config()) == 1
        assert family_bootstrap.suggest_families(db_session, _config()) == 1
        assert len(_bootstrap_rows(db_session)) == 1

    def test_route_returns_count(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        a = _seed_creative(db_session, "素材甲")
        b = _seed_creative(db_session, "素材乙")
        SettingsRepository(db_session).set("openai_api_key", "test-key")
        monkeypatch.setattr(
            family_bootstrap,
            "complete_json",
            _fake_llm(
                [{"name": "围栏防御", "core_mechanic": "", "hook_prototype": "",
                  "narrative_structure": "", "member_ids": [a.id, b.id],
                  "note": ""}]
            ),
        )
        result = suggest_families_route(db=db_session, settings=Settings())
        assert result.data == 1


class TestConfirmFamily:
    def _suggest(
        self, db: Session, monkeypatch: pytest.MonkeyPatch, member_ids: list[str]
    ) -> JudgeSuggestion:
        monkeypatch.setattr(
            family_bootstrap,
            "complete_json",
            _fake_llm(
                [{"name": "围栏防御", "core_mechanic": "建栏挡敌",
                  "hook_prototype": "绝境求生", "narrative_structure": "三幕反转",
                  "member_ids": member_ids, "note": ""}]
            ),
        )
        family_bootstrap.suggest_families(db, _config())
        return _bootstrap_rows(db)[0]

    def test_confirm_creates_family_and_assigns(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        a = _seed_creative(db_session, "素材甲")
        b = _seed_creative(db_session, "素材乙")
        row = self._suggest(db_session, monkeypatch, [a.id, b.id])

        result = confirm_family(
            FamilyConfirmPayload(
                suggestion_id=row.id,
                name="改名后的族",
                core_mechanic="建栏挡敌",
                hook_prototype="绝境求生",
                narrative_structure="三幕反转",
                member_ids=[a.id, b.id],
            ),
            db=db_session,
            settings=Settings(),
        )
        assert result.data is not None
        assert result.data.code == "D01"
        assert result.data.name == "改名后的族"

        db_session.expire_all()
        for creative in (a, b):
            assert creative.dna_id == result.data.id
        logs = db_session.scalars(
            select(EditLog).where(
                EditLog.entity_type == "creative", EditLog.field == "dna_id"
            )
        ).all()
        assert len(logs) == 2
        for log in logs:
            # 人工确认的批量归族不带 auto: 前缀（不进 judge 自动改判率桶）
            assert log.new_value.startswith("D01 改名后的族（智能建族人工确认）")
            assert not log.new_value.startswith("auto:")
        assert _bootstrap_rows(db_session) == []

    def test_confirm_skips_already_assigned_members(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        a = _seed_creative(db_session, "素材甲")
        b = _seed_creative(db_session, "素材乙")
        row = self._suggest(db_session, monkeypatch, [a.id, b.id])
        # 提案生成后 b 已被其他流程归族——确认时绝不抢
        other = CreativeDNA(id=str(uuid.uuid4()), code="D01", name="已有族")
        db_session.add(other)
        db_session.flush()
        b.dna_id = other.id
        db_session.flush()

        result = confirm_family(
            FamilyConfirmPayload(
                suggestion_id=row.id, name="围栏防御", member_ids=[a.id, b.id]
            ),
            db=db_session,
            settings=Settings(),
        )
        assert result.data is not None
        assert result.data.code == "D02"  # D01 已占用，发号递增
        db_session.expire_all()
        assert a.dna_id == result.data.id
        assert b.dna_id == other.id

    def test_confirm_missing_suggestion_404(self, db_session: Session) -> None:
        with pytest.raises(ApiError) as exc_info:
            confirm_family(
                FamilyConfirmPayload(
                    suggestion_id=str(uuid.uuid4()), name="x", member_ids=[]
                ),
                db=db_session,
                settings=Settings(),
            )
        assert exc_info.value.status_code == 404

    def test_rerun_never_touches_confirmed(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        a = _seed_creative(db_session, "素材甲")
        b = _seed_creative(db_session, "素材乙")
        row = self._suggest(db_session, monkeypatch, [a.id, b.id])
        result = confirm_family(
            FamilyConfirmPayload(
                suggestion_id=row.id, name="围栏防御", member_ids=[a.id, b.id]
            ),
            db=db_session,
            settings=Settings(),
        )
        assert result.data is not None
        # 重跑 suggest：已归族成员不再是散点，提案里的 id 被清洗后不足
        # MIN_MEMBERS → 无新提案，已确认族原样不动
        count = family_bootstrap.suggest_families(db_session, _config())
        assert count == 0
        db_session.expire_all()
        assert a.dna_id == result.data.id
        assert b.dna_id == result.data.id
        dnas = db_session.scalars(select(CreativeDNA)).all()
        assert len(dnas) == 1


class TestDismissFamily:
    def test_dismiss_only_deletes_suggestion(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        a = _seed_creative(db_session, "素材甲")
        b = _seed_creative(db_session, "素材乙")
        monkeypatch.setattr(
            family_bootstrap,
            "complete_json",
            _fake_llm(
                [{"name": "围栏防御", "core_mechanic": "", "hook_prototype": "",
                  "narrative_structure": "", "member_ids": [a.id, b.id],
                  "note": ""}]
            ),
        )
        family_bootstrap.suggest_families(db_session, _config())
        row = _bootstrap_rows(db_session)[0]

        result = dismiss_family(
            FamilyDismissPayload(suggestion_id=row.id), db=db_session
        )
        assert result.success is True
        assert _bootstrap_rows(db_session) == []
        assert db_session.scalars(select(CreativeDNA)).all() == []
        db_session.expire_all()
        assert a.dna_id is None
        assert b.dna_id is None

    def test_dismiss_missing_suggestion_404(self, db_session: Session) -> None:
        with pytest.raises(ApiError) as exc_info:
            dismiss_family(
                FamilyDismissPayload(suggestion_id=str(uuid.uuid4())),
                db=db_session,
            )
        assert exc_info.value.status_code == 404


class TestAttachToConfirmedFamily:
    """改动 1：assign_to_existing 引用已确认家族 → 挂接提案（只挂不建）。"""

    def _confirmed(self, db: Session) -> CreativeDNA:
        dna = CreativeDNA(
            id=str(uuid.uuid4()), code="D01", name="雪地求生",
            core_mechanic="资源管理",
        )
        db.add(dna)
        db.flush()
        return dna

    def test_attach_proposal_and_confirm(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        dna = self._confirmed(db_session)
        a = _seed_creative(db_session, "素材甲")
        b = _seed_creative(db_session, "素材乙")
        monkeypatch.setattr(
            family_bootstrap,
            "complete_json",
            _fake_llm(
                [], assigns=[{"existing_dna_code": "D01",
                              "member_ids": [a.id, b.id]}]
            ),
        )

        count = family_bootstrap.suggest_families(db_session, _config())
        assert count == 1
        rows = _bootstrap_rows(db_session)
        assert len(rows) == 1
        payload = json.loads(rows[0].reason)
        assert payload["existing_dna_code"] == "D01"
        assert payload["name"] == "雪地求生"

        items = review_service.family_bootstrap_items(db_session)
        assert len(items) == 1
        assert items[0].title == "并入 D01 · 雪地求生"
        assert items[0].family is not None
        assert items[0].family.existing_dna_code == "D01"

        result = confirm_family(
            FamilyConfirmPayload(
                suggestion_id=rows[0].id,
                existing_dna_code="D01",
                member_ids=[a.id, b.id],
            ),
            db=db_session,
            settings=Settings(),
        )
        assert result.data is not None
        assert result.data.code == "D01"
        assert "已并入家族" in result.message
        # 不建族：仍只有 D01 一个家族
        assert len(db_session.scalars(select(CreativeDNA)).all()) == 1
        db_session.expire_all()
        assert a.dna_id == dna.id
        assert b.dna_id == dna.id
        logs = db_session.scalars(
            select(EditLog).where(EditLog.field == "dna_id")
        ).all()
        assert len(logs) == 2
        for log in logs:
            assert "（智能建族并入已有家族）" in log.new_value
        assert _bootstrap_rows(db_session) == []

    def test_attach_unknown_code_dropped(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        a = _seed_creative(db_session, "素材甲")
        b = _seed_creative(db_session, "素材乙")
        monkeypatch.setattr(
            family_bootstrap,
            "complete_json",
            _fake_llm(
                [], assigns=[{"existing_dna_code": "D99",
                              "member_ids": [a.id, b.id]}]
            ),
        )
        # LLM 引用了不存在的 code：丢弃，不产生提案
        assert family_bootstrap.suggest_families(db_session, _config()) == 0
        assert _bootstrap_rows(db_session) == []

    def test_confirm_attach_missing_dna_404(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._confirmed(db_session)
        a = _seed_creative(db_session, "素材甲")
        b = _seed_creative(db_session, "素材乙")
        monkeypatch.setattr(
            family_bootstrap,
            "complete_json",
            _fake_llm(
                [], assigns=[{"existing_dna_code": "D01",
                              "member_ids": [a.id, b.id]}]
            ),
        )
        family_bootstrap.suggest_families(db_session, _config())
        row = _bootstrap_rows(db_session)[0]
        with pytest.raises(ApiError) as exc_info:
            confirm_family(
                FamilyConfirmPayload(
                    suggestion_id=row.id, existing_dna_code="D77",
                    member_ids=[a.id],
                ),
                db=db_session,
                settings=Settings(),
            )
        assert exc_info.value.status_code == 404


class TestKeywords:
    """改动 2：识别特征词的清洗与落库。"""

    def test_keywords_cleaned_and_stored(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        a = _seed_creative(db_session, "素材甲")
        b = _seed_creative(db_session, "素材乙")
        raw_keywords = (
            ["围栏", " 围栏 ", "栏" * 30, ""] + [f"词{i}" for i in range(12)]
        )
        monkeypatch.setattr(
            family_bootstrap,
            "complete_json",
            _fake_llm(
                [{"name": "围栏防御", "core_mechanic": "建栏挡敌",
                  "hook_prototype": "", "narrative_structure": "",
                  "keywords": raw_keywords,
                  "member_ids": [a.id, b.id], "note": ""}]
            ),
        )
        assert family_bootstrap.suggest_families(db_session, _config()) == 1
        row = _bootstrap_rows(db_session)[0]
        payload = json.loads(row.reason)
        expected = ["围栏", "栏" * 24] + [f"词{i}" for i in range(10)]
        assert payload["keywords"] == expected  # 去重/截断/上限 12

        result = confirm_family(
            FamilyConfirmPayload(
                suggestion_id=row.id, name="围栏防御",
                keywords=payload["keywords"], member_ids=[a.id, b.id],
            ),
            db=db_session,
            settings=Settings(),
        )
        assert result.data is not None
        dna = db_session.scalar(
            select(CreativeDNA).where(CreativeDNA.id == result.data.id)
        )
        assert dna is not None
        assert dna.keywords == expected


class TestScatterRescueAndOverlap:
    """改动 3：散点回收 + 互斥体检（lite，失败静默）。"""

    def test_scatter_rescued_into_proposal(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        a = _seed_creative(db_session, "素材甲")
        b = _seed_creative(db_session, "素材乙")
        c = _seed_creative(db_session, "素材丙")

        def fake(
            config: AIConfig, *, system: str, user: str, max_tokens: int = 800
        ) -> dict[str, Any]:
            if "散点素材" in user:  # 回收步：把丙并入新提案
                return {
                    "new_families": [],
                    "assign_to_existing": [
                        {"family": "围栏防御", "member_ids": [c.id]}
                    ],
                }
            return {
                "new_families": [
                    {"name": "围栏防御", "core_mechanic": "建栏挡敌",
                     "hook_prototype": "", "narrative_structure": "",
                     "member_ids": [a.id, b.id], "note": ""}
                ],
                "assign_to_existing": [],
            }

        monkeypatch.setattr(family_bootstrap, "complete_json", fake)
        assert family_bootstrap.suggest_families(db_session, _config()) == 1
        row = _bootstrap_rows(db_session)[0]
        assert row.votes == 3
        payload = json.loads(row.reason)
        assert payload["member_ids"] == [a.id, b.id, c.id]

    def test_overlap_marked_in_note(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        names = ["素材甲", "素材乙", "素材丙", "素材丁"]
        ids = [_seed_creative(db_session, n).id for n in names]

        def fake(
            config: AIConfig, *, system: str, user: str, max_tokens: int = 800
        ) -> dict[str, Any]:
            if "应当合并的家族对" in user:  # 互斥体检
                return {
                    "overlaps": [
                        {"family_a": "围栏防御", "family_b": "栅栏防守",
                         "why": "都是建栏挡敌"}
                    ]
                }
            return {
                "new_families": [
                    {"name": "围栏防御", "core_mechanic": "建栏挡敌",
                     "hook_prototype": "", "narrative_structure": "",
                     "member_ids": ids[:2], "note": "塔防"},
                    {"name": "栅栏防守", "core_mechanic": "立栅栏御敌",
                     "hook_prototype": "", "narrative_structure": "",
                     "member_ids": ids[2:], "note": "防守"},
                ],
                "assign_to_existing": [],
            }

        monkeypatch.setattr(family_bootstrap, "complete_json", fake)
        assert family_bootstrap.suggest_families(db_session, _config()) == 2
        notes = [
            json.loads(row.reason)["note"] for row in _bootstrap_rows(db_session)
        ]
        assert any("⚠ 与「栅栏防守」机制相近" in note for note in notes)
        assert any("⚠ 与「围栏防御」机制相近" in note for note in notes)

    def test_rescue_and_overlap_failure_tolerated(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        a = _seed_creative(db_session, "素材甲")
        b = _seed_creative(db_session, "素材乙")
        c = _seed_creative(db_session, "素材丙")
        d = _seed_creative(db_session, "素材丁")

        def fake(
            config: AIConfig, *, system: str, user: str, max_tokens: int = 800
        ) -> dict[str, Any] | None:
            if "散点素材" in user or "应当合并的家族对" in user:
                return None  # 回收/体检 LLM 全挂
            return {
                "new_families": [
                    {"name": "围栏防御", "core_mechanic": "建栏挡敌",
                     "hook_prototype": "", "narrative_structure": "",
                     "member_ids": [a.id, b.id], "note": "塔防"},
                    {"name": "经营模拟", "core_mechanic": "开店经营",
                     "hook_prototype": "", "narrative_structure": "",
                     "member_ids": [c.id, d.id], "note": "经营"},
                ],
                "assign_to_existing": [],
            }

        monkeypatch.setattr(family_bootstrap, "complete_json", fake)
        # 主流程不受影响：两个提案照常落库
        assert family_bootstrap.suggest_families(db_session, _config()) == 2
        assert len(_bootstrap_rows(db_session)) == 2


class TestBootstrapHint:
    """PR4：稳态增量扩族提示（review queue 的 family_bootstrap_hint）。"""

    def test_unassigned_count_scope(self, db_session: Session) -> None:
        _seed_creative(db_session, "散点甲")
        _seed_creative(db_session, "散点乙")
        _seed_creative(db_session, "无分析", with_analysis=False)  # 不计入
        assigned = _seed_creative(db_session, "已归族")  # 已归族不计入
        dna = CreativeDNA(id=str(uuid.uuid4()), code="D01", name="已有族")
        db_session.add(dna)
        db_session.flush()
        assigned.dna_id = dna.id
        db_session.flush()

        hint = review_service.family_bootstrap_hint(db_session)
        assert hint.unassigned == 2
        assert hint.pending_proposals == 0
        assert hint.suggest is False

    def test_suggest_threshold_boundary(self, db_session: Session) -> None:
        for i in range(family_bootstrap.SUGGEST_THRESHOLD - 1):
            _seed_creative(db_session, f"散点{i}")
        assert review_service.family_bootstrap_hint(db_session).suggest is False

        _seed_creative(db_session, "散点压线")
        hint = review_service.family_bootstrap_hint(db_session)
        assert hint.unassigned == family_bootstrap.SUGGEST_THRESHOLD
        assert hint.suggest is True

    def test_pending_proposals_suppress_hint(self, db_session: Session) -> None:
        for i in range(family_bootstrap.SUGGEST_THRESHOLD):
            _seed_creative(db_session, f"散点{i}")
        # 已有未处理提案浮在收件箱：不催，避免重复打扰
        upsert_suggestion(
            db_session,
            kind=family_bootstrap.KIND,
            left_id=str(uuid.uuid4()),
            right_id=None,
            verdict="提案占位",
            votes=2,
            reason="{}",
        )
        hint = review_service.family_bootstrap_hint(db_session)
        assert hint.unassigned == family_bootstrap.SUGGEST_THRESHOLD
        assert hint.pending_proposals == 1
        assert hint.suggest is False


class TestOrderByEmbedding:
    """向量预分组（E2 设计 §4.3）：语义相近的素材排进同批；无向量保持现状。"""

    def test_similar_profiles_grouped(self, db_session: Session) -> None:
        # 名字排序 a/b/c/d；向量上 a≈c、b≈d → 重排后相近对相邻
        a = _seed_creative(db_session, "素材甲")
        b = _seed_creative(db_session, "素材乙")
        c = _seed_creative(db_session, "素材丙")
        d = _seed_creative(db_session, "素材丁")
        a.representative_embedding = [1.0, 0.0, 0.0]
        c.representative_embedding = [0.9, 0.1, 0.0]
        b.representative_embedding = [0.0, 1.0, 0.0]
        d.representative_embedding = [0.0, 0.9, 0.1]
        db_session.flush()
        profiles = [{"id": x.id, "name": x.name} for x in (a, b, c, d)]
        ordered = family_bootstrap._order_by_embedding(db_session, profiles)
        assert [p["id"] for p in ordered] == [a.id, c.id, b.id, d.id]

    def test_no_embeddings_keeps_name_order(self, db_session: Session) -> None:
        a = _seed_creative(db_session, "素材甲")
        b = _seed_creative(db_session, "素材乙")
        profiles = [{"id": x.id, "name": x.name} for x in (a, b)]
        ordered = family_bootstrap._order_by_embedding(db_session, profiles)
        assert [p["id"] for p in ordered] == [a.id, b.id]

    def test_partial_embeddings_appended_at_tail(
        self, db_session: Session
    ) -> None:
        # 无向量的保持原相对顺序排在有向量素材之后
        a = _seed_creative(db_session, "素材甲")
        b = _seed_creative(db_session, "素材乙")  # 无向量
        c = _seed_creative(db_session, "素材丙")
        a.representative_embedding = [1.0, 0.0]
        c.representative_embedding = [0.9, 0.1]
        db_session.flush()
        profiles = [{"id": x.id, "name": x.name} for x in (a, b, c)]
        ordered = family_bootstrap._order_by_embedding(db_session, profiles)
        assert [p["id"] for p in ordered] == [a.id, c.id, b.id]
