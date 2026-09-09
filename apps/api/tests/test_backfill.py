"""Unit tests for scripts.backfill_variant_factors.derive_factors."""
from __future__ import annotations

import pytest

# 回填脚本只在私有库，未随公开仓库发布；缺失时跳过本文件而不是让整个 CI 红掉
derive_factors = pytest.importorskip(
    "scripts.backfill_variant_factors", reason="scripts 未随公开仓库发布"
).derive_factors


def test_market_always_with_market_prefix() -> None:
    assert derive_factors("KS_EN-260630-x.mp4", []) == ["language-market"]
    assert derive_factors("KS_KR-260630-x.mp4", []) == ["language-market"]


def test_no_market_without_market_prefix() -> None:
    assert derive_factors("random-video.mp4", []) == []


def test_intro_sticker_from_filename_keywords() -> None:
    for name in ("KS_EN-a-AI前贴-b.mp4", "KS_EN-a-Ai片头.mp4", "KS_EN-a-抓马.mp4",
                 "KS_EN-a-围栏防御前贴1-竖.mp4"):
        assert "intro-sticker" in derive_factors(name, []), name


def test_intro_sticker_from_tags() -> None:
    assert "intro-sticker" in derive_factors("KS_EN-x.mp4", ["ai-drama-intro"])
    assert "intro-sticker" in derive_factors("KS_EN-x.mp4", ["paper-cutout-intro"])


def test_aspect_ratio_vertical_suffix_and_square() -> None:
    assert "aspect-ratio" in derive_factors("KS_EN-a-b-竖.mp4", [])
    assert "aspect-ratio" in derive_factors("KS_EN-a-方屏.mp4", [])
    assert "aspect-ratio" not in derive_factors("KS_EN-a-b.mp4", [])


def test_voiceover_from_filename_and_tag() -> None:
    assert "voiceover-copy" in derive_factors("KS_EN-a-口播-b.mp4", [])
    assert "voiceover-copy" in derive_factors("KS_EN-a-解说.mp4", [])
    assert "voiceover-copy" in derive_factors("KS_EN-a.mp4", ["voiceover"])


def test_brand_endcard_from_tag_only() -> None:
    assert "brand-endcard" in derive_factors("KS_EN-a.mp4", ["brand-endcard"])
    assert "brand-endcard" not in derive_factors("KS_EN-a-品牌尾页.mp4", [])


def test_full_combination_order() -> None:
    factors = derive_factors(
        "KS_EN-260715-58-制作人丙-模拟经营-AI-田-丛林买枪-制作人乙-竖.mp4",
        ["ai-drama-intro", "brand-endcard"],
    )
    assert factors == ["language-market", "intro-sticker", "aspect-ratio", "brand-endcard"]
