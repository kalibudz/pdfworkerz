"""FNT-06 (full-font lookup) and FNT-07 (metric-similarity matching with confidence)."""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path

import pytest

from engine.fonts.match import (
    BUNDLED_FONTS_DIR,
    build_font_index,
    extract_metrics,
    find_by_name,
    metric_distance,
    rank_by_metrics,
)


@pytest.fixture(scope="module")
def bundled_index() -> list:
    return build_font_index(include_system=False)


@pytest.fixture(scope="module")
def vera_index(bundled_index: list) -> list:
    """Just the Bitstream Vera family, for tests written against exactly those
    four faces -- the bundled set also includes assets/fonts/NotoSansSC-Subset.otf
    (FNT-14) and is expected to keep growing, see assets/fonts/README.md."""
    return [c for c in bundled_index if c.family_name == "Bitstream Vera Sans"]


@pytest.mark.feature("FNT-06")
def test_bundled_fonts_directory_is_shipped_and_readable() -> None:
    assert BUNDLED_FONTS_DIR.is_dir()
    assert list(BUNDLED_FONTS_DIR.glob("*.ttf"))


@pytest.mark.feature("FNT-06")
def test_build_font_index_reads_real_names_from_the_bundled_family(vera_index: list) -> None:
    assert len(vera_index) == 4
    subfamilies = {c.subfamily_name for c in vera_index}
    assert subfamilies == {"Roman", "Bold", "Oblique", "Bold Oblique"}
    assert all(c.source == "bundled" for c in vera_index)


@pytest.mark.feature("FNT-06")
def test_build_font_index_also_finds_the_bundled_cjk_font(bundled_index: list) -> None:
    names = {c.family_name for c in bundled_index}
    assert "Noto Sans CJK SC" in names


@pytest.mark.feature("FNT-06")
def test_find_by_name_matches_the_postscript_name(bundled_index: list) -> None:
    found = find_by_name(bundled_index, "BitstreamVeraSans-Bold")
    assert found is not None
    assert found.subfamily_name == "Bold"


@pytest.mark.feature("FNT-06")
def test_find_by_name_ignores_a_subset_tag(bundled_index: list) -> None:
    found = find_by_name(bundled_index, "ABCDEF+BitstreamVeraSans-Bold")
    assert found is not None
    assert found.postscript_name == "BitstreamVeraSans-Bold"


@pytest.mark.feature("FNT-06")
def test_find_by_name_is_case_and_punctuation_insensitive(bundled_index: list) -> None:
    found = find_by_name(bundled_index, "bitstream vera sans-bold")
    assert found is not None
    assert found.subfamily_name == "Bold"


@pytest.mark.feature("FNT-06")
def test_find_by_name_returns_none_for_an_unknown_font(bundled_index: list) -> None:
    assert find_by_name(bundled_index, "Definitely Not A Real Font Name") is None


@pytest.mark.feature("FNT-06")
def test_build_font_index_skips_unreadable_files(work_dir: Path, bundled_index: list) -> None:
    garbage = work_dir / "not_a_font.ttf"
    garbage.write_bytes(b"this is not a font file")
    index = build_font_index(include_system=False, extra_dirs=[work_dir])
    assert len(index) == len(bundled_index)  # the garbage file contributed nothing, and nothing crashed


@pytest.mark.feature("FNT-07")
def test_extract_metrics_reads_plausible_values(bundled_index: list) -> None:
    regular = next(c for c in bundled_index if c.subfamily_name == "Roman")
    metrics = extract_metrics(regular.path)
    assert metrics is not None
    assert 0.5 < metrics.cap_height_ratio < 0.9
    assert 0.3 < metrics.x_height_ratio < metrics.cap_height_ratio
    assert metrics.weight_class == 400
    assert metrics.is_fixed_pitch is False


@pytest.mark.feature("FNT-07")
def test_bold_variant_has_a_higher_weight_class_than_regular(bundled_index: list) -> None:
    regular = extract_metrics(next(c for c in bundled_index if c.subfamily_name == "Roman").path)
    bold = extract_metrics(next(c for c in bundled_index if c.subfamily_name == "Bold").path)
    assert regular is not None and bold is not None
    assert bold.weight_class > regular.weight_class


@pytest.mark.feature("FNT-07")
def test_identical_metrics_have_zero_distance(bundled_index: list) -> None:
    metrics = extract_metrics(bundled_index[0].path)
    assert metrics is not None
    assert metric_distance(metrics, metrics) == 0.0


@pytest.mark.feature("FNT-07")
def test_a_closer_style_scores_a_smaller_distance(bundled_index: list) -> None:
    regular = extract_metrics(next(c for c in bundled_index if c.subfamily_name == "Roman").path)
    oblique = extract_metrics(next(c for c in bundled_index if c.subfamily_name == "Oblique").path)
    bold = extract_metrics(next(c for c in bundled_index if c.subfamily_name == "Bold").path)
    assert regular is not None and oblique is not None and bold is not None
    # oblique (italic angle differs, same weight) should be closer to regular than bold (weight differs) is
    assert metric_distance(regular, oblique) < metric_distance(regular, bold)


@pytest.mark.feature("FNT-07")
def test_rank_by_metrics_puts_the_exact_font_first(vera_index: list) -> None:
    target = extract_metrics(next(c for c in vera_index if c.subfamily_name == "Roman").path)
    assert target is not None
    ranked = rank_by_metrics(target, vera_index)
    assert len(ranked) == 4
    assert ranked[0].candidate.subfamily_name == "Roman"
    assert ranked[0].confidence == pytest.approx(1.0)
    assert all(a.confidence >= b.confidence for a, b in pairwise(ranked))


@pytest.mark.feature("FNT-07")
def test_rank_by_metrics_confidence_is_bounded(bundled_index: list) -> None:
    target = extract_metrics(next(c for c in bundled_index if c.subfamily_name == "Roman").path)
    assert target is not None
    ranked = rank_by_metrics(target, bundled_index)
    assert all(0.0 <= m.confidence <= 1.0 for m in ranked)


@pytest.mark.feature("FNT-07")
def test_rank_by_metrics_drops_candidates_missing_required_glyphs(vera_index: list) -> None:
    target = extract_metrics(vera_index[0].path)
    assert target is not None
    ranked = rank_by_metrics(target, vera_index, covering=frozenset("Hello"))
    assert len(ranked) == 4
    ranked_cjk = rank_by_metrics(target, vera_index, covering=frozenset("日本語"))
    assert ranked_cjk == []


@pytest.mark.feature("FNT-07")
def test_rank_by_metrics_on_empty_index_returns_empty() -> None:
    target = extract_metrics(BUNDLED_FONTS_DIR / "Vera.ttf")
    assert target is not None
    assert rank_by_metrics(target, []) == []
