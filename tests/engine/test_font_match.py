"""FNT-06 (full-font lookup) and FNT-07 (metric-similarity matching with confidence)."""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path

import pytest

from engine.fonts.match import (
    BUNDLED_FONTS_DIR,
    CROSS_FAMILY_FACTOR,
    FontCandidate,
    build_font_index,
    extract_metrics,
    find_by_name,
    metric_distance,
    rank_by_metrics,
    same_family,
)


@pytest.fixture(scope="module")
def bundled_index() -> list:
    return build_font_index(include_system=False, include_user=False)


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
    index = build_font_index(include_system=False, include_user=False, extra_dirs=[work_dir])
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


@pytest.mark.feature("FNT-06")
@pytest.mark.parametrize("weight", ["Light", "Regular"])
def test_bundled_roboto_is_found_by_a_subset_tagged_name(bundled_index: list, weight: str) -> None:
    """A real statement embedded "IOXRUB+Roboto-Light"; without Roboto bundled
    it fell back to an approximate match (Trebuchet)."""
    found = find_by_name(bundled_index, f"IOXRUB+Roboto-{weight}")
    assert found is not None
    assert found.source == "bundled"
    assert found.postscript_name == f"Roboto-{weight}"


@pytest.mark.feature("FNT-06")
def test_bundled_roboto_ships_with_its_ofl_license() -> None:
    from fontTools.ttLib import TTFont

    license_text = (BUNDLED_FONTS_DIR / "OFL-Roboto.txt").read_text(encoding="utf-8")
    assert "SIL OPEN FONT LICENSE Version 1.1" in license_text
    assert "The Roboto Project Authors" in license_text
    for weight in ("Light", "Regular"):
        embedded = TTFont(BUNDLED_FONTS_DIR / f"Roboto-{weight}.ttf")["name"].getDebugName(13)
        assert embedded is not None and "Open Font License" in embedded


@pytest.mark.feature("FNT-06")
def test_bundled_roboto_turns_an_approximate_match_into_an_exact_one(work_dir: Path, bundled_index: list) -> None:
    import pymupdf

    from engine.document import Document
    from engine.edit import replace_span_text
    from engine.fonts.style import extract_page_spans

    path = work_dir / "roboto.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_font(fontname="RobotoL", fontfile=str(BUNDLED_FONTS_DIR / "Roboto-Light.ttf"))
    page.insert_text((72, 100), "Balance forward", fontname="RobotoL", fontsize=8)
    doc.subset_fonts()
    doc.save(path)
    doc.close()

    without_roboto = [c for c in bundled_index if not c.postscript_name.startswith("Roboto")]
    tiers = {}
    for label, index in (("with", bundled_index), ("without", without_roboto)):
        with Document.open(path) as document:
            span = extract_page_spans(document.raw, 0)[0]
            result = replace_span_text(document, 0, span, "Summary forward", font_index=index)
            assert result.verification is not None and result.verification.text_matches
            tiers[label] = result.tier
    assert tiers == {"with": "exact", "without": "approximate"}


@pytest.mark.feature("FNT-07")
@pytest.mark.parametrize(
    ("base_font", "family", "expected"),
    [
        ("ABCDEF+Roboto-Light", "Roboto", True),
        ("ArialMT", "Arial", True),
        ("BitstreamVeraSans-Bold", "Bitstream Vera Sans", True),
        ("Delta-Book", "Maiandra GD", False),
        ("Roboto-Regular", "Trebuchet MS", False),
        ("F1", "F", False),
    ],
)
def test_same_family_matches_by_normalized_family_prefix(base_font: str, family: str, expected: bool) -> None:
    candidate = FontCandidate(
        path=Path("x.ttf"), family_name=family, subfamily_name="Regular", postscript_name="X", source="system"
    )
    assert same_family(base_font, candidate) is expected


@pytest.mark.feature("FNT-07")
def test_a_different_family_is_scaled_down_even_with_identical_metrics(vera_index: list) -> None:
    """The real-document case: metrics alone called a stranger a 0.98 match.
    Vera measured against itself is distance 0, so confidence 1.0 -- unless
    the PDF font's name says it's a different family."""
    target = extract_metrics(BUNDLED_FONTS_DIR / "Vera.ttf")
    same = rank_by_metrics(target, vera_index, target_name="BitstreamVeraSans-Roman")
    other = rank_by_metrics(target, vera_index, target_name="Delta-Book")
    assert same[0].confidence == pytest.approx(1.0)
    assert other[0].confidence == pytest.approx(CROSS_FAMILY_FACTOR)
    assert all(match.confidence <= CROSS_FAMILY_FACTOR for match in other)
