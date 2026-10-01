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

    # This subset has no cmap, so its letters can't be measured: cap height, x-height
    # and width fall back to defaults, and weight is the one real signal. Among other
    # bold families (Open Sans Bold, say) the winner is then arbitrary, so the weight
    # preference is checked within one family.
    vera = [c for c in font_index if c.family_name == "Bitstream Vera Sans"]
    result = resolve_font(
        unknown,
        original_font_bytes=original_bytes,
        already_rendered_text="AB",
        needed_text="ABC",
        font_index=vera,
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


def _non_embedded(base_font: str) -> object:
    pdf = pikepdf.new()
    page = pdf.add_blank_page()
    font = pdf.make_indirect(
        pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name("/" + base_font))
    )
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
    return classify_font(pdf, 0, "F1")


def _resolve_non_embedded(base_font: str, font_index: list) -> object:
    return resolve_font(
        _non_embedded(base_font),  # type: ignore[arg-type]
        original_font_bytes=None,
        already_rendered_text="",
        needed_text="abc",
        font_index=font_index,
    )


@pytest.mark.feature("FNT-06")
@pytest.mark.parametrize(
    "base_font, fontname",
    [("Helvetica-Bold", "hebo"), ("Times-Roman", "tiro"), ("Symbol", "symb"), ("ZapfDingbats", "zadb")],
)
def test_standard_14_names_resolve_exact_to_their_own_builtin(font_index: list, base_font: str, fontname: str) -> None:
    result = _resolve_non_embedded(base_font, font_index)
    assert (result.tier, result.fontname, result.requires_approval) == (TIER_EXACT, fontname, False)  # type: ignore[attr-defined]


@pytest.mark.feature("FNT-07")
def test_a_non_embedded_metric_compatible_font_is_approximate_and_needs_approval(font_index: list) -> None:
    # font_index has no system fonts here, so Arial can't be found installed.
    result = _resolve_non_embedded("ArialMT", font_index)
    assert (result.tier, result.fontname, result.requires_approval) == (TIER_APPROXIMATE, "helv", True)  # type: ignore[attr-defined]


@pytest.mark.feature("FNT-07")
@pytest.mark.parametrize("base_font", ["Verdana", "Wingdings", "MS-Mincho", "TotallyUnknownFont"])
def test_any_other_non_embedded_font_is_never_silently_exact(font_index: list, base_font: str) -> None:
    result = _resolve_non_embedded(base_font, font_index)
    assert result.tier == TIER_FALLBACK and result.requires_approval  # type: ignore[attr-defined]


@pytest.mark.feature("FNT-06")
def test_a_non_embedded_font_that_is_installed_is_embedded_exactly(font_index: list) -> None:
    result = _resolve_non_embedded("BitstreamVeraSans-Roman", font_index)  # bundled, so always "installed"
    assert result.tier == TIER_EXACT and result.font_bytes is not None  # type: ignore[attr-defined]


# -- a standard-14 fallback that can't actually draw the needed text --------------------
#
# Found from live use: a block containing a checkmark glyph (its own font, Type3, routes
# to a standard-font fallback per FNT-15) was restyled bold -- the checkmark silently
# vanished from the page, confirmed to be PyMuPDF drawing a replacement-glyph mark for any
# character above U+00FF when inserting through one of its own 14 built-in fonts, not
# refusing or reporting it. Every return path in resolve_font/resolve_non_embedded that
# names one of those 14 fonts now checks `needed_text` against exactly that limit.


@pytest.mark.feature("FNT-06")
def test_standard_14_builtin_flags_a_character_it_cannot_draw(font_index: list) -> None:
    result = _resolve_non_embedded("Helvetica", font_index)  # resolves exact via test_standard_14_names_... above
    with_emoji = resolve_font(
        _non_embedded("Helvetica"),  # type: ignore[arg-type]
        original_font_bytes=None,
        already_rendered_text="",
        needed_text="✅ done",
        font_index=font_index,
    )
    assert result.requires_approval is False  # the baseline (no uncoverable character) stays silent
    assert with_emoji.tier == TIER_EXACT  # still the right standard font...
    assert with_emoji.requires_approval is True  # ...but now flagged before anything is drawn
    assert "\\u2705" in with_emoji.note or "✅" in with_emoji.note
    assert "would not be drawn correctly" in with_emoji.note


@pytest.mark.feature("FNT-15")
def test_type3_fallback_flags_a_character_the_fallback_cannot_draw(corpus: Corpus, font_index: list) -> None:
    with pikepdf.open(corpus.type3) as pdf:
        classification = classify_font(pdf, 0, "T3")
    result = resolve_font(
        classification,
        original_font_bytes=None,
        already_rendered_text="",
        needed_text="✅ Approved",
        font_index=font_index,
    )
    assert result.tier == TIER_FALLBACK
    assert result.requires_approval is True
    assert "would not be drawn correctly" in result.note


@pytest.mark.feature("FNT-15")
def test_type3_fallback_with_only_ordinary_text_is_not_flagged(corpus: Corpus, font_index: list) -> None:
    with pikepdf.open(corpus.type3) as pdf:
        classification = classify_font(pdf, 0, "T3")
    result = resolve_font(
        classification,
        original_font_bytes=None,
        already_rendered_text="",
        needed_text="Approved",
        font_index=font_index,
    )
    assert "would not be drawn correctly" not in result.note


@pytest.mark.feature("FNT-07")
def test_metric_compatible_fallback_also_flags_an_uncoverable_character(font_index: list) -> None:
    result = resolve_font(
        _non_embedded("ArialMT"),  # type: ignore[arg-type]
        original_font_bytes=None,
        already_rendered_text="",
        needed_text="★ star",
        font_index=font_index,
    )
    assert result.tier == TIER_APPROXIMATE
    assert result.requires_approval is True
    assert "would not be drawn correctly" in result.note


@pytest.mark.feature("FNT-06")
def test_symbolic_builtins_are_never_flagged_by_the_latin1_check(font_index: list) -> None:
    """Symbol and ZapfDingbats use their own encodings entirely outside Latin-1 by design;
    the coverage check only ever applies to the ordinary standard-14 text fonts."""
    result = _resolve_non_embedded("Symbol", font_index)
    assert result.requires_approval is False
    assert "would not be drawn correctly" not in result.note
