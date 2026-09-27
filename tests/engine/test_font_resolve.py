"""Font tier resolution for text edits (ties FNT-05..FNT-08 into one decision)."""

from __future__ import annotations

import pikepdf
import pymupdf
import pytest

from engine.fonts.classify import classify_font
from engine.fonts.match import build_font_index
from engine.fonts.resolve import TIER_APPROXIMATE, TIER_EXACT, TIER_FALLBACK, resolve_font
from tests.corpus.build_corpus import Corpus


@pytest.fixture(scope="module")
def font_index() -> list:
    return build_font_index(include_system=False)


@pytest.mark.feature("FNT-06")
def test_standard_font_resolves_exact_with_no_approval_needed(corpus: Corpus, font_index: list) -> None:
    with pikepdf.open(corpus.simple) as pdf:
        classification = classify_font(pdf, 0, "helv")
    result = resolve_font(
        classification,
        original_font_bytes=None,
        already_rendered_text="Hello, PDFWorkerz.",
        needed_text="Goodbye",
        font_index=font_index,
    )
    assert result.tier == TIER_EXACT
    assert result.confidence == 1.0
    assert result.fontname == "helv"
    assert result.font_bytes is None
    assert result.requires_approval is False


@pytest.mark.feature("FNT-06")
def test_embedded_font_findable_by_name_resolves_exact_via_merged_subset(corpus: Corpus, font_index: list) -> None:
    with pikepdf.open(corpus.embedded_font_subset) as pdf:
        classification = classify_font(pdf, 0, "EmbeddedVeraBold")
    result = resolve_font(
        classification,
        original_font_bytes=None,
        already_rendered_text="AB",
        needed_text="ABC XYZ",
        font_index=font_index,
    )
    assert result.tier == TIER_EXACT
    assert result.confidence == 1.0
    assert result.requires_approval is False
    assert result.font_bytes is not None

    from engine.fonts.coverage import check_coverage

    coverage = check_coverage(
        font_type="TrueType",
        embedded=True,
        font_bytes=result.font_bytes,
        already_rendered_text="",
        characters="ABC XYZ",
    )
    assert coverage.fully_covered


@pytest.mark.feature("FNT-07")
def test_unknown_font_with_measurable_bytes_resolves_approximate(corpus: Corpus, font_index: list) -> None:
    with pikepdf.open(corpus.embedded_font_subset) as pdf:
        classification = classify_font(pdf, 0, "EmbeddedVeraBold")
    unknown = classification.model_copy(update={"base_font": "TotallyUnknownFontXYZ Bold"})

    with pymupdf.open(corpus.embedded_font_subset) as doc:
        xref = doc[0].get_fonts(full=True)[0][0]
        original_bytes = doc.extract_font(xref)[3]

    result = resolve_font(
        unknown,
        original_font_bytes=original_bytes,
        already_rendered_text="AB",
        needed_text="ABC",
        font_index=font_index,
    )
    assert result.tier == TIER_APPROXIMATE
    assert 0.0 < result.confidence < 1.0
    assert result.requires_approval is True
    assert result.font_bytes is not None
    assert "VeraBd" in result.note  # the bold Vera variant is the closest match by weight


@pytest.mark.feature("FNT-07")
def test_unknown_font_with_no_bytes_resolves_fallback(corpus: Corpus, font_index: list) -> None:
    with pikepdf.open(corpus.embedded_font_subset) as pdf:
        classification = classify_font(pdf, 0, "EmbeddedVeraBold")
    unknown = classification.model_copy(update={"base_font": "TotallyUnknownFontXYZ Bold"})

    result = resolve_font(
        unknown, original_font_bytes=None, already_rendered_text="AB", needed_text="ABC", font_index=font_index
    )
    assert result.tier == TIER_FALLBACK
    assert result.confidence < 0.5
    assert result.requires_approval is True
    assert result.fontname == "hebo"  # bold sans standard fallback, inferred from the name token "Bold"


@pytest.mark.feature("FNT-07")
def test_fallback_uses_serif_standard_font_for_a_serif_name(corpus: Corpus, font_index: list) -> None:
    with pikepdf.open(corpus.embedded_font_subset) as pdf:
        classification = classify_font(pdf, 0, "EmbeddedVeraBold")
    unknown = classification.model_copy(update={"base_font": "SomeUnknown Times Italic"})

    result = resolve_font(
        unknown, original_font_bytes=None, already_rendered_text="A", needed_text="B", font_index=font_index
    )
    assert result.tier == TIER_FALLBACK
    assert result.fontname == "tiit"  # Times italic


@pytest.mark.feature("FNT-08")
def test_exact_tier_merged_subset_covers_characters_never_seen_before(corpus: Corpus, font_index: list) -> None:
    with pikepdf.open(corpus.embedded_font_subset) as pdf:
        classification = classify_font(pdf, 0, "EmbeddedVeraBold")
    result = resolve_font(
        classification,
        original_font_bytes=None,
        already_rendered_text="AB",
        needed_text="completely new text!",
        font_index=font_index,
    )
    assert result.tier == TIER_EXACT

    from engine.fonts.coverage import check_coverage

    coverage = check_coverage(
        font_type="TrueType",
        embedded=True,
        font_bytes=result.font_bytes,
        already_rendered_text="",
        characters="completely new text!",
    )
    assert coverage.fully_covered
