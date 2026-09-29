"""EDT-01/02/03/04/06: the typed text-editing Ops."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pikepdf
import pytest

from engine.document import Document
from engine.errors import OpValidationError
from engine.fonts.style import extract_page_spans
from engine.ops.base import parse_op
from engine.ops.text import (
    DeleteTextOp,
    InsertTextOp,
    PreviewTextOp,
    ReflowTextOp,
    ReplaceSpanTextOp,
    ReplaceTextOp,
    RestyleTextOp,
)
from tests.corpus.build_corpus import Corpus


@pytest.fixture
def simple_doc(corpus: Corpus, work_dir: Path) -> Iterator[Document]:
    source = work_dir / "simple.pdf"
    shutil.copy(corpus.simple, source)
    doc = Document.open(source)
    yield doc
    doc.close()


@pytest.fixture
def paragraph_doc(corpus: Corpus, work_dir: Path) -> Iterator[Document]:
    source = work_dir / "paragraph.pdf"
    shutil.copy(corpus.paragraph, source)
    doc = Document.open(source)
    yield doc
    doc.close()


@pytest.mark.feature("EDT-02")
def test_replace_text_op_literal_match(simple_doc: Document) -> None:
    op = ReplaceTextOp(match="PDFWorkerz", replacement="Editor", require_tier="exact")
    results = op.apply(simple_doc)
    assert len(results) == 1
    assert results[0].tier == "exact"
    assert simple_doc.raw[0].get_text().strip() == "Hello, Editor."


@pytest.mark.feature("EDT-02")
def test_replace_text_op_regex_match(simple_doc: Document) -> None:
    op = ReplaceTextOp(match=r"PDF\w+", replacement="App", mode="regex")
    op.apply(simple_doc)
    assert simple_doc.raw[0].get_text().strip() == "Hello, App."


@pytest.mark.feature("EDT-02")
def test_replace_text_op_case_insensitive(simple_doc: Document) -> None:
    op = ReplaceTextOp(match="pdfworkerz", replacement="X", case_sensitive=False)
    op.apply(simple_doc)
    assert simple_doc.raw[0].get_text().strip() == "Hello, X."


@pytest.mark.feature("EDT-02")
def test_replace_text_op_no_match_is_a_no_op(simple_doc: Document) -> None:
    op = ReplaceTextOp(match="NotInTheDocument", replacement="X")
    results = op.apply(simple_doc)
    assert results == []
    assert simple_doc.raw[0].get_text().strip() == "Hello, PDFWorkerz."


@pytest.mark.feature("EDT-02")
def test_replace_text_op_multiple_occurrences_on_one_page(work_dir: Path) -> None:
    path = work_dir / "repeated.pdf"
    pdf = pikepdf.new()
    page = pdf.add_blank_page()
    font = pdf.make_indirect(
        pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name("/Helvetica"))
    )
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
    page.Contents = pdf.make_stream(b"BT\n/F1 12 Tf\n1 0 0 1 72 700 Tm\n(cat and cat and cat) Tj\nET\n")
    pdf.save(path)
    pdf.close()

    doc = Document.open(path)
    op = ReplaceTextOp(match="cat", replacement="dog")
    results = op.apply(doc)
    assert len(results) == 1  # one span: all three matches are replaced in a single redraw
    assert doc.raw[0].get_text().strip() == "dog and dog and dog"
    doc.close()


@pytest.mark.feature("EDT-02")
def test_replace_text_op_require_tier_rejects_a_weak_match(corpus: Corpus, work_dir: Path) -> None:
    """An *embedded* font (FontFile2 present) whose name matches nothing available
    must fall through to a weaker tier, which require_tier="exact" then rejects.
    Renamed from a real embedded fixture so `embedded` stays true -- a bare Type1
    dict with no FontFile is (correctly) treated as a non-embedded standard font
    instead, which always resolves "exact" and wouldn't exercise this path."""
    path = work_dir / "unmatchable.pdf"
    with pikepdf.open(corpus.embedded_font_subset) as pdf:
        font = pdf.pages[0].Resources.Font["/EmbeddedVeraBold"]
        font["/BaseFont"] = pikepdf.Name("/XYZUNK+TotallyUnknownFontXYZ")
        font["/DescendantFonts"][0]["/BaseFont"] = pikepdf.Name("/XYZUNK+TotallyUnknownFontXYZ")
        pdf.save(path)

    doc = Document.open(path)
    op = ReplaceTextOp(match="AB", replacement="world", require_tier="exact")
    with pytest.raises(OpValidationError, match="fell back to tier"):
        op.apply(doc)
    doc.close()


@pytest.mark.feature("EDT-02")
def test_replace_text_op_round_trips_through_json() -> None:
    op = ReplaceTextOp(match="a", replacement="b", mode="regex", case_sensitive=False, page_index=2)
    restored = parse_op(op.model_dump())
    assert restored == op


@pytest.mark.feature("EDT-04")
def test_delete_text_op(simple_doc: Document) -> None:
    op = DeleteTextOp(match="PDFWorkerz")
    results = op.apply(simple_doc)
    assert len(results) == 1
    assert simple_doc.raw[0].get_text().strip() == "Hello, ."


@pytest.mark.feature("EDT-04")
def test_delete_text_op_regex(simple_doc: Document) -> None:
    op = DeleteTextOp(match=r",\s*PDFWorkerz", mode="regex")
    op.apply(simple_doc)
    assert simple_doc.raw[0].get_text().strip() == "Hello."


@pytest.mark.feature("EDT-04")
def test_delete_text_op_no_match_is_a_no_op(simple_doc: Document) -> None:
    results = DeleteTextOp(match="nope").apply(simple_doc)
    assert results == []


@pytest.mark.feature("EDT-06")
def test_restyle_text_op_changes_color_not_text(simple_doc: Document) -> None:
    op = RestyleTextOp(match="PDFWorkerz", color=(1.0, 0.0, 0.0))
    results = op.apply(simple_doc)
    assert len(results) == 1
    assert simple_doc.raw[0].get_text().strip() == "Hello, PDFWorkerz."

    from engine.fonts.style import extract_page_spans

    spans = extract_page_spans(simple_doc.raw, 0)
    assert any(s.style.color == pytest.approx((1.0, 0.0, 0.0)) for s in spans)


@pytest.mark.feature("EDT-06")
def test_restyle_text_op_changes_size(simple_doc: Document) -> None:
    op = RestyleTextOp(match="PDFWorkerz", size=30.0)
    op.apply(simple_doc)

    from engine.fonts.style import extract_page_spans

    spans = extract_page_spans(simple_doc.raw, 0)
    assert any(s.style.size == pytest.approx(30.0) for s in spans)


@pytest.mark.feature("EDT-06")
def test_restyle_text_op_requires_at_least_one_field(simple_doc: Document) -> None:
    with pytest.raises(OpValidationError, match="set at least one of size, color, font, bold or italic"):
        RestyleTextOp(match="PDFWorkerz").apply(simple_doc)


@pytest.mark.feature("EDT-03")
def test_insert_text_op(simple_doc: Document) -> None:
    op = InsertTextOp(page_index=0, text=" Extra", position=(300, 72), reference_match="PDFWorkerz")
    result = op.apply(simple_doc)
    assert result.tier == "exact"
    text = simple_doc.raw[0].get_text()
    assert "Hello, PDFWorkerz." in text
    assert "Extra" in text


@pytest.mark.feature("EDT-03")
def test_insert_text_op_raises_when_reference_not_found(simple_doc: Document) -> None:
    op = InsertTextOp(page_index=0, text="x", position=(0, 0), reference_match="NoSuchText")
    with pytest.raises(OpValidationError, match="no span matches"):
        op.apply(simple_doc)


@pytest.mark.feature("EDT-03")
def test_insert_text_op_round_trips_through_json() -> None:
    op = InsertTextOp(page_index=1, text="hi", position=(10.5, 20.5), reference_match="ref")
    restored = parse_op(op.model_dump())
    assert restored == op


@pytest.mark.feature("FNT-11")
def test_reflow_text_op_rewraps_the_matched_paragraph(paragraph_doc: Document) -> None:
    op = ReflowTextOp(match="line two", new_text="Short new text.", page_index=0)
    results = op.apply(paragraph_doc)

    assert len(results) == 3
    text = paragraph_doc.raw[0].get_text()
    assert "Short new text." in text.replace("\n", " ")
    assert "A separate paragraph starts here." in text  # the other paragraph is untouched


@pytest.mark.feature("FNT-11")
def test_reflow_text_op_matches_by_any_line_in_the_block(paragraph_doc: Document) -> None:
    op_first_line = ReflowTextOp(match="line one", new_text="A", page_index=0)
    op_first_line.apply(paragraph_doc)
    # after reflowing via the first line's text, the third line's original wording is gone
    assert "line three" not in paragraph_doc.raw[0].get_text()


@pytest.mark.feature("FNT-11")
def test_reflow_text_op_raises_when_no_line_matches(paragraph_doc: Document) -> None:
    op = ReflowTextOp(match="NoSuchLineHere", new_text="x", page_index=0)
    with pytest.raises(OpValidationError, match="no line matches"):
        op.apply(paragraph_doc)


@pytest.mark.feature("FNT-11")
def test_reflow_text_op_rejects_overflow_by_default(paragraph_doc: Document) -> None:
    huge_text = " ".join(f"word{i}" for i in range(200))
    op = ReflowTextOp(match="line one", new_text=huge_text, page_index=0)
    with pytest.raises(OpValidationError, match="overflow"):
        op.apply(paragraph_doc)


@pytest.mark.feature("FNT-11")
def test_reflow_text_op_allow_overflow_true_succeeds_with_truncated_result(paragraph_doc: Document) -> None:
    huge_text = " ".join(f"word{i}" for i in range(200))
    op = ReflowTextOp(match="line one", new_text=huge_text, page_index=0, allow_overflow=True)
    results = op.apply(paragraph_doc)

    assert len(results) == 3
    assert results[-1].requires_approval is True
    assert "overflow:" in results[-1].note


@pytest.mark.feature("FNT-11")
def test_reflow_text_op_require_tier_rejects_a_weak_match(corpus: Corpus, work_dir: Path) -> None:
    path = work_dir / "unmatchable_paragraph.pdf"
    with pikepdf.open(corpus.embedded_font_subset) as pdf:
        font = pdf.pages[0].Resources.Font["/EmbeddedVeraBold"]
        font["/BaseFont"] = pikepdf.Name("/XYZUNK+TotallyUnknownFontXYZ")
        font["/DescendantFonts"][0]["/BaseFont"] = pikepdf.Name("/XYZUNK+TotallyUnknownFontXYZ")
        pdf.save(path)

    doc = Document.open(path)
    # allow_overflow=True: the fixture's only line is "AB" (a 2-character-wide
    # block), so any non-trivial replacement overflows it regardless of font
    # tier -- allowing overflow isolates the tier check this test targets.
    op = ReflowTextOp(match="AB", new_text="world", page_index=0, require_tier="exact", allow_overflow=True)
    with pytest.raises(OpValidationError, match="fell back to tier"):
        op.apply(doc)

    doc.close()

    # Without allow_overflow both problems are reported together, not just the first one checked.
    # (A fresh copy: a direct apply() draws before it validates; only the journal rolls that back.)
    doc = Document.open(path)
    both = ReflowTextOp(match="AB", new_text="world", page_index=0, require_tier="exact")
    with pytest.raises(OpValidationError, match=r"overflow:.*; font resolution fell back to tier"):
        both.apply(doc)
    doc.close()


@pytest.mark.feature("FNT-11")
def test_reflow_text_op_round_trips_through_json() -> None:
    op = ReflowTextOp(match="line one", new_text="new", page_index=0, allow_overflow=True, case_sensitive=False)
    restored = parse_op(op.model_dump())
    assert restored == op


# -- PreviewTextOp and ReplaceSpanTextOp (UI-02/UI-03 support: a live, non-
# mutating preview of a text edit's font resolution, and an index-precise
# commit that can't accidentally edit a different span with the same text) --


@pytest.mark.feature("FNT-07")
def test_preview_text_op_reports_an_exact_match_without_changing_anything(simple_doc: Document) -> None:
    """ "Without changing anything" means the page's own content, not literal
    byte-for-byte serialization -- confirmed separately that even a plain
    PyMuPDF read (Document.extract_font) already perturbs tobytes()'s exact
    output with zero drawing or redaction involved, so that's not a
    meaningful signal of mutation here."""
    op = PreviewTextOp(page_index=0, span_index=0, needed_text="Editor")
    result = op.apply(simple_doc)
    assert result.tier == "exact"
    assert result.confidence == 1.0
    assert result.requires_approval is False
    assert simple_doc.page_count == 1
    assert simple_doc.raw[0].get_text().strip() == "Hello, PDFWorkerz."  # its own text is still there, unedited


@pytest.mark.feature("FNT-15")
def test_preview_text_op_reports_fallback_for_a_type3_font(corpus: Corpus, work_dir: Path) -> None:
    source = work_dir / "type3.pdf"
    shutil.copy(corpus.type3, source)
    doc = Document.open(source)
    op = PreviewTextOp(page_index=0, span_index=0, needed_text="anything")
    result = op.apply(doc)
    assert result.tier == "fallback"
    assert result.requires_approval is True
    assert "Type3" in result.note
    doc.close()


@pytest.mark.feature("FNT-01")
def test_preview_text_op_rejects_an_out_of_range_span_index(simple_doc: Document) -> None:
    op = PreviewTextOp(page_index=0, span_index=99, needed_text="x")
    with pytest.raises(OpValidationError, match="out of range"):
        op.apply(simple_doc)


@pytest.mark.feature("FNT-07")
def test_preview_text_op_round_trips_through_json() -> None:
    op = PreviewTextOp(page_index=0, span_index=1, needed_text="new text")
    restored = parse_op(op.model_dump())
    assert restored == op


@pytest.mark.feature("EDT-02")
def test_replace_span_text_op_edits_only_the_targeted_span(work_dir: Path) -> None:
    """The same text appearing twice on a page (ReplaceTextOp's own test above
    edits every occurrence by design) -- span_index must pick exactly one,
    the whole point of adding this Op rather than reusing ReplaceTextOp for
    click-to-edit, where the user picked one specific instance on screen."""
    path = work_dir / "repeated.pdf"
    pdf = pikepdf.new()
    page = pdf.add_blank_page()
    font = pdf.make_indirect(
        pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name("/Helvetica"))
    )
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
    page.Contents = pdf.make_stream(
        b"BT\n/F1 12 Tf\n1 0 0 1 72 700 Tm\n(cat) Tj\nET\nBT\n/F1 12 Tf\n1 0 0 1 72 650 Tm\n(cat) Tj\nET\n"
    )
    pdf.save(path)
    pdf.close()

    doc = Document.open(path)
    op = ReplaceSpanTextOp(page_index=0, span_index=1, new_text="dog", require_tier="exact")
    result = op.apply(doc)
    assert result.tier == "exact"

    remaining = [span.style.text for span in extract_page_spans(doc.raw, 0)]
    assert "cat" in remaining  # the first occurrence (span 0) is untouched
    assert "dog" in remaining  # only the second (span 1) changed
    doc.close()


@pytest.mark.feature("EDT-02")
def test_replace_span_text_op_require_tier_rejects_a_weak_match(corpus: Corpus, work_dir: Path) -> None:
    path = work_dir / "unmatchable.pdf"
    with pikepdf.open(corpus.embedded_font_subset) as pdf:
        font = pdf.pages[0].Resources.Font["/EmbeddedVeraBold"]
        font["/BaseFont"] = pikepdf.Name("/XYZUNK+TotallyUnknownFontXYZ")
        font["/DescendantFonts"][0]["/BaseFont"] = pikepdf.Name("/XYZUNK+TotallyUnknownFontXYZ")
        pdf.save(path)

    doc = Document.open(path)
    op = ReplaceSpanTextOp(page_index=0, span_index=0, new_text="world", require_tier="exact")
    with pytest.raises(OpValidationError, match="fell back to tier"):
        op.apply(doc)
    doc.close()


@pytest.mark.feature("EDT-02")
def test_replace_span_text_op_rejects_an_out_of_range_span_index(simple_doc: Document) -> None:
    op = ReplaceSpanTextOp(page_index=0, span_index=99, new_text="x")
    with pytest.raises(OpValidationError, match="out of range"):
        op.apply(simple_doc)


@pytest.mark.feature("EDT-02")
def test_replace_span_text_op_round_trips_through_json() -> None:
    op = ReplaceSpanTextOp(page_index=0, span_index=1, new_text="new text", fit=True)
    restored = parse_op(op.model_dump())
    assert restored == op
