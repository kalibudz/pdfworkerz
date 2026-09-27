"""FNT-15: Type3 font handling.

Full glyph-procedure reuse (drawing new text through a Type3 font's own
CharProcs) is not implemented -- that needs a different drawing path
entirely, emitting Tf/Tj against the font's own resource directly rather
than through PyMuPDF's Font/insert_text API, which cannot load a Type3
font at all (there's no font program to hand it). What's implemented and
tested here is the honest half: detecting a Type3 font (already covered
by FNT-03's classify_font) and routing edits through it to a clearly
labeled fallback rather than silently misreporting it as a normal,
trustworthy standard font.
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
from engine.fonts.resolve import TIER_FALLBACK, resolve_font
from engine.fonts.style import extract_page_spans
from tests.corpus.build_corpus import Corpus


@pytest.fixture(scope="module")
def font_index() -> list:
    return build_font_index(include_system=False)


@pytest.mark.feature("FNT-15")
def test_type3_font_resolves_to_fallback_not_a_false_exact_match(corpus: Corpus, font_index: list) -> None:
    with pikepdf.open(corpus.type3) as pdf:
        classification = classify_font(pdf, 0, "T3")

    result = resolve_font(
        classification, original_font_bytes=None, already_rendered_text="A", needed_text="B", font_index=font_index
    )
    assert result.tier == TIER_FALLBACK
    assert result.requires_approval is True
    assert "Type3" in result.note


@pytest.mark.feature("FNT-15")
def test_type3_font_is_never_reported_as_a_confident_exact_match(corpus: Corpus, font_index: list) -> None:
    """A Type3 font has no embedded program (embedded=False per FNT-03), which
    would otherwise route it into the "standard font, exact, no approval needed"
    path -- that's wrong for Type3 specifically and must be caught first."""
    with pikepdf.open(corpus.type3) as pdf:
        classification = classify_font(pdf, 0, "T3")
    assert classification.embedded is False  # confirms this case would hit the generic path without the fix

    result = resolve_font(
        classification, original_font_bytes=None, already_rendered_text="A", needed_text="B", font_index=font_index
    )
    assert result.confidence < 1.0
    assert result.requires_approval is True


@pytest.mark.feature("FNT-15")
def test_replace_span_text_on_a_type3_font_still_draws_readable_text(
    corpus: Corpus, work_dir: Path, font_index: list
) -> None:
    source = work_dir / "type3.pdf"
    shutil.copy(corpus.type3, source)
    doc = Document.open(source)
    spans = extract_page_spans(doc.raw, 0)
    assert spans[0].style.text == "A"

    result = replace_span_text(doc, 0, spans[0], "New text", font_index=font_index)

    assert result.tier == TIER_FALLBACK
    assert doc.raw[0].get_text().strip() == "New text"
    doc.close()
