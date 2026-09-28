"""EDT-09: draw and edit vector shapes and lines."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from engine.document import Document
from engine.errors import OpValidationError
from engine.fonts.style import extract_page_spans
from engine.ops.base import parse_op
from engine.ops.journal import UndoRedoJournal
from engine.ops.shapes import DeleteShapeOp, DrawShapeOp, EditShapeOp, PageShapesOp


@pytest.fixture
def doc(work_dir: Path) -> Document:
    """A big blue box with a small filled red circle *inside* it, a dashed
    diagonal line elsewhere, and text inside the box that must survive."""
    raw = pymupdf.open()
    page = raw.new_page()
    shape = page.new_shape()
    shape.draw_rect(pymupdf.Rect(50, 50, 300, 300))
    shape.finish(color=(0, 0, 1), width=2)
    shape.draw_oval(pymupdf.Rect(100, 100, 200, 200))
    shape.finish(color=(1, 0, 0), fill=(1, 1, 0), width=1)
    shape.draw_line((50, 400), (300, 450))
    shape.finish(color=(0, 0, 0), width=3, dashes="[4 2] 0", closePath=False)
    shape.commit()
    page.insert_text((60, 80), "Text inside the box", fontsize=11)
    path = work_dir / "shapes.pdf"
    raw.save(path)
    raw.close()
    return Document.open(path)


def _kinds(doc: Document) -> list[str]:
    return [shape.kind for shape in PageShapesOp(page_index=0).apply(doc)]


def _pixel(doc: Document, x: float, y: float) -> tuple[int, ...]:
    return tuple(doc.raw[0].get_pixmap(dpi=72).pixel(int(x), int(y)))


@pytest.mark.feature("EDT-09")
def test_list_reports_each_path_with_its_style(doc: Document) -> None:
    shapes = PageShapesOp(page_index=0).apply(doc)
    assert [s.kind for s in shapes] == ["rect", "curve", "line"]
    box, circle, line = shapes
    assert box.stroke_color == (0.0, 0.0, 1.0) and box.fill_color is None and box.line_width == 2
    assert circle.fill_color == (1.0, 1.0, 0.0)
    assert line.line_width == 3


@pytest.mark.feature("EDT-09")
@pytest.mark.parametrize(
    ("kind", "points", "expected"),
    [
        ("line", [(10, 500), (200, 520)], "line"),
        ("rect", [(350, 350), (450, 420)], "rect"),
        ("ellipse", [(350, 350), (450, 420)], "curve"),
        ("polyline", [(10, 600), (60, 650), (110, 600)], "line"),
        ("polygon", [(10, 600), (60, 650), (110, 600)], "line"),
    ],
)
def test_draw_each_kind_of_shape(doc: Document, kind: str, points: list[tuple[float, float]], expected: str) -> None:
    drawn = DrawShapeOp(page_index=0, kind=kind, points=points, fill_color=(0, 1, 0)).apply(doc)  # type: ignore[arg-type]
    assert drawn.kind == expected
    reopened = Document.from_bytes(doc.to_bytes())
    assert len(PageShapesOp(page_index=0).apply(reopened)) == 4


@pytest.mark.feature("EDT-09")
def test_a_drawn_filled_rect_really_paints(doc: Document) -> None:
    DrawShapeOp(
        page_index=0, kind="rect", points=[(350, 350), (450, 420)], stroke_color=None, fill_color=(0, 1, 0)
    ).apply(doc)
    assert _pixel(doc, 400, 385) == (0, 255, 0)


@pytest.mark.feature("EDT-09")
def test_deleting_the_outer_box_keeps_the_circle_inside_it_and_the_text(doc: Document) -> None:
    """MuPDF's line-art redaction removes every path inside the area -- the
    circle goes with the box and has to be drawn back."""
    DeleteShapeOp(page_index=0, index=0).apply(doc)
    shapes = PageShapesOp(page_index=0).apply(doc)
    assert sorted(s.kind for s in shapes) == ["curve", "line"]
    circle = next(s for s in shapes if s.kind == "curve")
    assert circle.fill_color == (1.0, 1.0, 0.0)
    assert [round(v) for v in circle.rect] == [100, 100, 200, 200]
    assert "Text inside the box" in "".join(s.style.text for s in extract_page_spans(doc.raw, 0))


@pytest.mark.feature("EDT-09")
def test_deleting_the_inner_circle_keeps_the_box(doc: Document) -> None:
    DeleteShapeOp(page_index=0, index=1).apply(doc)
    assert sorted(_kinds(doc)) == ["line", "rect"]
    assert _pixel(doc, 150, 150) == (255, 255, 255)


@pytest.mark.feature("EDT-09")
def test_edit_moves_and_resizes_a_shape(doc: Document) -> None:
    moved = EditShapeOp(page_index=0, index=1, rect=(350, 500, 550, 600)).apply(doc)
    assert [round(v) for v in moved.rect] == [350, 500, 550, 600]
    assert moved.fill_color == (1.0, 1.0, 0.0)  # style kept
    assert len(_kinds(doc)) == 3


@pytest.mark.feature("EDT-09")
def test_edit_restyles_a_shape_and_can_remove_its_fill(doc: Document) -> None:
    edited = EditShapeOp(page_index=0, index=1, stroke_color=(0, 1, 0), line_width=4, no_fill=True).apply(doc)
    assert edited.stroke_color == (0.0, 1.0, 0.0)
    assert edited.line_width == 4
    assert edited.fill_color is None


@pytest.mark.feature("EDT-09")
def test_edit_keeps_a_lines_dash_pattern(doc: Document) -> None:
    EditShapeOp(page_index=0, index=2, rect=(50, 500, 300, 550)).apply(doc)
    line = next(p for p in doc.raw[0].get_drawings() if round(p["rect"].y0) == 500)
    assert "4" in line["dashes"]


@pytest.mark.feature("EDT-09")
@pytest.mark.parametrize(
    ("op", "message"),
    [
        (DrawShapeOp(page_index=0, kind="line", points=[(1, 1)]), "exactly 2"),
        (DrawShapeOp(page_index=0, kind="polygon", points=[(1, 1), (2, 2)]), "at least 3"),
        (DrawShapeOp(page_index=0, kind="rect", points=[(5, 5), (5, 50)]), "non-empty"),
        (DrawShapeOp(page_index=0, kind="rect", points=[(1, 1), (9, 9)], stroke_color=None), "stroke_color, a fill"),
        (DrawShapeOp(page_index=0, kind="line", points=[(1, 1), (9, 9)], line_width=0), "positive"),
        (DrawShapeOp(page_index=0, kind="line", points=[(1, 1), (9, 9)], stroke_color=(2, 0, 0)), "between 0 and 1"),
        (DrawShapeOp(page_index=0, kind="line", points=[(5000, 5000), (5100, 5100)]), "outside the page"),
        (EditShapeOp(page_index=0, index=0), "nothing to change"),
        (EditShapeOp(page_index=0, index=7, line_width=2), "out of range"),
        (DeleteShapeOp(page_index=4, index=0), "out of range"),
    ],
)
def test_invalid_shape_ops_are_rejected(doc: Document, op: DrawShapeOp, message: str) -> None:
    before = _kinds(doc)
    with pytest.raises(OpValidationError, match=message):
        op.apply(doc)
    assert _kinds(doc) == before


@pytest.mark.feature("EDT-09")
def test_shape_edits_are_undoable_through_the_journal(doc: Document) -> None:
    journal = UndoRedoJournal(doc)
    journal.record(DeleteShapeOp(page_index=0, index=0))
    assert len(PageShapesOp(page_index=0).apply(journal.document)) == 2
    journal.undo()
    assert [s.kind for s in PageShapesOp(page_index=0).apply(journal.document)] == ["rect", "curve", "line"]


@pytest.mark.feature("EDT-09")
def test_shape_ops_round_trip_through_json() -> None:
    for op in (
        DrawShapeOp(page_index=0, kind="ellipse", points=[(1, 2), (3, 4)], fill_color=(1, 0, 0), dashed=True),
        EditShapeOp(page_index=0, index=1, rect=(1, 2, 3, 4), no_fill=True),
        DeleteShapeOp(page_index=0, index=2),
        PageShapesOp(page_index=1),
    ):
        assert parse_op(op.model_dump()) == op
