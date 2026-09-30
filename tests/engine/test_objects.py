"""EDT-13..EDT-15: moving, copying and deleting several objects as one Op.

Each page is built by the test: two text blocks, an image and a shape.
"""

from __future__ import annotations

import io

import pymupdf
import pytest
from PIL import Image

from engine.document import Document
from engine.fonts.style import extract_page_spans
from engine.images import list_images
from engine.ops.base import parse_op
from engine.ops.journal import UndoRedoJournal
from engine.shapes import list_shapes


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (20, 10), (200, 30, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


def _journal(pages: int = 1) -> UndoRedoJournal:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "First heading", fontname="helv", fontsize=12)
    page.insert_text((72, 200), "Second heading", fontname="helv", fontsize=12)
    page.insert_image(pymupdf.Rect(300, 300, 400, 350), stream=_png())
    shape = page.new_shape()
    shape.draw_rect(pymupdf.Rect(100, 400, 200, 450))
    shape.finish(color=(0, 0, 1), width=2)
    shape.commit()
    for _ in range(pages - 1):
        doc.new_page()
    return UndoRedoJournal(Document.from_bytes(doc.tobytes()))


def _text_x(journal: UndoRedoJournal, text: str, page: int = 0) -> list[float]:
    return [s.style.bbox[0] for s in extract_page_spans(journal.document.raw, page) if s.style.text == text]


def _span(journal: UndoRedoJournal, text: str) -> int:
    return next(i for i, s in enumerate(extract_page_spans(journal.document.raw, 0)) if s.style.text == text)


def _all(journal: UndoRedoJournal) -> list[dict]:
    return [
        {"kind": "text", "page_index": 0, "index": _span(journal, "First heading")},
        {"kind": "text", "page_index": 0, "index": _span(journal, "Second heading")},
        {"kind": "image", "page_index": 0, "index": 0},
        {"kind": "shape", "page_index": 0, "index": 0},
    ]


@pytest.mark.feature("EDT-13")
def test_moving_several_objects_is_one_undo_and_survives_renumbering() -> None:
    journal = _journal()
    image_before = list_images(journal.document, 0)[0].rect
    shape_before = list_shapes(journal.document, 0)[0].rect
    journal.record(parse_op({"op": "move_objects", "items": _all(journal), "dx": 30, "dy": 5}))
    assert len(journal.history) == 1
    assert _text_x(journal, "First heading")[0] == pytest.approx(102, abs=0.5)
    assert _text_x(journal, "Second heading")[0] == pytest.approx(102, abs=0.5)
    assert list_images(journal.document, 0)[0].rect[0] == pytest.approx(image_before[0] + 30, abs=0.5)
    assert list_shapes(journal.document, 0)[0].rect[0] == pytest.approx(shape_before[0] + 30, abs=1.5)

    journal.undo()
    assert _text_x(journal, "First heading")[0] == pytest.approx(72, abs=0.5)
    assert list_images(journal.document, 0)[0].rect == pytest.approx(image_before, abs=0.5)


@pytest.mark.feature("EDT-13")
def test_each_item_can_move_by_its_own_offset() -> None:
    """What align-left sends: every object to the same left edge."""
    journal = _journal()
    items = [
        {"kind": "text", "page_index": 0, "index": _span(journal, "First heading"), "dx": 28},
        {"kind": "image", "page_index": 0, "index": 0, "dx": -200},
    ]
    journal.record(parse_op({"op": "move_objects", "items": items}))
    assert _text_x(journal, "First heading")[0] == pytest.approx(100, abs=0.5)
    assert list_images(journal.document, 0)[0].rect[0] == pytest.approx(100, abs=0.5)


@pytest.mark.feature("EDT-15")
def test_duplicating_copies_everything_and_leaves_the_originals() -> None:
    journal = _journal()
    results = journal.record(parse_op({"op": "duplicate_objects", "items": _all(journal), "dx": 10, "dy": 10}))
    assert len(journal.history) == 1
    assert sorted(_text_x(journal, "First heading")) == pytest.approx([72, 82], abs=0.5)
    assert len(list_images(journal.document, 0)) == 2
    assert len(list_shapes(journal.document, 0)) == 2
    texts = [r for r in results if hasattr(r, "tier")]
    assert all(r.tier == "exact" for r in texts)
    assert all(r.verification.looks_right for r in texts if r.verification)  # an offset copy may overlap its source

    # The copy is itself a normal text block: it can be moved.
    copy = next(
        i
        for i, s in enumerate(extract_page_spans(journal.document.raw, 0))
        if s.style.text == "First heading" and s.style.bbox[0] > 80
    )
    journal.record(
        parse_op({"op": "move_objects", "items": [{"kind": "text", "page_index": 0, "index": copy}], "dy": 30})
    )


@pytest.mark.feature("EDT-15")
def test_duplicating_to_another_page() -> None:
    journal = _journal(pages=2)
    journal.record(
        parse_op({"op": "duplicate_objects", "items": _all(journal), "dx": 0, "dy": 0, "target_page_index": 1})
    )
    assert _text_x(journal, "First heading", page=1) == pytest.approx([72], abs=0.5)
    assert len(list_images(journal.document, 1)) == 1
    assert len(list_shapes(journal.document, 1)) == 1
    assert len(list_images(journal.document, 0)) == 1  # the source page is untouched


@pytest.mark.feature("EDT-15")
def test_deleting_removes_exactly_the_selected_objects() -> None:
    journal = _journal()
    items = [
        {"kind": "text", "page_index": 0, "index": _span(journal, "Second heading")},
        {"kind": "image", "page_index": 0, "index": 0},
        {"kind": "shape", "page_index": 0, "index": 0},
    ]
    journal.record(parse_op({"op": "delete_objects", "items": items}))
    assert [s.style.text for s in extract_page_spans(journal.document.raw, 0)] == ["First heading"]
    assert list_images(journal.document, 0) == []
    assert list_shapes(journal.document, 0) == []
    journal.undo()
    assert len(extract_page_spans(journal.document.raw, 0)) == 2


@pytest.mark.feature("EDT-13")
def test_page_blocks_group_a_paragraphs_lines() -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    for i, line in enumerate(["One line of a paragraph", "and its second line", "and a third."]):
        page.insert_text((72, 100 + i * 14), line, fontname="helv", fontsize=11)
    page.insert_text((72, 400), "A separate note", fontname="helv", fontsize=11)
    journal = UndoRedoJournal(Document.from_bytes(doc.tobytes()))
    blocks = journal.document and parse_op({"op": "page_blocks", "page_index": 0}).apply(journal.document)
    assert [len(b["span_indices"]) for b in blocks] == [3, 1]
    assert blocks[0]["bbox"][1] == pytest.approx(100 - 11, abs=3)


def test_empty_selection_is_refused() -> None:
    with pytest.raises(Exception, match="at least one"):
        parse_op({"op": "move_objects", "items": [], "dx": 1})
