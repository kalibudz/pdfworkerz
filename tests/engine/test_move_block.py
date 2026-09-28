"""EDT-05: move and resize text blocks (MoveTextBlockOp)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from engine.document import Document
from engine.errors import OpValidationError
from engine.fonts.style import extract_page_spans
from engine.ops.base import parse_op
from engine.ops.text import MoveTextBlockOp
from tests.corpus.build_corpus import Corpus

PARAGRAPH = ["This is line one of a paragraph.", "This is line two continuing on.", "And this is line three, the last."]
SEPARATE = "A separate paragraph starts here."


@pytest.fixture
def doc(corpus: Corpus, work_dir: Path) -> Document:
    path = work_dir / "paragraph.pdf"
    shutil.copy(corpus.paragraph, path)
    return Document.open(path)


def _origins(doc: Document) -> dict[str, tuple[float, float]]:
    return {span.style.text: span.style.chars[0].origin for span in extract_page_spans(doc.raw, 0)}


@pytest.mark.feature("EDT-05")
def test_move_shifts_every_line_of_the_block_and_nothing_else(doc: Document) -> None:
    before = _origins(doc)
    results = MoveTextBlockOp(page_index=0, span_index=1, dx=15, dy=300).apply(doc)

    assert len(results) == 3
    assert results[-1].verification is not None and results[-1].verification.looks_right
    after = _origins(doc)
    for line in PARAGRAPH:
        assert after[line] == pytest.approx((before[line][0] + 15, before[line][1] + 300), abs=0.5)
    assert after[SEPARATE] == pytest.approx(before[SEPARATE])


@pytest.mark.feature("EDT-05")
def test_a_short_move_that_overlaps_the_old_position_keeps_all_text(doc: Document) -> None:
    """Every old line is redacted before any new one is drawn -- redacting
    line by line would erase the freshly drawn line above it here."""
    MoveTextBlockOp(page_index=0, span_index=0, dy=5).apply(doc)
    texts = [span.style.text for span in extract_page_spans(doc.raw, 0)]
    for line in PARAGRAPH:
        assert line in texts


@pytest.mark.feature("EDT-05")
def test_resize_narrower_rewraps_onto_more_lines_at_the_same_spacing(doc: Document) -> None:
    before = _origins(doc)
    pitch = before[PARAGRAPH[1]][1] - before[PARAGRAPH[0]][1]

    results = MoveTextBlockOp(page_index=0, span_index=0, width=150).apply(doc)

    assert len(results) > 3
    lines = [span for span in extract_page_spans(doc.raw, 0) if span.style.text != SEPARATE]
    assert " ".join(span.style.text for span in lines) == " ".join(PARAGRAPH)
    assert all(span.style.bbox[2] - span.style.bbox[0] <= 150 + 0.5 for span in lines)
    baselines = [span.style.chars[0].origin[1] for span in lines]
    assert baselines[1] - baselines[0] == pytest.approx(pitch, abs=0.5)


@pytest.mark.feature("EDT-05")
def test_resize_wider_joins_lines(doc: Document) -> None:
    MoveTextBlockOp(page_index=0, span_index=0, width=500).apply(doc)
    texts = [span.style.text for span in extract_page_spans(doc.raw, 0)]
    assert len(texts) < 4  # three lines became fewer, plus the separate paragraph


@pytest.mark.feature("EDT-05")
def test_a_moved_block_can_be_found_and_resized_again(doc: Document) -> None:
    """After an edit, the redrawn lines sit at the end of the content stream,
    so the block is only consecutive in visual order -- _block_at tries both."""
    MoveTextBlockOp(page_index=0, span_index=0, dy=300).apply(doc)
    moved_index = next(s.style.span_index for s in extract_page_spans(doc.raw, 0) if s.style.text == PARAGRAPH[2])
    results = MoveTextBlockOp(page_index=0, span_index=moved_index, width=150).apply(doc)
    assert len(results) > 3


@pytest.mark.feature("EDT-05")
@pytest.mark.parametrize(
    ("op", "message"),
    [
        (MoveTextBlockOp(page_index=0, span_index=0), "nothing to do"),
        (MoveTextBlockOp(page_index=0, span_index=0, width=-5), "positive"),
        (MoveTextBlockOp(page_index=0, span_index=0, dy=5000), "off the page"),
        (MoveTextBlockOp(page_index=0, span_index=0, dx=-500), "off the page"),
        (MoveTextBlockOp(page_index=0, span_index=42, dy=5), "out of range"),
    ],
)
def test_invalid_moves_are_rejected_without_changing_the_page(doc: Document, op: MoveTextBlockOp, message: str) -> None:
    before = _origins(doc)
    with pytest.raises(OpValidationError, match=message):
        op.apply(doc)
    assert _origins(doc) == before


@pytest.mark.feature("EDT-05")
def test_move_text_block_op_round_trips_through_json() -> None:
    op = MoveTextBlockOp(page_index=0, span_index=2, dx=1.5, dy=-3, width=200)
    assert parse_op(op.model_dump()) == op
