"""Unit tests for app.services.matching (MIN_PREFIX two-directional prefix)."""
from __future__ import annotations

from app.services.matching import MIN_PREFIX, matches, normalize

BOILER = "ks_en-260630-58-制作人戊-模拟经营-ai-制作人丁-"  # ~31 chars shared boilerplate


class TestNormalize:
    def test_strips_extension_and_lowercases(self) -> None:
        assert normalize("KS_EN-260630-ABC.MP4") == "ks_en-260630-abc"

    def test_strips_trailing_dash_underscore_space(self) -> None:
        assert normalize("name-竖_ ") == "name-竖"  # inner dash kept

    def test_nfkc_fullwidth(self) -> None:
        assert normalize("ＡＢＣ") == "abc"


class TestMatches:
    def test_identical_names(self) -> None:
        name = BOILER + "混乱管理前贴2-竖"
        assert matches(name, name)

    def test_excel_drops_filename_suffix(self) -> None:
        # Excel name drops the trailing -竖 of the filename.
        filename = "KS_EN-260715-58-制作人丙-模拟经营-AI-田-丛林买枪-制作人乙-竖.mp4"
        excel_name = "KS_EN-260715-58-制作人丙-模拟经营-AI-田-丛林买枪-制作人乙"
        assert matches(filename, excel_name)
        assert matches(excel_name, filename)  # symmetric

    def test_cross_match_rejected_beyond_boilerplate(self) -> None:
        # Same boilerplate, different distinguishing part → no match.
        a = BOILER + "混乱管理前贴2"
        b = BOILER + "围栏防御前贴1"
        assert not matches(a, b)

    def test_min_prefix_boundary(self) -> None:
        shorter = "x" * MIN_PREFIX
        assert matches(shorter, shorter + "tail")
        assert not matches("x" * (MIN_PREFIX - 1), "x" * MIN_PREFIX + "tail")

    def test_short_cjk_name_rejected(self) -> None:
        # "丛林买枪-制作人乙" is only ~8 chars — below MIN_PREFIX, no match.
        assert not matches("丛林买枪-制作人乙", BOILER + "丛林买枪-制作人乙-竖")

    def test_one_side_not_prefix_rejected(self) -> None:
        a = BOILER + "abc-def"
        b = BOILER + "abc-xyz"
        assert not matches(a, b)
