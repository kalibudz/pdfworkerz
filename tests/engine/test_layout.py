"""P5 slice 2: page geometry (ORG-09..12)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pymupdf
import pytest

from engine.document import Document
from engine.errors import OpValidationError
from engine.fonts.style import extract_page_spans
from engine.layout import booklet_order
from engine.ops.base import parse_op
from engine.ops.journal import UndoRedoJournal
from tests.corpus.build_corpus import ENCRYPTED_TEXT, Corpus


def _numbered(path: Path, count: int, size: tuple[float, float] = (595, 842)) -> Path:
    doc = pymupdf.open()
    for n in range(1, count + 1):
        page = doc.new_page(width=size[0], height=size[1])
        page.insert_text((72, 72), f"Page {n}", fontsize=14)
        page.insert_text((72, size[1] - 50), f"footer {n}", fontsize=9)
    doc.save(path)
    return path


def _apply(doc: Document, data: dict[str, object]) -> object:
    op = parse_op(data)
    op.check_pages(doc)
    return op.apply(doc)


# -- ORG-09 crop --


@pytest.mark.feature("ORG-09")
def test_crop_to_a_box_hides_the_rest_without_removing_it(tmp_path: Path) -> None:
    doc = Document.open(_numbered(tmp_path / "p.pdf", 2))
    _apply(doc, {"op": "crop_pages", "page_indices": [0], "box": (0, 0, 595, 400)})
    page = doc.raw[0]
    assert (page.rect.width, page.rect.height) == (595, 400)
    assert "footer 1" not in page.get_text() and "Page 1" in page.get_text()
    _apply(doc, {"op": "uncrop_pages", "page_indices": [0]})
    assert "footer 1" in doc.raw[0].get_text()  # it was only hidden
    assert doc.raw[1].rect.height == 842  # other pages untouched
    doc.close()


@pytest.mark.feature("ORG-09")
def test_crop_by_margins_and_refuse_cropping_to_nothing(tmp_path: Path) -> None:
    doc = Document.open(_numbered(tmp_path / "p.pdf", 1))
    _apply(doc, {"op": "crop_pages", "page_indices": [0], "margins": (36, 36, 36, 36)})
    assert (doc.raw[0].rect.width, doc.raw[0].rect.height) == (523, 770)
    with pytest.raises(OpValidationError, match="less than 10 points"):
        _apply(doc, {"op": "crop_pages", "page_indices": [0], "margins": (300, 0, 300, 0)})
    doc.close()


# -- ORG-10 resize --


@pytest.mark.feature("ORG-10")
def test_resize_scales_content_to_the_new_size(tmp_path: Path) -> None:
    doc = Document.open(_numbered(tmp_path / "p.pdf", 2))
    _apply(doc, {"op": "resize_pages", "page_indices": [1], "size": "letter"})
    assert (doc.raw[1].rect.width, doc.raw[1].rect.height) == (612, 792)
    assert "Page 2" in doc.raw[1].get_text() and "footer 2" in doc.raw[1].get_text()
    assert (doc.raw[0].rect.width, doc.raw[0].rect.height) == (595, 842)
    assert doc.raw[0].get_text().startswith("Page 1")
    doc.close()


@pytest.mark.feature("ORG-10")
def test_resized_text_is_still_editable(tmp_path: Path) -> None:
    from engine.ops.text import ReplaceTextOp

    doc = Document.open(_numbered(tmp_path / "p.pdf", 1))
    _apply(doc, {"op": "resize_pages", "page_indices": [0], "size": "a5"})
    ReplaceTextOp(match="Page 1", replacement="Sheet 1", require_tier="fallback").apply(doc)
    assert "Sheet 1" in [s.style.text for s in extract_page_spans(doc.raw, 0)]
    doc.close()


@pytest.mark.feature("ORG-10")
def test_resize_without_scaling_keeps_the_content_size(tmp_path: Path) -> None:
    doc = Document.open(_numbered(tmp_path / "p.pdf", 1))
    before = [s.style.size for s in extract_page_spans(doc.raw, 0)]
    _apply(doc, {"op": "resize_pages", "page_indices": [0], "size": [700, 950], "scale": False})
    after = [s.style.size for s in extract_page_spans(doc.raw, 0)]
    assert after == pytest.approx(before)
    with pytest.raises(OpValidationError, match="unknown page size"):
        _apply(doc, {"op": "resize_pages", "page_indices": [0], "size": "b7"})
    doc.close()


# -- ORG-11 N-up --


@pytest.mark.feature("ORG-11")
def test_two_up_puts_pages_side_by_side_in_order(tmp_path: Path) -> None:
    doc = Document.open(_numbered(tmp_path / "p.pdf", 5))
    assert _apply(doc, {"op": "n_up", "cols": 2, "rows": 1}) == 3
    assert doc.page_count == 3
    first = doc.raw[0]
    assert first.rect.width > first.rect.height  # landscape sheet
    words = {w[4]: w[0] for w in first.get_text("words")}
    assert words["1"] < words["2"]  # page 1 left of page 2
    assert "Page 5" in doc.raw[2].get_text()
    doc.close()


@pytest.mark.feature("ORG-11")
def test_four_up_and_bounds(tmp_path: Path) -> None:
    doc = Document.open(_numbered(tmp_path / "p.pdf", 8))
    assert _apply(doc, {"op": "n_up", "cols": 2, "rows": 2, "sheet": "a4"}) == 2
    with pytest.raises(OpValidationError, match="2 to 64"):
        _apply(doc, {"op": "n_up", "cols": 1, "rows": 1})
    doc.close()


# -- ORG-12 booklet --


@pytest.mark.feature("ORG-12")
def test_booklet_order_is_saddle_stitch() -> None:
    assert booklet_order(8) == [7, 0, 1, 6, 5, 2, 3, 4]
    assert booklet_order(6) == [None, 0, 1, None, 5, 2, 3, 4]  # padded to 8 with blanks


@pytest.mark.feature("ORG-12")
def test_booklet_imposes_pages_for_folding(tmp_path: Path) -> None:
    doc = Document.open(_numbered(tmp_path / "p.pdf", 8))
    assert _apply(doc, {"op": "booklet"}) == 4
    sheet = doc.raw[0]
    words = {w[4]: w[0] for w in sheet.get_text("words") if w[4].isdigit()}
    assert words["8"] < words["1"]  # front of the outer sheet: page 8 left, page 1 right
    doc.close()


# -- journaling and encryption --


@pytest.mark.feature("ORG-11")
def test_imposition_undoes_and_keeps_encryption(corpus: Corpus, tmp_path: Path) -> None:
    source = tmp_path / "enc.pdf"
    shutil.copy(corpus.encrypted_aes_256, source)
    journal = UndoRedoJournal(Document.open(source, password=corpus.user_password))
    journal.record(parse_op({"op": "n_up", "cols": 2, "rows": 1}))
    journal.document.save(tmp_path / "out.pdf")
    with pymupdf.open(tmp_path / "out.pdf") as saved:
        assert saved.needs_pass and saved.authenticate(corpus.user_password)
        assert ENCRYPTED_TEXT in saved[0].get_text()
    journal.undo()
    assert journal.document.raw[0].rect.width < journal.document.raw[0].rect.height
    journal.document.close()


# -- command bar --


@pytest.mark.feature("ORG-09")
@pytest.mark.feature("ORG-10")
@pytest.mark.feature("ORG-11")
@pytest.mark.feature("ORG-12")
def test_geometry_commands_plan_the_matching_ops() -> None:
    from engine.commands import CommandError, parse_command

    def ops(text: str) -> list[dict[str, object]]:
        return parse_command(text, page_count=8).ops

    assert ops("crop pages 1-3 by 36") == [
        {"op": "crop_pages", "page_indices": [0, 1, 2], "margins": [36.0, 36.0, 36.0, 36.0]}
    ]
    assert ops("uncrop page 2") == [{"op": "uncrop_pages", "page_indices": [1]}]
    assert ops("resize page 2 to letter landscape without scaling") == [
        {"op": "resize_pages", "page_indices": [1], "size": "letter-landscape", "scale": False}
    ]
    assert ops("impose 4 up") == [{"op": "n_up", "cols": 2, "rows": 2, "sheet": "a4"}]
    assert ops("impose as booklet on a3 landscape") == [{"op": "booklet", "sheet": "a3-landscape"}]
    with pytest.raises(CommandError, match="not 5"):
        ops("impose 5 up")


# -- review fixes: nothing is lost silently --


def _linked(path: Path) -> Path:
    doc = pymupdf.open()
    for n in range(1, 4):
        doc.new_page().insert_text((72, 72), f"Page {n}")
    doc.set_toc([[1, "One", 1], [1, "Two", 2], [2, "Three", 3]])
    doc[0].insert_link({"kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(72, 60, 150, 80), "page": 1})
    doc[1].insert_link(
        {"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(100, 100, 200, 120), "uri": "https://example.com"}
    )
    doc.save(path)
    return path


@pytest.mark.feature("ORG-10")
def test_resize_keeps_the_outline_and_links_and_scales_links_on_the_page(tmp_path: Path) -> None:
    doc = Document.open(_linked(tmp_path / "l.pdf"))
    _apply(doc, {"op": "resize_pages", "page_indices": [1], "size": "a5"})
    assert doc.raw.get_toc() == [[1, "One", 1], [1, "Two", 2], [2, "Three", 3]]
    assert [link["page"] for link in doc.raw[0].get_links()] == [1]  # the link into the resized page survives
    (uri,) = doc.raw[1].get_links()
    scale = 420 / 595
    assert tuple(uri["from"]) == pytest.approx(tuple(pymupdf.Rect(100, 100, 200, 120) * scale), abs=0.5)


@pytest.mark.feature("ORG-10")
def test_resize_refuses_to_drop_annotations_unless_asked(tmp_path: Path) -> None:
    source = pymupdf.open(_linked(tmp_path / "l.pdf"))
    source[1].add_text_annot((50, 50), "keep me")
    source.save(tmp_path / "a.pdf")
    doc = Document.open(tmp_path / "a.pdf")
    with pytest.raises(OpValidationError, match="can't carry over 1 annotation"):
        _apply(doc, {"op": "resize_pages", "page_indices": [1], "size": "a5"})
    assert doc.raw[1].rect.width == 595  # nothing changed
    _apply(doc, {"op": "resize_pages", "page_indices": [1], "size": "a5", "drop_interactive": True})
    assert doc.raw[1].rect.width == 420


@pytest.mark.feature("ORG-11")
@pytest.mark.feature("ORG-12")
def test_imposition_refuses_to_drop_links_and_moves_bookmarks_to_sheets(tmp_path: Path) -> None:
    doc = Document.open(_linked(tmp_path / "l.pdf"))
    with pytest.raises(OpValidationError, match="can't carry over 2 links"):
        _apply(doc, {"op": "n_up", "cols": 2, "rows": 1})
    assert doc.page_count == 3
    _apply(doc, {"op": "n_up", "cols": 2, "rows": 1, "drop_interactive": True})
    assert doc.raw.get_toc() == [[1, "One", 1], [1, "Two", 1], [2, "Three", 2]]
    doc = Document.open(tmp_path / "l.pdf")
    _apply(doc, {"op": "booklet", "drop_interactive": True})  # order [blank, 1, 2, 3]
    assert doc.raw.get_toc() == [[1, "One", 1], [1, "Two", 2], [2, "Three", 2]]


@pytest.mark.feature("ORG-11")
def test_nan_and_negative_geometry_are_refused(tmp_path: Path) -> None:
    doc = Document.open(_numbered(tmp_path / "p.pdf", 4))
    with pytest.raises(OpValidationError, match="finite number"):
        parse_op({"op": "n_up", "cols": 2, "rows": 1, "gap": float("nan")})
    with pytest.raises(OpValidationError, match="finite number"):
        parse_op({"op": "crop_pages", "page_indices": [0], "box": (0, 0, float("nan"), 100)})
    with pytest.raises(OpValidationError, match="can't be negative"):
        _apply(doc, {"op": "n_up", "cols": 2, "rows": 1, "gap": -5})
