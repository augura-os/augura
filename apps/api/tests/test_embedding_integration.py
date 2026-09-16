"""真实 fastembed 模型的 integration 测试（默认 skip）。

主 CI 不装 requirements-embed.txt（onnxruntime 太重）→ importorskip
自动跳过；backend-embed-integration job（workflow_dispatch / 每晚定时）
安装可选依赖后真实运行——首次会下载 ~95MB 模型（HF 直连，失败自动切
hf-mirror 重试一次）。
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastembed", reason="可选依赖未安装（requirements-embed.txt）")

from app.services.embedding import (  # noqa: E402
    LOCAL_EMBEDDING_MODEL,
    get_local_embedder,
    reset_local_embedder,
)


@pytest.fixture(autouse=True)
def _reset():
    reset_local_embedder()
    yield
    reset_local_embedder()


def test_real_model_embeds_512dim_normalized() -> None:
    embedder = get_local_embedder()
    vector = embedder.embed("岛屿生存建造游戏，失败重开钩子")
    assert len(vector) == 512  # bge-small-zh-v1.5
    norm = sum(v * v for v in vector) ** 0.5
    assert norm == pytest.approx(1.0, abs=1e-3)


def test_semantic_similarity_beats_lexical() -> None:
    """语义相似不需要词汇重叠（embedding 治文本兜底的核心病）。"""
    from app.services.clustering import cosine_similarity

    embedder = get_local_embedder()
    same_topic = cosine_similarity(
        embedder.embed("荒岛求生，砍树建家，失败后重新开始"),
        embedder.embed("流落无人岛，采集木材盖房子，死了就重来"),
    )
    different_topic = cosine_similarity(
        embedder.embed("荒岛求生，砍树建家，失败后重新开始"),
        embedder.embed("城市地铁线路规划与高峰客流疏导"),
    )
    assert same_topic > different_topic


def test_loaded_model_id_matches() -> None:
    """E0 坑端到端验证：实际加载的就是配置的 zh 模型，不是静默回退。"""
    embedder = get_local_embedder()
    inner = getattr(embedder._model, "model", embedder._model)
    assert getattr(inner, "model_name", "") == LOCAL_EMBEDDING_MODEL
