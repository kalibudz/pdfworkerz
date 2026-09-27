"""FNT-14: CJK text. See this module's docstring for what is and isn't covered.

**Covered and tested**: extraction, classification, font matching (FNT-06),
glyph-borrow merging (FNT-08) and style-matched editing (EDT-01/02) for CJK
text using a CID/Identity-H composite font -- confirmed the common
real-world shape for CJK PDFs, and the same code path FNT-01 through FNT-10
already use for any composite font. No CJK-specific code was needed for any
of this; it works because the pipeline was never Latin-only.

**Not covered, and explicitly not claimed**: right-to-left scripts (Arabic,
Hebrew) and CJK vertical writing mode. Confirmed empirically that inserting
Arabic text makes PyMuPDF apply contextual shaping before drawing --
texttrace then reports the shaped presentation-form codepoints (Unicode
"Arabic Presentation Forms"), not the logical characters a person typed --
so a literal find/replace against logical Arabic text would not match.
Vertical writing mode did not take effect through the API path tried here
(`Page.insert_font(..., wmode=1)`). Both are real, substantial features of
their own and are left for a later session rather than a partial, untested
attempt.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pikepdf
import pytest

from engine.document import Document
from engine.edit import replace_span_text
from engine.fonts.classify import classify_font
from engine.fonts.match import build_font_index
from engine.fonts.resolve import TIER_EXACT
from engine.fonts.style import extract_page_spans
from tests.corpus.build_corpus import Corpus

HELLO_WORLD = "你好世界"
TEST_CHINESE = "测试中文"


@pytest.fixture(scope="module")
def font_index() -> list:
    return build_font_index(include_system=False)


@pytest.mark.feature("FNT-14")
def test_cjk_text_extracts_correctly(corpus: Corpus) -> None:
    with pikepdf.open(corpus.cjk):
        pass  # sanity: the fixture itself opens cleanly

    import pymupdf

    with pymupdf.open(corpus.cjk) as doc:
        spans = extract_page_spans(doc, 0)
    assert len(spans) == 1
    assert spans[0].style.text == HELLO_WORLD


@pytest.mark.feature("FNT-14")
def test_cjk_font_classifies_as_a_composite_font(corpus: Corpus) -> None:
    with pikepdf.open(corpus.cjk) as pdf:
        classification = classify_font(pdf, 0, "CJKTest")
    assert classification.is_composite is True
    assert classification.descendant_type == "CIDFontType0"  # Noto Sans CJK is CFF-based
    assert classification.embedded is True


@pytest.mark.feature("FNT-14")
def test_cjk_font_is_findable_in_the_bundled_index(corpus: Corpus, font_index: list) -> None:
    from engine.fonts.match import find_by_name

    with pikepdf.open(corpus.cjk) as pdf:
        classification = classify_font(pdf, 0, "CJKTest")
    match = find_by_name(font_index, classification.base_font)
    assert match is not None
    assert match.path.name == "NotoSansSC-Subset.otf"


@pytest.mark.feature("FNT-14")
def test_replace_cjk_text_with_new_cjk_text(corpus: Corpus, work_dir: Path, font_index: list) -> None:
    source = work_dir / "cjk.pdf"
    shutil.copy(corpus.cjk, source)
    doc = Document.open(source)
    spans = extract_page_spans(doc.raw, 0)

    result = replace_span_text(doc, 0, spans[0], TEST_CHINESE, font_index=font_index)

    assert result.tier == TIER_EXACT
    assert result.confidence == 1.0
    assert result.requires_approval is False
    assert result.verification is not None
    assert result.verification.text_matches is True

    after_spans = extract_page_spans(doc.raw, 0)
    assert after_spans[0].style.text == TEST_CHINESE
    doc.close()


@pytest.mark.feature("FNT-14")
def test_replace_cjk_text_covers_characters_the_original_never_had(
    corpus: Corpus, work_dir: Path, font_index: list
) -> None:
    """The bundled font is itself a subset (~65 characters); this proves the
    merge (FNT-08) genuinely covers new characters, not just a lucky overlap."""
    source = work_dir / "cjk.pdf"
    shutil.copy(corpus.cjk, source)
    doc = Document.open(source)
    spans = extract_page_spans(doc.raw, 0)
    assert not set(TEST_CHINESE) & set(HELLO_WORLD)  # the two fixtures share no characters

    replace_span_text(doc, 0, spans[0], TEST_CHINESE, font_index=font_index)

    assert doc.raw[0].get_text().strip() == TEST_CHINESE
    doc.close()


@pytest.mark.feature("FNT-14")
def test_mixed_cjk_and_latin_replacement(corpus: Corpus, work_dir: Path, font_index: list) -> None:
    source = work_dir / "cjk.pdf"
    shutil.copy(corpus.cjk, source)
    doc = Document.open(source)
    spans = extract_page_spans(doc.raw, 0)

    result = replace_span_text(doc, 0, spans[0], "PDFWorkerz 测试", font_index=font_index)

    assert result.tier == TIER_EXACT
    assert doc.raw[0].get_text().strip() == "PDFWorkerz 测试"
    doc.close()
