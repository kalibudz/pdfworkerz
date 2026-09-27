"""FNT-13: missing-ToUnicode recovery."""

from __future__ import annotations

from pathlib import Path

import pikepdf
import pymupdf
import pytest

from engine.fonts.style import extract_page_spans
from engine.fonts.tounicode import REPLACEMENT_CHAR, build_gid_to_unicode, recover_broken_spans
from tests.corpus.build_corpus import Corpus


def _strip_to_unicode(source_path: Path, dest_path: Path, resource_name: str) -> None:
    """A copy of `source_path` with its ToUnicode CMap removed from `resource_name`."""
    with pikepdf.open(source_path) as pdf:
        font = pdf.pages[0].Resources.Font[f"/{resource_name}"]
        del font["/ToUnicode"]
        pdf.save(dest_path)


@pytest.mark.feature("FNT-13")
def test_missing_to_unicode_makes_mupdf_report_replacement_characters(corpus: Corpus, work_dir: Path) -> None:
    """Confirms the failure this feature recovers from actually happens, so the
    recovery test below is proving something real, not a no-op."""
    broken = work_dir / "broken.pdf"
    _strip_to_unicode(corpus.embedded_font_full, broken, "EmbeddedVeraBold")

    with pymupdf.open(broken) as doc:
        spans = extract_page_spans(doc, 0)
    assert REPLACEMENT_CHAR in spans[0].style.text


@pytest.mark.feature("FNT-13")
def test_recover_broken_spans_reconstructs_the_original_text(corpus: Corpus, work_dir: Path) -> None:
    broken = work_dir / "broken.pdf"
    _strip_to_unicode(corpus.embedded_font_full, broken, "EmbeddedVeraBold")

    with pymupdf.open(broken) as doc:
        spans = extract_page_spans(doc, 0)
        recovered = recover_broken_spans(doc, 0, spans)

    assert REPLACEMENT_CHAR not in recovered[0].style.text
    assert recovered[0].style.text == "Embedded bold, full font"


@pytest.mark.feature("FNT-13")
def test_recovered_chars_keep_their_original_positions(corpus: Corpus, work_dir: Path) -> None:
    broken = work_dir / "broken.pdf"
    _strip_to_unicode(corpus.embedded_font_full, broken, "EmbeddedVeraBold")

    with pymupdf.open(broken) as doc:
        spans = extract_page_spans(doc, 0)
        before_boxes = [(c.origin, c.bbox) for c in spans[0].style.chars]
        recovered = recover_broken_spans(doc, 0, spans)

    after_boxes = [(c.origin, c.bbox) for c in recovered[0].style.chars]
    assert after_boxes == before_boxes
    assert [c.char for c in recovered[0].style.chars] == list("Embedded bold, full font")


@pytest.mark.feature("FNT-13")
def test_recover_broken_spans_is_a_no_op_when_nothing_is_broken(corpus: Corpus) -> None:
    with pymupdf.open(corpus.simple) as doc:
        spans = extract_page_spans(doc, 0)
        recovered = recover_broken_spans(doc, 0, spans)
    assert recovered == spans


@pytest.mark.feature("FNT-13")
def test_recover_broken_spans_leaves_unrecoverable_text_unchanged(corpus: Corpus, work_dir: Path) -> None:
    """A subset font has no cmap at all (confirmed by FNT-05/FNT-08's tests), so
    it can't be reversed; recovery must not guess and must not crash."""
    broken = work_dir / "broken_subset.pdf"
    _strip_to_unicode(corpus.embedded_font_subset, broken, "EmbeddedVeraBold")

    with pymupdf.open(broken) as doc:
        spans = extract_page_spans(doc, 0)
        assert REPLACEMENT_CHAR in spans[0].style.text  # confirms the premise
        recovered = recover_broken_spans(doc, 0, spans)

    assert recovered == spans


@pytest.mark.feature("FNT-13")
def test_build_gid_to_unicode_reads_a_real_font(corpus: Corpus) -> None:
    with pymupdf.open(corpus.embedded_font_full) as doc:
        xref = doc[0].get_fonts(full=True)[0][0]
        font_bytes = doc.extract_font(xref)[3]

    gid_map = build_gid_to_unicode(font_bytes)
    assert gid_map is not None
    assert len(gid_map) > 50  # Bitstream Vera Bold covers a broad Latin-1 range


@pytest.mark.feature("FNT-13")
def test_build_gid_to_unicode_returns_none_for_garbage_bytes() -> None:
    assert build_gid_to_unicode(b"not a font") is None


@pytest.mark.feature("FNT-13")
def test_build_gid_to_unicode_returns_none_for_a_cmap_less_subset(corpus: Corpus) -> None:
    with pymupdf.open(corpus.embedded_font_subset) as doc:
        xref = doc[0].get_fonts(full=True)[0][0]
        font_bytes = doc.extract_font(xref)[3]
    assert build_gid_to_unicode(font_bytes) is None
