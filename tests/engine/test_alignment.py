"""Single-line alignment: a replacement keeps a right-aligned line's right
edge and a centered line's center (engine.fonts.blocks.detect_alignment),
a follow-up from the first real-document test (state/checkpoint.json)."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from engine.document import Document
from engine.fonts.blocks import detect_alignment
from engine.fonts.style import SpanTrace, extract_page_spans
from engine.ops.text import ReplaceTextOp

RIGHT_EDGE = 523.0
_HELV = pymupdf.Font("helv")


def _right_aligned(page: pymupdf.Page, text: str, y: float, size: float = 10) -> None:
    page.insert_text((RIGHT_EDGE - _HELV.text_length(text, fontsize=size), y), text, fontsize=size, fontname="helv")


def _centered(page: pymupdf.Page, text: str, y: float, size: float = 16) -> None:
    x = page.rect.width / 2 - _HELV.text_length(text, fontsize=size) / 2
    page.insert_text((x, y), text, fontsize=size, fontname="helv")


@pytest.fixture
def statement(work_dir: Path) -> Document:
    """Like the real statement: a right-aligned header block, a centered
    title, and left-aligned body text."""
    raw = pymupdf.open()
    page = raw.new_page()
    _right_aligned(page, "Statement date: 12/31/2025", 60)
    _right_aligned(page, "Account 0042-7781", 74)
    _centered(page, "Monthly Statement", 120)
    for i, line in enumerate(["Opening balance carried forward.", "Deposits and other credits."]):
        page.insert_text((72, 170 + i * 16), line, fontsize=11, fontname="helv")
    path = work_dir / "statement.pdf"
    raw.save(path)
    raw.close()
    return Document.open(path)


def _span(doc: Document, text: str) -> SpanTrace:
    return next(s for s in extract_page_spans(doc.raw, 0) if s.style.text == text)


def _alignment(doc: Document, text: str) -> str:
    return detect_alignment(_span(doc, text), extract_page_spans(doc.raw, 0), doc.raw[0].rect.width)


@pytest.mark.feature("EDT-02")
def test_detect_alignment_classifies_each_kind_of_line(statement: Document) -> None:
    assert _alignment(statement, "Statement date: 12/31/2025") == "right"
    assert _alignment(statement, "Account 0042-7781") == "right"
    assert _alignment(statement, "Monthly Statement") == "center"
    assert _alignment(statement, "Opening balance carried forward.") == "left"


@pytest.mark.feature("EDT-02")
def test_a_shorter_replacement_keeps_a_right_aligned_lines_right_edge(statement: Document) -> None:
    [result] = ReplaceTextOp(match="Statement date: 12/31/2025", replacement="Date: 1/1/26").apply(statement)
    assert "right alignment" in result.note
    assert _span(statement, "Date: 1/1/26").style.bbox[2] == pytest.approx(RIGHT_EDGE, abs=1.5)


@pytest.mark.feature("EDT-02")
def test_a_longer_replacement_keeps_a_centered_lines_center(statement: Document) -> None:
    center = statement.raw[0].rect.width / 2
    ReplaceTextOp(match="Monthly Statement", replacement="Quarterly Account Statement").apply(statement)
    x0, _y0, x1, _y1 = _span(statement, "Quarterly Account Statement").style.bbox
    assert (x0 + x1) / 2 == pytest.approx(center, abs=1.5)


@pytest.mark.feature("EDT-02")
def test_left_aligned_body_text_keeps_its_left_edge(statement: Document) -> None:
    before = _span(statement, "Deposits and other credits.").style.bbox[0]
    [result] = ReplaceTextOp(match="Deposits and other credits.", replacement="Deposits.").apply(statement)
    assert "alignment" not in result.note
    assert _span(statement, "Deposits.").style.bbox[0] == pytest.approx(before, abs=0.5)


@pytest.mark.feature("EDT-02")
def test_a_lone_line_at_the_right_margin_counts_as_right_aligned(work_dir: Path) -> None:
    raw = pymupdf.open()
    page = raw.new_page()
    page.insert_text((72, 200), "Body text starts at the left margin here.", fontsize=11, fontname="helv")
    _right_aligned(page, "Page 1 of 4", 800)
    path = work_dir / "footer.pdf"
    raw.save(path)
    doc = Document.open(path)
    ReplaceTextOp(match="Page 1 of 4", replacement="Page 12 of 40").apply(doc)
    assert _span(doc, "Page 12 of 40").style.bbox[2] == pytest.approx(RIGHT_EDGE, abs=1.5)
