"""FNT-03 (font classification) and FNT-04 (style fingerprint)."""

from __future__ import annotations

import pikepdf
import pytest

from engine.fonts.classify import classify_font, fingerprint_style, list_page_fonts
from tests.corpus.build_corpus import Corpus


@pytest.mark.feature("FNT-03")
def test_standard_font_is_type1_and_not_embedded(corpus: Corpus) -> None:
    with pikepdf.open(corpus.simple) as pdf:
        c = classify_font(pdf, 0, "helv")
    assert c.font_type == "Type1"
    assert c.base_font == "Helvetica"
    assert c.embedded is False
    assert c.embedded_format is None
    assert c.subset_tag is None


@pytest.mark.feature("FNT-03")
def test_embedded_full_truetype_font_is_type0_and_embedded(corpus: Corpus) -> None:
    with pikepdf.open(corpus.embedded_font_full) as pdf:
        c = classify_font(pdf, 0, "EmbeddedVeraBold")
    assert c.font_type == "Type0"
    assert c.is_composite is True
    assert c.descendant_type == "CIDFontType2"
    assert c.embedded is True
    assert c.embedded_format == "FontFile2"
    assert c.subset_tag is None
    assert "Bold" in c.base_font


@pytest.mark.feature("FNT-03")
def test_subset_font_has_a_six_letter_tag(corpus: Corpus) -> None:
    with pikepdf.open(corpus.embedded_font_subset) as pdf:
        c = classify_font(pdf, 0, "EmbeddedVeraBold")
    assert c.embedded is True
    assert c.subset_tag is not None
    assert len(c.subset_tag) == 6
    assert c.subset_tag.isupper()
    assert c.base_font == f"{c.subset_tag}+Bitstream Vera Sans Bold"


@pytest.mark.feature("FNT-03")
def test_type3_font_is_detected_and_never_reported_as_embedded(corpus: Corpus) -> None:
    with pikepdf.open(corpus.type3) as pdf:
        c = classify_font(pdf, 0, "T3")
    assert c.font_type == "Type3"
    assert c.embedded is False  # Type3 glyphs are content-stream procedures, not a font program
    assert c.is_composite is False


@pytest.mark.feature("FNT-03")
def test_to_unicode_presence_is_reported_accurately(corpus: Corpus) -> None:
    with pikepdf.open(corpus.simple) as pdf:
        standard = classify_font(pdf, 0, "helv")
    with pikepdf.open(corpus.embedded_font_full) as pdf:
        embedded = classify_font(pdf, 0, "EmbeddedVeraBold")
    assert standard.has_to_unicode is False  # standard-14 fonts don't need one
    assert embedded.has_to_unicode is True  # PyMuPDF writes one for embedded fonts


@pytest.mark.feature("FNT-03")
def test_list_page_fonts_returns_every_resource_name(corpus: Corpus) -> None:
    import pymupdf

    with pymupdf.open(corpus.bold_italic_standard) as doc:
        names = list_page_fonts(doc, 0)
    assert set(names) == {"helv", "hebo", "heit"}


@pytest.mark.feature("FNT-04")
def test_bold_is_inferred_from_the_base_font_name(corpus: Corpus) -> None:
    with pikepdf.open(corpus.bold_italic_standard) as pdf:
        regular = fingerprint_style(classify_font(pdf, 0, "helv"))
        bold = fingerprint_style(classify_font(pdf, 0, "hebo"))
    assert regular.bold is False
    assert bold.bold is True
    assert bold.weight >= 700 > regular.weight


@pytest.mark.feature("FNT-04")
def test_italic_is_inferred_from_the_base_font_name(corpus: Corpus) -> None:
    with pikepdf.open(corpus.bold_italic_standard) as pdf:
        regular = fingerprint_style(classify_font(pdf, 0, "helv"))
        italic = fingerprint_style(classify_font(pdf, 0, "heit"))
    assert regular.italic is False
    assert italic.italic is True


@pytest.mark.feature("FNT-04")
def test_sans_family_is_recognized_by_name(corpus: Corpus) -> None:
    with pikepdf.open(corpus.simple) as pdf:
        fp = fingerprint_style(classify_font(pdf, 0, "helv"))
    assert fp.family_class == "sans"


@pytest.mark.feature("FNT-04")
def test_bold_is_also_recognized_from_embedded_font_name_tokens(corpus: Corpus) -> None:
    with pikepdf.open(corpus.embedded_font_full) as pdf:
        fp = fingerprint_style(classify_font(pdf, 0, "EmbeddedVeraBold"))
    assert fp.bold is True
    assert fp.family_class == "sans"


@pytest.mark.feature("FNT-04")
def test_subset_tag_does_not_interfere_with_name_based_inference(corpus: Corpus) -> None:
    """Bold/family inference must look past the "ABCDEF+" subset prefix."""
    with pikepdf.open(corpus.embedded_font_subset) as pdf:
        fp = fingerprint_style(classify_font(pdf, 0, "EmbeddedVeraBold"))
    assert fp.bold is True
    assert fp.family_class == "sans"


@pytest.mark.feature("FNT-04")
def test_unknown_family_when_no_signal_is_present(corpus: Corpus) -> None:
    with pikepdf.open(corpus.type3) as pdf:
        fp = fingerprint_style(classify_font(pdf, 0, "T3"))
    assert fp.family_class == "unknown"
    assert fp.bold is False
    assert fp.italic is False
