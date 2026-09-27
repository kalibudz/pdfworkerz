"""FNT-08: glyph-borrow merge into a fresh subset font."""

from __future__ import annotations

import pytest
from fontTools.ttLib import TTFont

from engine.fonts.coverage import check_coverage
from engine.fonts.match import build_font_index
from engine.fonts.merge import borrow_glyphs_for, build_merged_subset


@pytest.fixture(scope="module")
def bundled_index() -> list:
    return build_font_index(include_system=False)


@pytest.mark.feature("FNT-08")
def test_merged_subset_is_a_valid_smaller_font(bundled_index: list) -> None:
    from io import BytesIO

    full = next(c for c in bundled_index if c.subfamily_name == "Roman")
    merged = build_merged_subset(full.path, "Hello World")
    assert len(merged) < full.path.stat().st_size
    TTFont(BytesIO(merged))  # parses without raising


@pytest.mark.feature("FNT-08")
def test_merged_subset_covers_exactly_the_requested_characters(bundled_index: list) -> None:
    full = next(c for c in bundled_index if c.subfamily_name == "Roman")
    merged = build_merged_subset(full.path, "ABC")
    result = check_coverage(
        font_type="TrueType", embedded=True, font_bytes=merged, already_rendered_text="", characters="ABC"
    )
    assert result.fully_covered
    missing_result = check_coverage(
        font_type="TrueType", embedded=True, font_bytes=merged, already_rendered_text="", characters="XYZ"
    )
    assert missing_result.missing == frozenset("XYZ")  # never requested, never included


@pytest.mark.feature("FNT-08")
def test_borrow_glyphs_covers_both_existing_and_new_text(bundled_index: list) -> None:
    merged = borrow_glyphs_for("STHBBN+Bitstream Vera Sans Bold", "AB", "ABC XYZ", bundled_index)
    assert merged is not None
    result = check_coverage(
        font_type="TrueType", embedded=True, font_bytes=merged, already_rendered_text="", characters="ABC XYZ"
    )
    assert result.fully_covered


@pytest.mark.feature("FNT-08")
def test_borrow_glyphs_returns_none_when_no_full_font_is_found(bundled_index: list) -> None:
    assert borrow_glyphs_for("Definitely Not A Real Font", "A", "B", bundled_index) is None


@pytest.mark.feature("FNT-08")
def test_borrow_glyphs_finds_the_font_regardless_of_subset_tag(bundled_index: list) -> None:
    """The same lookup must succeed whatever the document's own subset tag happens to be."""
    for tag in ("STHBBN+", "ABCXYZ+", ""):
        merged = borrow_glyphs_for(f"{tag}Bitstream Vera Sans Bold", "A", "B", bundled_index)
        assert merged is not None, tag


@pytest.mark.feature("FNT-08")
def test_merged_subset_includes_a_notdef_glyph(bundled_index: list) -> None:
    full = next(c for c in bundled_index if c.subfamily_name == "Roman")
    merged = build_merged_subset(full.path, "A")
    from io import BytesIO

    tt = TTFont(BytesIO(merged))
    assert ".notdef" in tt.getGlyphOrder()
