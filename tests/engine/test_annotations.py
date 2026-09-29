"""P5 slice 4: annotations (ANN-01..06)."""

from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

import pymupdf
import pytest

from engine.document import Document
from engine.errors import OpValidationError, OverwriteRefusedError
from engine.ops.base import parse_op
from engine.ops.journal import UndoRedoJournal
from tests.corpus.build_corpus import ENCRYPTED_TEXT, Corpus


def _doc(tmp_path: Path) -> Document:
    doc = pymupdf.open()
    for n in (1, 2):
        page = doc.new_page()
        page.insert_text((72, 100), f"The quarterly total is due on page {n}.", fontsize=12)
        page.insert_text((72, 140), "Another total line.", fontsize=12)
    doc.save(tmp_path / "a.pdf")
    return Document.open(tmp_path / "a.pdf")


def _apply(doc: Document, data: dict[str, object]) -> object:
    op = parse_op(data)
    op.check_pages(doc)
    return op.apply(doc)


def _listed(doc: Document, page_index: int | None = None) -> list[dict[str, object]]:
    result = _apply(doc, {"op": "page_annotations", "page_index": page_index})
    return [item.model_dump() for item in result]  # type: ignore[attr-defined]


def _reopened(doc: Document, tmp_path: Path) -> pymupdf.Document:
    """Annotations survive a save: read them back from the written file, not the live object."""
    out = tmp_path / "saved.pdf"
    doc.save(out)
    return pymupdf.open(out)


# -- ANN-01 markup --


@pytest.mark.feature("ANN-01")
@pytest.mark.parametrize(
    ("kind", "pdf_type"),
    [("highlight", "Highlight"), ("underline", "Underline"), ("strikeout", "StrikeOut"), ("squiggly", "Squiggly")],
)
def test_mark_every_occurrence_of_text(tmp_path: Path, kind: str, pdf_type: str) -> None:
    doc = _doc(tmp_path)
    made = _apply(doc, {"op": "mark_text", "kind": kind, "match": "total", "author": "Kim", "note": "check"})
    assert len(made) == 4  # type: ignore[arg-type]  # two per page
    items = _listed(doc)
    assert {i["kind"] for i in items} == {pdf_type}
    assert {i["marked_text"] for i in items} == {"total"}
    assert items[0]["author"] == "Kim" and items[0]["contents"] == "check"
    with _reopened(doc, tmp_path) as saved:
        assert [a.type[1] for a in saved[1].annots()] == [pdf_type, pdf_type]


@pytest.mark.feature("ANN-01")
def test_mark_on_one_page_or_by_rectangle_and_refusals(tmp_path: Path) -> None:
    doc = _doc(tmp_path)
    assert len(_apply(doc, {"op": "mark_text", "match": "quarterly", "page_index": 1})) == 1  # type: ignore[arg-type]
    assert _listed(doc, 0) == []
    _apply(doc, {"op": "mark_text", "kind": "underline", "rects": [(70, 85, 200, 104)], "page_index": 0})
    assert "quarterly" in _listed(doc, 0)[0]["marked_text"]  # type: ignore[operator]
    with pytest.raises(OpValidationError, match="isn't in the document"):
        _apply(doc, {"op": "mark_text", "match": "nowhere"})
    with pytest.raises(OpValidationError, match="off the page"):
        _apply(doc, {"op": "mark_text", "rects": [(0, 0, 900, 900)], "page_index": 0})


# -- ANN-02 notes and comments --


@pytest.mark.feature("ANN-02")
def test_sticky_note_and_on_page_comment(tmp_path: Path) -> None:
    doc = _doc(tmp_path)
    note = _apply(
        doc, {"op": "add_note", "page_index": 0, "point": (400, 90), "text": "Is this right?", "author": "Kim"}
    )
    comment = _apply(
        doc, {"op": "add_comment", "page_index": 0, "rect": (300, 300, 500, 360), "text": "Approved in review"}
    )
    assert note.kind == "Text" and comment.kind == "FreeText"  # type: ignore[attr-defined]
    with _reopened(doc, tmp_path) as saved:
        annots = {a.type[1]: a.info for a in saved[0].annots()}
        assert annots["Text"]["content"] == "Is this right?" and annots["Text"]["title"] == "Kim"
        assert "Approved in review" in saved[0].get_text() or annots["FreeText"]["content"] == "Approved in review"
    with pytest.raises(OpValidationError, match="needs some text"):
        _apply(doc, {"op": "add_note", "page_index": 0, "point": (10, 10), "text": " "})
    with pytest.raises(OpValidationError, match="unknown note icon"):
        _apply(doc, {"op": "add_note", "page_index": 0, "point": (10, 10), "text": "x", "icon": "Smiley"})


# -- ANN-03 shapes --


@pytest.mark.feature("ANN-03")
def test_every_shape_kind(tmp_path: Path) -> None:
    doc = _doc(tmp_path)
    specs: list[dict[str, object]] = [
        {"kind": "rectangle", "rect": (50, 200, 150, 260), "fill": "blue", "opacity": 0.5},
        {"kind": "ellipse", "rect": (200, 200, 300, 260)},
        {"kind": "line", "points": [(50, 300), (200, 300)]},
        {"kind": "arrow", "points": [(50, 330), (200, 380)], "width": 3},
        {"kind": "polyline", "points": [(50, 400), (100, 450), (150, 400)]},
        {"kind": "polygon", "points": [(200, 400), (250, 450), (300, 400)]},
        {"kind": "freehand", "strokes": [[(50, 500), (60, 520), (80, 510)], [(100, 500), (120, 530)]]},
    ]
    for spec in specs:
        _apply(doc, {"op": "add_annotation_shape", "page_index": 0, "color": "green", **spec})
    kinds = [i["kind"] for i in _listed(doc, 0)]
    assert kinds == ["Square", "Circle", "Line", "Line", "PolyLine", "Polygon", "Ink"]
    with _reopened(doc, tmp_path) as saved:
        annots = [(a.line_ends, a.opacity, a.colors) for a in saved[0].annots()]
        assert annots[3][0][1] == pymupdf.PDF_ANNOT_LE_OPEN_ARROW  # type: ignore[attr-defined]
        assert annots[0][1] == pytest.approx(0.5) and annots[0][2]["fill"] == pytest.approx((0.0, 0.2, 0.8))
        assert annots[1][2]["stroke"] == pytest.approx((0.0, 0.5, 0.0))


@pytest.mark.feature("ANN-03")
def test_shapes_refuse_bad_geometry(tmp_path: Path) -> None:
    doc = _doc(tmp_path)
    with pytest.raises(OpValidationError, match="exactly two points"):
        _apply(doc, {"op": "add_annotation_shape", "page_index": 0, "kind": "arrow", "points": [(1, 1)]})
    with pytest.raises(OpValidationError, match="on the page"):
        _apply(doc, {"op": "add_annotation_shape", "page_index": 0, "kind": "line", "points": [(1, 1), (5000, 1)]})
    with pytest.raises(OpValidationError, match="needs a rect"):
        _apply(doc, {"op": "add_annotation_shape", "page_index": 0, "kind": "ellipse"})


# -- ANN-04 flatten --


@pytest.mark.feature("ANN-04")
def test_flatten_draws_annotations_into_the_page(tmp_path: Path) -> None:
    doc = _doc(tmp_path)
    _apply(
        doc,
        {
            "op": "add_annotation_shape",
            "page_index": 0,
            "kind": "rectangle",
            "rect": (50, 200, 150, 260),
            "fill": "red",
        },
    )
    _apply(doc, {"op": "mark_text", "match": "quarterly"})
    before = doc.raw[0].get_pixmap(dpi=36).samples
    assert _apply(doc, {"op": "flatten_annotations"}) == 3
    assert _listed(doc) == []
    after = doc.raw[0].get_pixmap(dpi=36).samples
    assert sum(abs(a - b) > 8 for a, b in zip(before, after, strict=True)) == 0  # looks the same, now part of the page
    assert _apply(doc, {"op": "flatten_annotations"}) == 0


# -- ANN-05 summary --


@pytest.mark.feature("ANN-05")
def test_summary_in_three_formats(tmp_path: Path) -> None:
    doc = _doc(tmp_path)
    _apply(doc, {"op": "mark_text", "match": "quarterly", "page_index": 0, "author": "Kim", "note": "confirm figure"})
    _apply(doc, {"op": "add_note", "page_index": 1, "point": (400, 90), "text": "Page two question"})
    assert _apply(doc, {"op": "annotation_summary", "out": str(tmp_path / "s.md")}) == 2
    markdown = (tmp_path / "s.md").read_text(encoding="utf-8")
    assert "**Page 1, Highlight** by Kim" in markdown and "> quarterly" in markdown and "Page two question" in markdown
    _apply(doc, {"op": "annotation_summary", "out": str(tmp_path / "s.csv"), "format": "csv"})
    rows = list(csv.DictReader((tmp_path / "s.csv").open(encoding="utf-8")))
    assert [(r["page"], r["kind"]) for r in rows] == [("1", "Highlight"), ("2", "Text")]
    _apply(doc, {"op": "annotation_summary", "out": str(tmp_path / "s.json"), "format": "json"})
    assert json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))[0]["contents"] == "confirm figure"
    with pytest.raises(OverwriteRefusedError):
        _apply(doc, {"op": "annotation_summary", "out": str(tmp_path / "s.md")})


# -- ANN-06 edit and delete --


@pytest.mark.feature("ANN-06")
def test_edit_and_delete_an_existing_annotation(tmp_path: Path) -> None:
    doc = _doc(tmp_path)
    note = _apply(doc, {"op": "add_note", "page_index": 0, "point": (400, 90), "text": "old"})
    box = _apply(doc, {"op": "add_annotation_shape", "page_index": 0, "kind": "rectangle", "rect": (50, 200, 150, 260)})
    xref = box.xref  # type: ignore[attr-defined]
    _apply(
        doc,
        {
            "op": "update_annotation",
            "page_index": 0,
            "xref": xref,
            "contents": "moved",
            "color": "blue",
            "opacity": 0.4,
            "rect": (300, 500, 400, 560),
        },
    )
    edited = next(i for i in _listed(doc, 0) if i["xref"] == xref)
    assert edited["contents"] == "moved" and edited["color"] == pytest.approx((0.0, 0.2, 0.8))
    assert edited["opacity"] == pytest.approx(0.4) and edited["rect"][0] == pytest.approx(300, abs=3)  # type: ignore[index]
    _apply(doc, {"op": "delete_annotation", "page_index": 0, "xref": note.xref})  # type: ignore[attr-defined]
    assert [i["xref"] for i in _listed(doc, 0)] == [xref]
    with pytest.raises(OpValidationError, match="no annotation"):
        _apply(doc, {"op": "delete_annotation", "page_index": 0, "xref": note.xref})  # type: ignore[attr-defined]


@pytest.mark.feature("ANN-06")
def test_existing_annotations_from_another_tool_are_listed_and_editable(corpus: Corpus, tmp_path: Path) -> None:
    source = pymupdf.open()
    page = source.new_page()
    page.insert_text((72, 100), "Signed off")
    annot = page.add_text_annot((300, 300), "made elsewhere")
    annot.set_info(title="Reviewer")
    annot.update()
    page.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(72, 90, 150, 105), "uri": "https://example.com"})
    source.save(tmp_path / "other.pdf")
    doc = Document.open(tmp_path / "other.pdf")
    (item,) = _listed(doc)  # the link is not an annotation here
    assert (item["kind"], item["author"], item["contents"]) == ("Text", "Reviewer", "made elsewhere")
    _apply(doc, {"op": "update_annotation", "page_index": 0, "xref": item["xref"], "contents": "answered"})
    with _reopened(doc, tmp_path) as saved:
        assert next(saved[0].annots()).info["content"] == "answered"
        assert saved[0].get_links()  # link untouched


# -- journaling and encryption --


@pytest.mark.feature("ANN-01")
def test_annotations_undo_and_keep_encryption(corpus: Corpus, tmp_path: Path) -> None:
    source = tmp_path / "enc.pdf"
    shutil.copy(corpus.encrypted_aes_256, source)
    journal = UndoRedoJournal(Document.open(source, password=corpus.user_password))
    word = ENCRYPTED_TEXT.split()[0]
    journal.record(parse_op({"op": "mark_text", "match": word, "page_index": 0}))
    journal.document.save(tmp_path / "out.pdf")
    with pymupdf.open(tmp_path / "out.pdf") as saved:
        assert saved.needs_pass and saved.authenticate(corpus.user_password)
        assert [a.type[1] for a in saved[0].annots()] == ["Highlight"]
    journal.undo()
    assert not list(journal.document.raw[0].annots())
    journal.document.close()


# -- command bar --


@pytest.mark.feature("ANN-01")
@pytest.mark.feature("ANN-02")
@pytest.mark.feature("ANN-04")
@pytest.mark.feature("ANN-05")
def test_annotation_commands_plan_and_run(tmp_path: Path) -> None:
    from engine.commands import CommandError, parse_command

    def ops(text: str) -> list[dict[str, object]]:
        return parse_command(text, page_count=2).ops

    assert ops('highlight "total" on page 2 note "check"') == [
        {"op": "mark_text", "kind": "highlight", "match": "total", "note": "check", "page_index": 1}
    ]
    assert ops('strike out "Another"')[0]["kind"] == "strikeout"
    assert ops('note "Why?" at 400, 90 on page 1') == [
        {"op": "add_note", "page_index": 0, "point": [400.0, 90.0], "text": "Why?"}
    ]
    assert ops("flatten annotations") == [{"op": "flatten_annotations"}]
    with pytest.raises(CommandError, match="planned for P6"):
        ops("flatten form fields")
    doc = _doc(tmp_path)
    out = tmp_path / "notes.csv"
    for command in [
        'underline "total"',
        'note "Why?" at 400, 90 on page 1',
        f'export annotations to "{out.as_posix()}"',
    ]:
        for op in parse_command(command, page_count=2).ops:
            _apply(doc, op)
    assert out.read_text(encoding="utf-8").count("\n") == 6  # header + 4 underlines + 1 note
