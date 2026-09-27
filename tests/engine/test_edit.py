"""EDT-01/02/03/04/06 low-level drawing: engine.edit."""

from __future__ import annotations

import shutil
from pathlib import Path

import pikepdf
import pytest

from engine.document import Document
from engine.edit import insert_text_near, replace_span_text
from engine.fonts.match import build_font_index
from engine.fonts.style import extract_page_spans
from engine.verify import pixel_diff, render_to_array
from tests.corpus.build_corpus import Corpus


@pytest.fixture(scope="module")
def font_index() -> list:
    return build_font_index(include_system=False)


@pytest.mark.feature("EDT-01")
def test_replace_span_text_on_a_standard_font(corpus: Corpus, work_dir: Path, font_index: list) -> None:
    source = work_dir / "simple.pdf"
    shutil.copy(corpus.simple, source)
    doc = Document.open(source)
    spans = extract_page_spans(doc.raw, 0)

    result = replace_span_text(doc, 0, spans[0], "Goodbye, Editor!", font_index=font_index)

    assert result.tier == "exact"
    assert result.confidence == 1.0
    assert result.requires_approval is False
    assert doc.raw[0].get_text().strip() == "Goodbye, Editor!"
    doc.close()


@pytest.mark.feature("EDT-01")
def test_replace_span_text_on_an_embedded_subset_font(corpus: Corpus, work_dir: Path, font_index: list) -> None:
    source = work_dir / "subset.pdf"
    shutil.copy(corpus.embedded_font_subset, source)
    doc = Document.open(source)
    spans = extract_page_spans(doc.raw, 0)
    assert spans[0].style.text == "AB"

    result = replace_span_text(doc, 0, spans[0], "Completely New Text", font_index=font_index)

    assert result.tier == "exact"  # the same font (Bitstream Vera) is in the bundled index
    assert doc.raw[0].get_text().strip() == "Completely New Text"
    doc.close()


@pytest.mark.feature("EDT-01")
def test_replace_span_text_is_pixel_localized(corpus: Corpus, work_dir: Path, font_index: list) -> None:
    source = work_dir / "simple.pdf"
    shutil.copy(corpus.simple, source)
    doc = Document.open(source)
    before = render_to_array(doc.raw, 0, dpi=150)
    spans = extract_page_spans(doc.raw, 0)

    replace_span_text(doc, 0, spans[0], "Short", font_index=font_index)

    after = render_to_array(doc.raw, 0, dpi=150)
    diff = pixel_diff(before, after)
    assert 0.0 < diff.changed_fraction < 0.01  # something changed, and only a small region
    doc.close()


@pytest.mark.feature("EDT-01")
def test_replace_span_text_preserves_default_spacing_as_one_span(
    corpus: Corpus, work_dir: Path, font_index: list
) -> None:
    """A default-spacing edit must not fragment into one span per character
    (that would break any further edit on the same text -- see EDT-02 tests)."""
    source = work_dir / "simple.pdf"
    shutil.copy(corpus.simple, source)
    doc = Document.open(source)
    spans = extract_page_spans(doc.raw, 0)

    replace_span_text(doc, 0, spans[0], "A whole new sentence here", font_index=font_index)

    after_spans = extract_page_spans(doc.raw, 0)
    assert len(after_spans) == 1
    assert after_spans[0].style.text == "A whole new sentence here"
    doc.close()


@pytest.mark.feature("EDT-01")
def test_replace_span_text_matches_original_font_size(corpus: Corpus, work_dir: Path, font_index: list) -> None:
    source = work_dir / "simple.pdf"
    shutil.copy(corpus.simple, source)
    doc = Document.open(source)
    spans = extract_page_spans(doc.raw, 0)
    original_size = spans[0].style.size

    replace_span_text(doc, 0, spans[0], "New", font_index=font_index)

    after_spans = extract_page_spans(doc.raw, 0)
    assert after_spans[0].style.size == pytest.approx(original_size)
    doc.close()


@pytest.mark.feature("EDT-01")
def test_replace_span_text_respects_explicit_tc_tw_tz(work_dir: Path, font_index: list) -> None:
    """A span with non-default spacing must still be readable and use the right
    advance width, even though it draws per-character (see engine.edit docstring)."""
    path = work_dir / "custom_state.pdf"
    pdf = pikepdf.new()
    page = pdf.add_blank_page()
    font = pdf.make_indirect(
        pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name("/Helvetica"))
    )
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
    page.Contents = pdf.make_stream(b"BT\n3 Tc\n/F1 14 Tf\n1 0 0 1 72 700 Tm\n(Original) Tj\nET\n")
    pdf.save(path)
    pdf.close()

    doc = Document.open(path)
    spans = extract_page_spans(doc.raw, 0)
    assert spans[0].text_state is not None
    assert spans[0].text_state.char_spacing == 3.0

    result = replace_span_text(doc, 0, spans[0], "Wide", font_index=font_index)
    assert result.tier == "exact"
    # get_text() infers word breaks from visual gaps, and Tc=3 on a 14pt font
    # creates gaps wide enough to fool that heuristic -- check the real character
    # sequence via texttrace instead, which is exact.
    after_spans = extract_page_spans(doc.raw, 0)
    assert "".join(s.style.text for s in after_spans) == "Wide"
    doc.close()


@pytest.mark.feature("EDT-04")
def test_delete_by_replacing_with_empty_text_removes_the_span(corpus: Corpus, work_dir: Path, font_index: list) -> None:
    source = work_dir / "simple.pdf"
    shutil.copy(corpus.simple, source)
    doc = Document.open(source)
    spans = extract_page_spans(doc.raw, 0)

    replace_span_text(doc, 0, spans[0], "", font_index=font_index)

    assert doc.raw[0].get_text().strip() == ""
    doc.close()


@pytest.mark.feature("EDT-06")
def test_replace_span_text_with_override_size_and_color(corpus: Corpus, work_dir: Path, font_index: list) -> None:
    source = work_dir / "simple.pdf"
    shutil.copy(corpus.simple, source)
    doc = Document.open(source)
    spans = extract_page_spans(doc.raw, 0)
    original_text = spans[0].style.text

    replace_span_text(
        doc, 0, spans[0], original_text, font_index=font_index, override_size=24.0, override_color=(1.0, 0.0, 0.0)
    )

    after_spans = extract_page_spans(doc.raw, 0)
    assert after_spans[0].style.text == original_text
    assert after_spans[0].style.size == pytest.approx(24.0)
    assert after_spans[0].style.color == pytest.approx((1.0, 0.0, 0.0))
    doc.close()


@pytest.mark.feature("EDT-03")
def test_insert_text_near_does_not_touch_the_reference_span(corpus: Corpus, work_dir: Path, font_index: list) -> None:
    source = work_dir / "simple.pdf"
    shutil.copy(corpus.simple, source)
    doc = Document.open(source)
    spans = extract_page_spans(doc.raw, 0)
    reference = spans[0]
    original_text = reference.style.text

    result = insert_text_near(doc, 0, reference, " Extra!", (300, 72), font_index=font_index)

    assert result.tier == "exact"
    text = doc.raw[0].get_text()
    assert original_text in text
    assert "Extra!" in text
    doc.close()


@pytest.mark.feature("FNT-12")
def test_replace_span_text_verifies_by_default(corpus: Corpus, work_dir: Path, font_index: list) -> None:
    source = work_dir / "simple.pdf"
    shutil.copy(corpus.simple, source)
    doc = Document.open(source)
    spans = extract_page_spans(doc.raw, 0)

    result = replace_span_text(doc, 0, spans[0], "Verified Text", font_index=font_index)

    assert result.verification is not None
    assert result.verification.text_matches is True
    assert result.verification.diff.changed_fraction > 0.0
    assert result.verification.looks_right is True
    doc.close()


@pytest.mark.feature("FNT-12")
def test_replace_span_text_verify_false_skips_verification(corpus: Corpus, work_dir: Path, font_index: list) -> None:
    source = work_dir / "simple.pdf"
    shutil.copy(corpus.simple, source)
    doc = Document.open(source)
    spans = extract_page_spans(doc.raw, 0)

    result = replace_span_text(doc, 0, spans[0], "No Verify", font_index=font_index, verify=False)

    assert result.verification is None
    doc.close()


@pytest.mark.feature("FNT-12")
def test_delete_text_verification_reports_a_real_pixel_change(corpus: Corpus, work_dir: Path, font_index: list) -> None:
    source = work_dir / "simple.pdf"
    shutil.copy(corpus.simple, source)
    doc = Document.open(source)
    spans = extract_page_spans(doc.raw, 0)

    result = replace_span_text(doc, 0, spans[0], "", font_index=font_index)

    assert result.verification is not None
    assert result.verification.diff.changed_fraction > 0.0  # the deleted glyphs really disappeared
    doc.close()


@pytest.mark.feature("FNT-12")
def test_insert_text_near_verifies_the_new_text_landed(corpus: Corpus, work_dir: Path, font_index: list) -> None:
    source = work_dir / "simple.pdf"
    shutil.copy(corpus.simple, source)
    doc = Document.open(source)
    spans = extract_page_spans(doc.raw, 0)

    result = insert_text_near(doc, 0, spans[0], "Landed Here", (72, 300), font_index=font_index)

    assert result.verification is not None
    assert result.verification.text_matches is True
    doc.close()


@pytest.mark.feature("EDT-03")
def test_insert_text_near_matches_reference_style(corpus: Corpus, work_dir: Path, font_index: list) -> None:
    source = work_dir / "simple.pdf"
    shutil.copy(corpus.simple, source)
    doc = Document.open(source)
    spans = extract_page_spans(doc.raw, 0)
    reference = spans[0]

    insert_text_near(doc, 0, reference, "Matching", (72, 200), font_index=font_index)

    after_spans = extract_page_spans(doc.raw, 0)
    inserted = next(s for s in after_spans if s.style.text == "Matching")
    assert inserted.style.font == reference.style.font
    assert inserted.style.size == pytest.approx(reference.style.size)
    doc.close()
