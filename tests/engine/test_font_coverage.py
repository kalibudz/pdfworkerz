"""FNT-05: subset glyph-coverage check for replacement text."""

from __future__ import annotations

import pymupdf
import pytest

from engine.fonts.coverage import check_coverage
from tests.corpus.build_corpus import Corpus


def _extract(doc: pymupdf.Document, resource_name: str) -> bytes:
    for xref, _ext, _type, _basefont, name, *_rest in doc[0].get_fonts(full=True):
        if name == resource_name:
            return doc.extract_font(xref)[3]  # type: ignore[no-any-return]
    raise AssertionError(f"font resource {resource_name!r} not found on page 0")


@pytest.mark.feature("FNT-05")
def test_standard_font_covers_the_latin1_range(corpus: Corpus) -> None:
    result = check_coverage(
        font_type="Type1",
        embedded=False,
        font_bytes=b"",
        already_rendered_text="anything",
        characters="Hello, World! 123",
    )
    assert result.method == "latin1-range"
    assert result.fully_covered
    assert result.missing == frozenset()


@pytest.mark.feature("FNT-05")
def test_standard_font_does_not_cover_characters_outside_latin1() -> None:
    result = check_coverage(
        font_type="Type1", embedded=False, font_bytes=b"", already_rendered_text="", characters="日本語"
    )
    assert result.method == "latin1-range"
    assert not result.fully_covered
    assert result.missing == frozenset("日本語")


@pytest.mark.feature("FNT-05")
def test_full_embedded_font_uses_its_own_cmap(corpus: Corpus) -> None:
    with pymupdf.open(corpus.embedded_font_full) as doc:
        font_bytes = _extract(doc, "EmbeddedVeraBold")
    result = check_coverage(
        font_type="Type0", embedded=True, font_bytes=font_bytes, already_rendered_text="", characters="ABCxyz09"
    )
    assert result.method == "font-cmap"
    assert result.fully_covered  # Vera Bold covers basic Latin


@pytest.mark.feature("FNT-05")
def test_full_embedded_font_reports_genuinely_missing_glyphs(corpus: Corpus) -> None:
    with pymupdf.open(corpus.embedded_font_full) as doc:
        font_bytes = _extract(doc, "EmbeddedVeraBold")
    result = check_coverage(
        font_type="Type0",
        embedded=True,
        font_bytes=font_bytes,
        already_rendered_text="",
        characters="A☃",  # snowman
    )
    assert result.method == "font-cmap"
    assert "A" in result.covered
    assert "☃" in result.missing


@pytest.mark.feature("FNT-05")
def test_subset_font_falls_back_to_already_rendered_text(corpus: Corpus) -> None:
    with pymupdf.open(corpus.embedded_font_subset) as doc:
        font_bytes = _extract(doc, "EmbeddedVeraBold")
    result = check_coverage(
        font_type="Type0", embedded=True, font_bytes=font_bytes, already_rendered_text="AB", characters="ABC"
    )
    assert result.method == "already-rendered"
    assert result.covered == frozenset("AB")
    assert result.missing == frozenset("C")


@pytest.mark.feature("FNT-05")
def test_subset_font_fully_covered_when_every_character_was_already_used(corpus: Corpus) -> None:
    with pymupdf.open(corpus.embedded_font_subset) as doc:
        font_bytes = _extract(doc, "EmbeddedVeraBold")
    result = check_coverage(
        font_type="Type0", embedded=True, font_bytes=font_bytes, already_rendered_text="Bank Balance", characters="Bal"
    )
    assert result.fully_covered


@pytest.mark.feature("FNT-05")
def test_type3_font_is_never_reported_as_covered() -> None:
    result = check_coverage(
        font_type="Type3", embedded=False, font_bytes=None, already_rendered_text="A", characters="A"
    )
    assert result.method == "unsupported"
    assert not result.fully_covered
    assert result.missing == frozenset("A")


@pytest.mark.feature("FNT-05")
def test_full_embedded_font_reports_space_as_covered_not_missing(corpus: Corpus) -> None:
    """Regression: a space glyph has zero contours by design (it's blank), which
    must not be confused with a glyph a subsetter stripped out."""
    with pymupdf.open(corpus.embedded_font_full) as doc:
        font_bytes = _extract(doc, "EmbeddedVeraBold")
    result = check_coverage(
        font_type="Type0", embedded=True, font_bytes=font_bytes, already_rendered_text="", characters="A B"
    )
    assert result.fully_covered
    assert " " in result.covered


@pytest.mark.feature("FNT-05")
def test_a_truetype_collection_does_not_crash_coverage_checking() -> None:
    """Regression: a .ttc file's raw bytes are a multi-font collection, not a single
    sfnt font; TTFont() without fontNumber raised TTLibFileIsCollectionError."""
    import struct

    # A minimal, syntactically valid 'ttcf' header naming zero fonts is enough to
    # reach the code path that used to crash before it returns "can't tell."
    ttc_header = struct.pack(">4sHHI", b"ttcf", 1, 0, 0)
    result = check_coverage(
        font_type="TrueType", embedded=True, font_bytes=ttc_header, already_rendered_text="", characters="A"
    )
    assert result.missing == frozenset("A")


@pytest.mark.feature("FNT-05")
def test_empty_characters_are_trivially_fully_covered() -> None:
    result = check_coverage(font_type="Type1", embedded=False, font_bytes=b"", already_rendered_text="", characters="")
    assert result.fully_covered
