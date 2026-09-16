"""Embedding 抽象层（services/embedding）+ P0-3 口径修复 + shadow 语义的测试。

fastembed / OpenAI 客户端全部 mock——CI 不装 onnxruntime、不触网。
真实模型路径见 tests/test_embedding_integration.py（缺依赖自动 skip）。
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.exceptions import ApiError
from app.models import AnalysisResult, Creative, CreativeAsset, CreativeVariant
from app.repositories.creatives import CreativeRepository, VariantRepository
from app.repositories.settings import SettingsRepository
from app.services import embedding as embedding_service
from app.services import pipeline
from app.services.clustering import (
    cosine_matrix,
    cosine_similarity,
    mean_embeddings,
    merge_embedding,
)
from app.services.embedding import (
    EMBEDDING_BACKEND_SETTING,
    EMBEDDING_MODEL_ACTIVE_SETTING,
    LOCAL_EMBEDDING_MODEL,
    active_embedding_model_id,
    backfill_embeddings,
    embed_analysis_text,
    invalidate_embeddings,
    recompute_creative_representative,
    resolve_embedding_backend,
)
from app.services.settings import AIConfig

_CONFIG = AIConfig(
    api_key="sk-test",
    base_url="https://example.com/v1",
    vision_model="model-a",
    embedding_model="text-embedding-3-small",
)
_CONFIG_NO_EMBED = AIConfig(
    api_key="sk-test",
    base_url="https://example.com/v1",
    vision_model="model-a",
    embedding_model="",
)

_PROVIDER_VECTOR = [0.6, 0.8]
_LOCAL_VECTOR = [0.0, 1.0]


def _set_backend(db: Session, value: str | None) -> None:
    if value is not None:
        SettingsRepository(db).set(EMBEDDING_BACKEND_SETTING, value)
        db.flush()


def _fake_provider_embed(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.analysis import AnalysisService

    monkeypatch.setattr(
        AnalysisService, "embed", lambda _self, _text: list(_PROVIDER_VECTOR)
    )


def _fake_local_embedder(
    monkeypatch: pytest.MonkeyPatch, vector: list[float] | Exception
) -> None:
    class _Fake:
        def embed(self, _text: str) -> list[float]:
            if isinstance(vector, Exception):
                raise vector
            return list(vector)

    monkeypatch.setattr(embedding_service, "get_local_embedder", lambda: _Fake())


class TestResolveBackend:
    def test_default_is_local_when_unset(self, db_session: Session) -> None:
        assert resolve_embedding_backend(db_session) == "local"

    def test_invalid_value_falls_back(self, db_session: Session) -> None:
        _set_backend(db_session, "bogus")
        assert resolve_embedding_backend(db_session) == "local"

    def test_explicit_values(self, db_session: Session) -> None:
        for value in ("off", "provider", "local"):
            _set_backend(db_session, value)
            assert resolve_embedding_backend(db_session) == value


class TestActiveModelId:
    def test_off_returns_none(self) -> None:
        assert active_embedding_model_id("off", _CONFIG) is None

    def test_provider_without_model_returns_none(self) -> None:
        assert active_embedding_model_id("provider", _CONFIG_NO_EMBED) is None

    def test_provider_with_model(self) -> None:
        assert (
            active_embedding_model_id("provider", _CONFIG)
            == "provider:text-embedding-3-small"
        )

    def test_local(self) -> None:
        assert active_embedding_model_id("local", _CONFIG_NO_EMBED) == (
            f"local:{LOCAL_EMBEDDING_MODEL}"
        )


class TestEmbedDispatch:
    """三态分发：off / provider / local，含失败降级与 active id 记录。"""

    def test_off_returns_none_and_records_nothing(self, db_session: Session) -> None:
        _set_backend(db_session, "off")
        assert embed_analysis_text(db_session, _CONFIG, "hello") is None
        repo = SettingsRepository(db_session)
        assert repo.get(EMBEDDING_MODEL_ACTIVE_SETTING) is None

    def test_provider_without_model_returns_none(self, db_session: Session) -> None:
        _set_backend(db_session, "provider")
        assert embed_analysis_text(db_session, _CONFIG_NO_EMBED, "hello") is None

    def test_provider_path(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_backend(db_session, "provider")
        _fake_provider_embed(monkeypatch)
        assert embed_analysis_text(db_session, _CONFIG, "hello") == _PROVIDER_VECTOR
        repo = SettingsRepository(db_session)
        assert repo.get(EMBEDDING_MODEL_ACTIVE_SETTING) == (
            "provider:text-embedding-3-small"
        )

    def test_local_path(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_backend(db_session, "local")
        _fake_local_embedder(monkeypatch, _LOCAL_VECTOR)
        assert embed_analysis_text(db_session, _CONFIG_NO_EMBED, "hello") == (
            _LOCAL_VECTOR
        )
        repo = SettingsRepository(db_session)
        assert repo.get(EMBEDDING_MODEL_ACTIVE_SETTING) == (
            f"local:{LOCAL_EMBEDDING_MODEL}"
        )

    def test_local_failure_degrades_to_none(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_backend(db_session, "local")
        _fake_local_embedder(
            monkeypatch, embedding_service.EmbeddingUnavailableError("下载失败")
        )
        assert embed_analysis_text(db_session, _CONFIG_NO_EMBED, "hello") is None

    def test_default_backend_is_local(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 未配置 embedding_backend 的存量库 = local（shadow 语义保证不变糟）
        _fake_local_embedder(monkeypatch, _LOCAL_VECTOR)
        assert embed_analysis_text(db_session, _CONFIG_NO_EMBED, "hello") == (
            _LOCAL_VECTOR
        )


def _seed_vectors(db: Session) -> tuple[Creative, CreativeVariant, AnalysisResult]:
    creative = Creative(
        id=str(uuid.uuid4()),
        name="c",
        representative_embedding=[1.0, 0.0],
        embedding_count=2,
    )
    asset = CreativeAsset(
        id=str(uuid.uuid4()), filename="a.mp4", file_type="video",
        storage_key=f"test/{uuid.uuid4()}",
    )
    variant = CreativeVariant(
        id=str(uuid.uuid4()), creative_id=creative.id, asset_id=asset.id,
        name="v", embedding=[1.0, 0.0],
    )
    db.add_all([creative, asset, variant])
    db.flush()
    analysis = AnalysisResult(
        id=str(uuid.uuid4()), asset_id=asset.id, embedding=[0.5, 0.5]
    )
    db.add(analysis)
    db.flush()
    return creative, variant, analysis


class TestModelConsistency:
    """embedding_model_active：切换模型 → 存量向量失效清空 + 记录更新。"""

    def test_switch_clears_all_vectors(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        creative, variant, analysis = _seed_vectors(db_session)
        repo = SettingsRepository(db_session)
        repo.set(EMBEDDING_MODEL_ACTIVE_SETTING, "provider:old-model")
        db_session.flush()

        _set_backend(db_session, "provider")
        _fake_provider_embed(monkeypatch)
        result = embed_analysis_text(db_session, _CONFIG, "hello")
        assert result == _PROVIDER_VECTOR

        db_session.expire_all()
        assert creative.representative_embedding is None
        assert creative.embedding_count == 0
        assert variant.embedding is None
        assert analysis.embedding is None
        assert repo.get(EMBEDDING_MODEL_ACTIVE_SETTING) == (
            "provider:text-embedding-3-small"
        )

    def test_same_model_keeps_vectors(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        creative, variant, analysis = _seed_vectors(db_session)
        repo = SettingsRepository(db_session)
        repo.set(EMBEDDING_MODEL_ACTIVE_SETTING, "provider:text-embedding-3-small")
        db_session.flush()

        _set_backend(db_session, "provider")
        _fake_provider_embed(monkeypatch)
        embed_analysis_text(db_session, _CONFIG, "hello")

        db_session.expire_all()
        assert creative.representative_embedding == [1.0, 0.0]
        assert creative.embedding_count == 2
        assert variant.embedding == [1.0, 0.0]
        assert analysis.embedding == [0.5, 0.5]

    def test_first_embed_only_records(self, db_session: Session) -> None:
        # 首次写入（无历史记录）：不清向量，只登记 active id
        creative, _, _ = _seed_vectors(db_session)
        embedding_service._ensure_model_consistency(
            db_session, f"local:{LOCAL_EMBEDDING_MODEL}"
        )
        db_session.expire_all()
        assert creative.representative_embedding == [1.0, 0.0]
        repo = SettingsRepository(db_session)
        assert repo.get(EMBEDDING_MODEL_ACTIVE_SETTING) == (
            f"local:{LOCAL_EMBEDDING_MODEL}"
        )

    def test_invalidate_embeddings_clears_three_stores(
        self, db_session: Session
    ) -> None:
        creative, variant, analysis = _seed_vectors(db_session)
        invalidate_embeddings(db_session)
        db_session.expire_all()
        assert creative.representative_embedding is None
        assert creative.embedding_count == 0
        assert variant.embedding is None
        assert analysis.embedding is None


class TestRecomputeRepresentative:
    def test_mean_of_members_with_embeddings(self, db_session: Session) -> None:
        creative = Creative(id=str(uuid.uuid4()), name="c")
        db_session.add(creative)
        db_session.flush()
        for i, vector in enumerate([[1.0, 0.0], None, [0.0, 1.0]]):
            asset = CreativeAsset(
                id=str(uuid.uuid4()), filename=f"{i}.mp4", file_type="video",
                storage_key=f"test/{uuid.uuid4()}",
            )
            variant = CreativeVariant(
                id=str(uuid.uuid4()), creative_id=creative.id, asset_id=asset.id,
                name=f"v{i}", embedding=vector,
            )
            db_session.add_all([asset, variant])
        db_session.flush()

        recompute_creative_representative(db_session, creative)
        assert creative.representative_embedding == [0.5, 0.5]
        assert creative.embedding_count == 2

    def test_no_embeddings_clears_representative(self, db_session: Session) -> None:
        creative = Creative(
            id=str(uuid.uuid4()), name="c",
            representative_embedding=[1.0], embedding_count=1,
        )
        db_session.add(creative)
        db_session.flush()
        recompute_creative_representative(db_session, creative)
        assert creative.representative_embedding is None
        assert creative.embedding_count == 0


class TestMergeEmbedding:
    """P0-3：增量并向量时 count 口径 = 参与过均值的向量条数。"""

    def test_appends_with_count(self) -> None:
        mean, count = merge_embedding([1.0, 3.0], 1, [3.0, 1.0])
        assert mean == [2.0, 2.0]
        assert count == 2

    def test_first_vector(self) -> None:
        mean, count = merge_embedding(None, 0, [1.0, 2.0])
        assert mean == [1.0, 2.0]
        assert count == 1

    def test_inconsistent_state_resets(self) -> None:
        # 旧均值缺失但 count>0（数据不一致）→ 回退新向量并复位计数
        mean, count = merge_embedding(None, 3, [1.0, 2.0])
        assert mean == [1.0, 2.0]
        assert count == 1

    def test_e0_drift_scenario(self) -> None:
        # E0 漂移复现场景：5 variant 仅 v1/v3/v5 有向量。正确口径下
        # v5 进族后均值权重均等（8:8:8），不再偏袒最早进族的 v1
        mean, count = merge_embedding([1.0, 0.0], 1, [0.0, 1.0])
        mean, count = merge_embedding(mean, count, [0.0, 1.0])
        assert mean == pytest.approx([1 / 3, 2 / 3])
        assert count == 3


class TestMeanEmbeddings:
    def test_simple_mean(self) -> None:
        assert mean_embeddings([[1.0, 0.0], [0.0, 1.0]]) == [0.5, 0.5]

    def test_empty_returns_none(self) -> None:
        assert mean_embeddings([]) is None
        assert mean_embeddings([None, []]) is None  # type: ignore[list-item]

    def test_skips_dim_mismatch(self) -> None:
        assert mean_embeddings([[1.0, 0.0], [1.0]]) == [1.0, 0.0]

    def test_order_independent(self) -> None:
        vectors = [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]]
        assert mean_embeddings(vectors) == mean_embeddings(list(reversed(vectors)))


class TestCosineMatrix:
    """NumPy 矩阵化余弦 vs clustering.cosine_similarity 的语义交叉断言。"""

    def test_random_vectors_match_scalar_impl(self) -> None:
        import random

        rng = random.Random(42)
        vectors = [[rng.uniform(-1, 1) for _ in range(16)] for _ in range(8)]
        matrix = cosine_matrix(vectors)
        for i in range(len(vectors)):
            for j in range(len(vectors)):
                assert matrix[i][j] == pytest.approx(
                    cosine_similarity(vectors[i], vectors[j]), abs=1e-12
                )

    def test_degenerate_semantics_match(self) -> None:
        vectors: list[list[float]] = [[1.0, 2.0], [], [0.0, 0.0], [1.0], [2.0, 4.0]]
        matrix = cosine_matrix(vectors)
        for i in range(len(vectors)):
            for j in range(len(vectors)):
                assert matrix[i][j] == pytest.approx(
                    cosine_similarity(vectors[i], vectors[j]), abs=1e-12
                )

    def test_empty_input(self) -> None:
        assert cosine_matrix([]) == []


class TestSettingsRoute:
    """embedding_backend 的 GET 暴露与 PUT 校验。"""

    def test_put_validates_and_persists(self, db_session: Session) -> None:
        from app.api.routes.settings import update_settings
        from app.schemas.settings import SettingsUpdate

        with pytest.raises(ApiError) as exc_info:
            update_settings(
                SettingsUpdate(embedding_backend="bogus"), db_session, get_settings()
            )
        assert exc_info.value.status_code == 400

        result = update_settings(
            SettingsUpdate(embedding_backend="off"), db_session, get_settings()
        )
        assert result.data is not None
        assert result.data.embedding_backend == "off"

    def test_get_defaults(self, db_session: Session) -> None:
        from app.api.routes.settings import get_settings_info

        result = get_settings_info(db_session, get_settings())
        assert result.data is not None
        assert result.data.embedding_backend == "local"
        assert result.data.embedding_model_active == ""


class TestClusterPartialEmbeddings:
    """P0-3 回归：部分 variant 无向量的族 attach 后，代表向量 =
    有向量成员（含新向量）的真实均值，embedding_count 同步。"""

    def test_attach_uses_embedding_count_not_variant_count(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        creative = Creative(
            id=str(uuid.uuid4()), name="fail-retry-island",
            representative_embedding=[1.0, 0.0], embedding_count=1,
        )
        db_session.add(creative)
        db_session.flush()
        # 两个存量 variant：一个有向量（即均值的来源），一个没有
        for i, vector in enumerate([[1.0, 0.0], None]):
            asset = CreativeAsset(
                id=str(uuid.uuid4()), filename=f"{i}.mp4", file_type="video",
                storage_key=f"test/{uuid.uuid4()}",
            )
            variant = CreativeVariant(
                id=str(uuid.uuid4()), creative_id=creative.id, asset_id=asset.id,
                name=f"v{i}", embedding=vector,
            )
            db_session.add_all([asset, variant])
        db_session.flush()

        # 强制文本召回命中本族（单候选过阈值 → attach）；图片素材不触发测量
        monkeypatch.setattr(
            pipeline,
            "top_creative_matches_by_text",
            lambda _n, _t, _cs, limit=2: [(creative, 0.50)][:limit],
        )
        new_asset = CreativeAsset(
            id=str(uuid.uuid4()), filename="new.png", file_type="image",
            storage_key=f"test/{uuid.uuid4()}",
        )
        db_session.add(new_asset)
        db_session.flush()

        from app.services.pipeline import _cluster

        attached, _ = _cluster(
            db_session, new_asset, "fail-retry-island", [0.0, 1.0],
            "fail-retry-island",
            settings=Settings(upload_dir="/tmp"),
        )
        assert attached.id == creative.id
        # 正确口径：(1.0*1 + 0.0)/2, (0.0*1 + 1.0)/2 = [0.5, 0.5]；
        # 旧 bug 口径会按 variant_count=2 算出 [1/3, 1/3]
        assert creative.representative_embedding == [0.5, 0.5]
        assert creative.embedding_count == 2

    def test_shadow_attach_ignores_embedding_channel(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """shadow 语义：即使 embedding 存在，attach 判定也不走向量通道——
        文本召回为空时向量再像也新建族。"""
        creative = Creative(
            id=str(uuid.uuid4()), name="existing",
            representative_embedding=[1.0, 0.0], embedding_count=1,
        )
        db_session.add(creative)
        db_session.flush()
        # 文本通道零候选；若走向量通道 [1.0, 0.0] vs [1.0, 0.0] = 1.0 必 attach
        monkeypatch.setattr(
            pipeline,
            "top_creative_matches_by_text",
            lambda _n, _t, _cs, limit=2: [],
        )
        asset = CreativeAsset(
            id=str(uuid.uuid4()), filename="new.png", file_type="image",
            storage_key=f"test/{uuid.uuid4()}",
        )
        db_session.add(asset)
        db_session.flush()

        from app.services.pipeline import _cluster

        result, _ = _cluster(
            db_session, asset, "totally-different", [1.0, 0.0],
            "totally-different",
            settings=Settings(upload_dir="/tmp"),
        )
        assert result.id != creative.id
        # 新族携带向量与初始计数（供 E2 召回）
        assert result.representative_embedding == [1.0, 0.0]
        assert result.embedding_count == 1


class TestSplitRepresentative:
    """P0-3 拆族口径：新族代表向量 = 被拆成员中有向量者的均值。"""

    def test_split_uses_member_mean(self, db_session: Session) -> None:
        from app.api.routes.graph import split_variants
        from app.schemas.graph import SplitRequest

        creative = Creative(id=str(uuid.uuid4()), name="source")
        db_session.add(creative)
        db_session.flush()
        variant_ids: list[str] = []
        vectors: list[list[float] | None] = [[1.0, 0.0], [0.0, 1.0], None]
        for i, vector in enumerate(vectors):
            asset = CreativeAsset(
                id=str(uuid.uuid4()), filename=f"{i}.mp4", file_type="video",
                storage_key=f"test/{uuid.uuid4()}",
            )
            variant = CreativeVariant(
                id=str(uuid.uuid4()), creative_id=creative.id, asset_id=asset.id,
                name=f"v{i}", embedding=vector,
            )
            db_session.add_all([asset, variant])
            db_session.flush()
            variant_ids.append(variant.id)

        result = split_variants(
            SplitRequest(creative_id=creative.id, variant_ids=variant_ids),
            db_session,
            get_settings(),
        )
        assert result.success is True
        # 新族 = 源族（全部 variant 拆出后源族被清理）：代表向量是
        # [1,0] 与 [0,1] 的均值，不是旧口径的 variants[0].embedding
        moved = VariantRepository(db_session).get_many(variant_ids)
        new_creative = CreativeRepository(db_session).get(moved[0].creative_id)
        assert new_creative is not None
        assert new_creative.representative_embedding == [0.5, 0.5]
        assert new_creative.embedding_count == 2

    def test_split_without_embeddings(self, db_session: Session) -> None:
        from app.api.routes.graph import split_variants
        from app.schemas.graph import SplitRequest

        creative = Creative(id=str(uuid.uuid4()), name="source")
        db_session.add(creative)
        db_session.flush()
        asset = CreativeAsset(
            id=str(uuid.uuid4()), filename="0.mp4", file_type="video",
            storage_key=f"test/{uuid.uuid4()}",
        )
        variant = CreativeVariant(
            id=str(uuid.uuid4()), creative_id=creative.id, asset_id=asset.id,
            name="v0", embedding=None,
        )
        db_session.add_all([asset, variant])
        db_session.flush()

        result = split_variants(
            SplitRequest(creative_id=creative.id, variant_ids=[variant.id]),
            db_session,
            get_settings(),
        )
        assert result.success is True
        moved = VariantRepository(db_session).get(variant.id)
        assert moved is not None
        new_creative = CreativeRepository(db_session).get(moved.creative_id)
        assert new_creative is not None
        assert new_creative.representative_embedding is None
        assert new_creative.embedding_count == 0


class TestBackfill:
    """存量向量回填（E2 设计 §4.4）：三处覆盖 + 按成员重算 + 分批 + 容错。"""

    def _seed_unembedded(
        self, db: Session, name: str, summary: str, tags: list[str]
    ) -> tuple[Creative, CreativeVariant, AnalysisResult]:
        creative = Creative(id=str(uuid.uuid4()), name=name)
        asset = CreativeAsset(
            id=str(uuid.uuid4()), filename=f"{name}.mp4", file_type="video",
            storage_key=f"test/{uuid.uuid4()}",
        )
        variant = CreativeVariant(
            id=str(uuid.uuid4()), creative_id=creative.id, asset_id=asset.id,
            name=name,
        )
        db.add_all([creative, asset, variant])
        db.flush()
        analysis = AnalysisResult(
            id=str(uuid.uuid4()), asset_id=asset.id,
            summary=summary, tags=tags,
        )
        db.add(analysis)
        db.flush()
        return creative, variant, analysis

    def test_writes_three_stores_and_recomputes(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        creative, variant, analysis = self._seed_unembedded(
            db_session, "c1", "摘要", ["tag1"]
        )
        # 族里另有一个已有向量的 variant：重算后代表向量 = 两者均值
        asset2 = CreativeAsset(
            id=str(uuid.uuid4()), filename="c1-b.mp4", file_type="video",
            storage_key=f"test/{uuid.uuid4()}",
        )
        variant2 = CreativeVariant(
            id=str(uuid.uuid4()), creative_id=creative.id, asset_id=asset2.id,
            name="c1-b", embedding=[2.0, 0.0],
        )
        db_session.add_all([asset2, variant2])
        creative.representative_embedding = [2.0, 0.0]
        creative.embedding_count = 1
        db_session.flush()

        _set_backend(db_session, "local")
        _fake_local_embedder(monkeypatch, [0.0, 2.0])
        stats = backfill_embeddings(db_session, _CONFIG_NO_EMBED)
        assert stats.analyses == 1
        assert stats.variants == 1
        assert stats.creatives == 1
        assert stats.failed == 0
        assert stats.remaining == 0

        db_session.expire_all()
        assert analysis.embedding == [0.0, 2.0]
        assert variant.embedding == [0.0, 2.0]
        # 按成员重算（非增量）：[2,0] 与 [0,2] 的均值
        assert creative.representative_embedding == [1.0, 1.0]
        assert creative.embedding_count == 2

        # 幂等：第二轮无活可干
        stats2 = backfill_embeddings(db_session, _CONFIG_NO_EMBED)
        assert stats2.analyses == 0
        assert stats2.remaining == 0

    def test_limit_batches(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for i in range(2):
            self._seed_unembedded(db_session, f"c{i}", f"摘要{i}", [])
        _set_backend(db_session, "local")
        _fake_local_embedder(monkeypatch, [1.0, 0.0])
        stats = backfill_embeddings(db_session, _CONFIG_NO_EMBED, limit=1)
        assert stats.analyses == 1
        assert stats.remaining == 1

    def test_single_failure_skipped(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _good, _v, good_analysis = self._seed_unembedded(
            db_session, "good", "正常摘要", []
        )
        _bad, _v2, bad_analysis = self._seed_unembedded(
            db_session, "bad", "会失败的摘要", []
        )
        _set_backend(db_session, "local")

        class _Flaky:
            def embed(self, text: str) -> list[float]:
                if "会失败" in text:
                    raise embedding_service.EmbeddingUnavailableError("炸了")
                return [1.0, 0.0]

        monkeypatch.setattr(
            embedding_service, "get_local_embedder", lambda: _Flaky()
        )
        stats = backfill_embeddings(db_session, _CONFIG_NO_EMBED)
        assert stats.analyses == 1
        assert stats.failed == 1
        assert stats.skipped == [bad_analysis.id]
        db_session.expire_all()
        assert good_analysis.embedding == [1.0, 0.0]
        assert bad_analysis.embedding is None  # 单条失败不阻塞，保持 NULL

    def test_empty_text_skipped(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _c, _v, analysis = self._seed_unembedded(db_session, "empty", "", [])
        _set_backend(db_session, "local")
        _fake_local_embedder(monkeypatch, [1.0, 0.0])
        stats = backfill_embeddings(db_session, _CONFIG_NO_EMBED)
        # 空文本嵌入无意义：跳过且不写库（保持 NULL，remaining 照实统计）
        assert stats.analyses == 0
        assert stats.failed == 1
        assert stats.remaining == 1
        db_session.expire_all()
        assert analysis.embedding is None

    def test_no_config_only_counts_remaining(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _c, _v, analysis = self._seed_unembedded(db_session, "c", "摘要", [])
        _fake_local_embedder(monkeypatch, [1.0, 0.0])
        stats = backfill_embeddings(db_session, None)
        assert stats.analyses == 0
        assert stats.remaining == 1
        db_session.expire_all()
        assert analysis.embedding is None

    def test_backend_off_writes_nothing(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _c, _v, analysis = self._seed_unembedded(db_session, "c", "摘要", [])
        _set_backend(db_session, "off")
        _fake_local_embedder(monkeypatch, [1.0, 0.0])
        stats = backfill_embeddings(db_session, _CONFIG_NO_EMBED)
        assert stats.analyses == 0
        assert stats.remaining == 1
        db_session.expire_all()
        assert analysis.embedding is None


class TestBackfillRoute:
    """POST /admin/backfill-embeddings：手动全量回填端点。"""

    def test_route_returns_stats(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        creative = Creative(id=str(uuid.uuid4()), name="c")
        asset = CreativeAsset(
            id=str(uuid.uuid4()), filename="c.mp4", file_type="video",
            storage_key=f"test/{uuid.uuid4()}",
        )
        variant = CreativeVariant(
            id=str(uuid.uuid4()), creative_id=creative.id, asset_id=asset.id,
            name="v",
        )
        db_session.add_all([creative, asset, variant])
        db_session.flush()
        analysis = AnalysisResult(
            id=str(uuid.uuid4()), asset_id=asset.id, summary="摘要", tags=[],
        )
        db_session.add(analysis)
        db_session.flush()
        _set_backend(db_session, "local")
        _fake_local_embedder(monkeypatch, [1.0, 0.0])

        from app.api.routes.admin import backfill_embeddings_route

        result = backfill_embeddings_route(db_session, get_settings())
        assert result.data is not None
        assert result.data.analyses == 1
        assert result.data.variants == 1
        assert result.data.creatives == 1
        assert result.data.remaining == 0
        db_session.expire_all()
        assert analysis.embedding == [1.0, 0.0]
        assert creative.representative_embedding == [1.0, 0.0]
