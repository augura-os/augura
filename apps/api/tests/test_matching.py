"""Unit tests for app.services.matching (MIN_PREFIX two-directional prefix)."""
from __future__ import annotations

from app.services.matching import MIN_PREFIX, matches, normalize

BOILER = "ks_en-260101-58-制作人戊-模拟经营-ai-制作人丁-"  # ~31 chars shared boilerplate


class TestNormalize:
    def test_strips_extension_and_lowercases(self) -> None:
        assert normalize("KS_EN-260101-ABC.MP4") == "ks_en-260101-abc"

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
        filename = "KS_EN-260102-58-制作人丙-模拟经营-AI-示例-山林采集-制作人乙-竖.mp4"
        excel_name = "KS_EN-260102-58-制作人丙-模拟经营-AI-示例-山林采集-制作人乙"
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
        # "山林采集-制作人乙" is only ~8 chars — below MIN_PREFIX, no match.
        assert not matches("山林采集-制作人乙", BOILER + "山林采集-制作人乙-竖")

    def test_one_side_not_prefix_rejected(self) -> None:
        a = BOILER + "abc-def"
        b = BOILER + "abc-xyz"
        assert not matches(a, b)


class TestIndexEquivalence:
    """match_rows（索引路径）必须与逐行 matches（旧路径）产出完全一致。

    性能优化把 O(变体×行数) 的逐行 normalize+matches 换成 distinct-name 索引，
    这里钉死两条路径在同一输入下的等价性，防止索引实现悄悄改变匹配语义。
    """

    NAMES = [
        BOILER + "混乱管理前贴2-竖",
        BOILER + "混乱管理前贴2",          # Excel 名掉了 -竖 后缀
        BOILER + "围栏防御前贴1-竖",
        "山林采集-制作人乙",               # 短名，低于 MIN_PREFIX
        "ＫＳ_ＥＮ-全角变体-260101-58-制作人甲-模拟经营-ai-制作人丁-混乱管理前贴2",
        "",                                 # 空名：索引跳过，逐行也不匹配
        "x" * MIN_PREFIX,
        "x" * MIN_PREFIX + "tail",
    ]

    STEMS = [
        BOILER + "混乱管理前贴2-竖.mp4",
        BOILER + "围栏防御前贴1.mp4",
        "山林采集-制作人乙.mp4",
        "x" * MIN_PREFIX + "tail-more",
        "completely-unrelated-name",
    ]

    def test_match_rows_equals_bruteforce(self) -> None:
        from types import SimpleNamespace

        from app.services.matching import index_performances, match_rows

        rows = [SimpleNamespace(creative_name=n) for n in self.NAMES]
        index = index_performances(rows)
        for stem in self.STEMS:
            expected = [r for r in rows if r.creative_name and matches(stem, r.creative_name)]
            assert match_rows(index, stem) == expected, stem

    def test_index_groups_distinct_normalized_names(self) -> None:
        from types import SimpleNamespace

        from app.services.matching import index_performances

        rows = [SimpleNamespace(creative_name=n) for n in self.NAMES]
        index = index_performances(rows)
        distinct = {normalize(n) for n in self.NAMES if n}
        assert len(index.entries) == len(distinct)
        non_empty = [n for n in self.NAMES if n]
        assert sum(len(rs) for _, rs in index.entries) == len(non_empty)
