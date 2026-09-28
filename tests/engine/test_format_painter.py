"""EDT-07: the format painter (CopyStyleOp)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pikepdf
import pytest

from engine.document import Document
from engine.errors import OpValidationError
from engine.fonts.style import SpanTrace, extract_page_spans
from engine.ops.base import parse_op
from engine.ops.text import CopyStyleOp
from tests.corpus.build_corpus import Corpus


def _by_text(doc: Document, page_index: int, text: str) -> SpanTrace:
    return next(span for span in extract_page_spans(doc.raw, page_index) if span.style.text == text)


def _styled_pair(path: Path) -> None:
    """Page 0: a small black Helvetica label and a large red Times heading.
    Page 1: one more small black Helvetica label, for the cross-page case."""
    pdf = pikepdf.new()
    helvetica = pdf.make_indirect(
        pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name("/Helvetica"))
    )
    times = pdf.make_indirect(
        pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name("/Times-Bold"))
    )
    for content in (
        b"BT /F1 10 Tf 72 700 Td (plain label) Tj ET\nBT 1 0 0 rg /F2 20 Tf 72 600 Td (Heading) Tj ET\n",
        b"BT /F1 10 Tf 72 700 Td (other page) Tj ET\n",
    ):
        page = pdf.add_blank_page()
        page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=helvetica, F2=times))
        page.Contents = pdf.make_stream(content)
    pdf.save(path)
    pdf.close()


@pytest.fixture
def styled_doc(work_dir: Path) -> Document:
    path = work_dir / "styled.pdf"
    _styled_pair(path)
    return Document.open(path)


@pytest.mark.feature("EDT-07")
def test_copy_style_applies_font_size_and_color_but_keeps_the_text(styled_doc: Document) -> None:
    label = _by_text(styled_doc, 0, "plain label")
    heading = _by_text(styled_doc, 0, "Heading")

    result = CopyStyleOp(
        page_index=0,
        span_index=heading.style.span_index,
        target_page_index=0,
        target_span_index=label.style.span_index,
    ).apply(styled_doc)

    assert result.tier == "exact"
    assert result.verification is not None and result.verification.looks_right
    painted = _by_text(styled_doc, 0, "plain label")
    assert painted.style.font == "Times-Bold"
    assert painted.style.size == pytest.approx(20.0)
    assert painted.style.color == pytest.approx((1.0, 0.0, 0.0))
    # The target keeps its own position; only its look changed.
    assert painted.style.chars[0].origin == pytest.approx(label.style.chars[0].origin)
    # The source is untouched.
    assert _by_text(styled_doc, 0, "Heading").style.font == "Times-Bold"


@pytest.mark.feature("EDT-07")
def test_copy_style_works_across_pages(styled_doc: Document) -> None:
    heading = _by_text(styled_doc, 0, "Heading")
    other = _by_text(styled_doc, 1, "other page")

    CopyStyleOp(
        page_index=0,
        span_index=heading.style.span_index,
        target_page_index=1,
        target_span_index=other.style.span_index,
    ).apply(styled_doc)

    painted = _by_text(styled_doc, 1, "other page")
    assert painted.style.font == "Times-Bold"
    assert painted.style.size == pytest.approx(20.0)


@pytest.mark.feature("EDT-07")
def test_copy_style_on_standard_corpus_turns_regular_into_bold(corpus: Corpus, work_dir: Path) -> None:
    path = work_dir / "bold_italic.pdf"
    shutil.copy(corpus.bold_italic_standard, path)
    doc = Document.open(path)
    regular = _by_text(doc, 0, "Regular text")
    bold = _by_text(doc, 0, "Bold text")

    CopyStyleOp(
        page_index=0, span_index=bold.style.span_index, target_page_index=0, target_span_index=regular.style.span_index
    ).apply(doc)

    assert _by_text(doc, 0, "Regular text").style.font == "Helvetica-Bold"
    doc.close()


@pytest.mark.feature("EDT-07")
def test_copy_style_rejects_the_same_span_as_source_and_target(styled_doc: Document) -> None:
    with pytest.raises(OpValidationError, match="same span"):
        CopyStyleOp(page_index=0, span_index=0, target_page_index=0, target_span_index=0).apply(styled_doc)


@pytest.mark.feature("EDT-07")
def test_copy_style_rejects_an_out_of_range_span(styled_doc: Document) -> None:
    with pytest.raises(OpValidationError, match="out of range"):
        CopyStyleOp(page_index=0, span_index=0, target_page_index=0, target_span_index=99).apply(styled_doc)


@pytest.mark.feature("EDT-07")
def test_copy_style_op_round_trips_through_json() -> None:
    op = CopyStyleOp(page_index=0, span_index=1, target_page_index=2, target_span_index=3)
    assert parse_op(op.model_dump()) == op
