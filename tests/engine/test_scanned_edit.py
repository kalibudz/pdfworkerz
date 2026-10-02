"""EDT-12: editing text on a scanned page -- remove the old pixels, draw the new text in the
estimated style, keep everything else as it was."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from engine.document import Document
from engine.errors import OcrError, OpValidationError
from engine.external import find_tool
from engine.ocr import ocr_document, page_has_text
from engine.ops.base import parse_op
from engine.ops.journal import UndoRedoJournal
from engine.scanned_edit import ScannedLine, StyleChoice, edit_scanned_text, find_scanned_lines
from engine.scanned_style import ink_box, ink_mask
from engine.scanpage import has_visible_text
from tests import scans

needs_tesseract = pytest.mark.skipif(find_tool("tesseract") is None, reason="Tesseract is not installed")

TARGET = "Customer: Northwind Traders"


def _scan(
    *, color: tuple[float, float, float] = (0, 0, 0), sideways: bool = False, fontsize: float = 16, dpi: int = 250
) -> Document:
    png = scans.text_page_png(dpi=dpi, color=color, fontsize=fontsize)
    data = scans.sideways_scan_pdf_bytes(png) if sideways else scans.scanned_pdf_bytes(png)
    return Document.from_bytes(data)


def _line(document: Document, text: str = TARGET) -> ScannedLine:
    lines = find_scanned_lines(document, 0)
    return next(line for line in lines if text.split()[0] in line.text)


def _picture(document: Document) -> np.ndarray:
    xref = document.raw[0].get_images()[0][0]
    return cv2.imdecode(np.frombuffer(document.raw.extract_image(xref)["image"], np.uint8), cv2.IMREAD_COLOR)


def _read(document: Document) -> str:
    copy = Document.from_bytes(document.to_bytes())
    ocr_document(copy, force=True)
    return " ".join(copy.raw[0].get_text("text").split())


@needs_tesseract
@pytest.mark.feature("EDT-12", criterion=1)
def test_a_line_on_a_scanned_page_can_be_replaced() -> None:
    document = _scan()
    line = _line(document)
    assert line.text == TARGET
    result = edit_scanned_text(document, 0, line.rect, "Customer: Contoso Traders")
    assert result.old_text == TARGET
    text = _read(document)
    assert "Customer: Contoso Traders" in text
    assert "Northwind" not in text
    assert not has_visible_text(document.raw[0]), "the page is still one picture"


@needs_tesseract
@pytest.mark.feature("EDT-12", criterion=1)
def test_a_single_word_can_be_replaced_leaving_the_rest() -> None:
    document = _scan()
    line = _line(document, "Invoice")
    edit_scanned_text(document, 0, line.rect, "Invoice Number 99999")
    text = _read(document)
    assert "Invoice Number 99999" in text and "20481" not in text
    assert "Customer: Northwind Traders" in text, "the other lines were not touched"


@needs_tesseract
@pytest.mark.feature("EDT-12", criterion=2)
def test_the_new_text_is_drawn_in_the_estimated_style_and_colour() -> None:
    document = _scan(color=(10 / 255, 30 / 255, 140 / 255), fontsize=16)
    line = _line(document)
    result = edit_scanned_text(document, 0, line.rect, "Customer: Contoso Traders")
    assert result.style.font_class == "sans" and not result.style.bold
    assert abs(result.style.size_pt - 16) <= 1.0
    after = _picture(document)
    scale = after.shape[1] / 612
    x0, y0, x1, y1 = (int(v * scale) for v in line.rect)
    patch = after[y0:y1, x0:x1]
    ink = patch[ink_mask(patch)]
    darkest = ink[np.argsort(ink.sum(axis=1))[: max(len(ink) // 3, 1)]].mean(axis=0)[::-1]  # BGR -> RGB
    assert darkest[2] > darkest[0] + 60, f"still blue, not black: {darkest}"
    assert "Contoso" in _read(document)


@needs_tesseract
@pytest.mark.feature("EDT-12", criterion=2)
def test_hand_set_style_overrides_the_estimate() -> None:
    document = _scan()
    line = _line(document, "Invoice")
    result = edit_scanned_text(
        document,
        0,
        line.rect,
        "INVOICE",
        style=StyleChoice(font_class="serif", bold=True, size_pt=20, color=(150, 20, 20)),
    )
    assert (result.style.font_class, result.style.bold, result.style.size_pt, result.style.color) == (
        "serif",
        True,
        20,
        (150, 20, 20),
    )


@needs_tesseract
@pytest.mark.feature("EDT-12", criterion=3)
def test_deleting_leaves_clean_paper_and_nothing_outside_the_area_changes() -> None:
    document = _scan()
    before = _picture(document)
    line = _line(document)
    edit_scanned_text(document, 0, line.rect, "")
    after = _picture(document)
    scale = after.shape[1] / 612
    x0, y0, x1, y1 = (round(v * scale) for v in line.rect)
    pad = 4
    region = (slice(y0 - pad, y1 + pad), slice(x0 - pad, x1 + pad))
    assert ink_box(ink_mask(after[region])) is None, "no ghost of the old text is left"
    assert int(after[region].min()) >= 240
    outside = np.ones(after.shape[:2], bool)
    outside[region] = False
    assert np.array_equal(before[outside], after[outside]), "pixels outside the edited area are untouched"
    assert "Northwind" not in _read(document)


@needs_tesseract
@pytest.mark.feature("EDT-12", criterion=3)
def test_the_old_ink_is_gone_from_the_file_not_hidden_under_a_patch() -> None:
    import pymupdf

    document = _scan()
    line = _line(document)
    edit_scanned_text(document, 0, line.rect, "x")
    saved = pymupdf.open("pdf", document.to_bytes())
    scale = saved.extract_image(saved[0].get_images()[0][0])["width"] / 612
    x0, y0, x1, y1 = (int(v * scale) for v in line.rect)
    for xref in range(1, saved.xref_length()):
        if "/Subtype/Image" not in (saved.xref_object(xref, compressed=True) or "").replace(" ", ""):
            continue
        picture = cv2.imdecode(np.frombuffer(saved.extract_image(xref)["image"], np.uint8), cv2.IMREAD_COLOR)
        right_part = picture[y0:y1, x0 + (x1 - x0) // 2 : x1]
        assert ink_box(ink_mask(right_part)) is None, f"picture object {xref} still holds the old text"


@needs_tesseract
@pytest.mark.feature("EDT-12", criterion=3)
def test_the_search_layer_of_an_ocr_page_follows_the_edit() -> None:
    document = _scan()
    ocr_document(document)
    assert document.raw[0].search_for("Northwind")
    line = _line(document)
    edit_scanned_text(document, 0, line.rect, "Customer: Contoso Traders")
    assert document.raw[0].search_for("Contoso")
    assert not document.raw[0].search_for("Northwind")
    assert document.raw[0].search_for("Invoice"), "the other lines stay searchable"
    assert not has_visible_text(document.raw[0])


@needs_tesseract
@pytest.mark.feature("EDT-12", criterion=1)
def test_a_page_stored_sideways_with_a_rotation_edits_in_the_right_place() -> None:
    document = _scan(sideways=True)
    line = _line(document)
    edit_scanned_text(document, 0, line.rect, "Customer: Contoso Traders")
    assert "Contoso" in _read(document) and "Northwind" not in _read(document)


@needs_tesseract
@pytest.mark.feature("EDT-12", criterion=4)
def test_the_edit_is_one_typed_op_and_one_undo_step() -> None:
    document = _scan()
    line = _line(document)
    journal = UndoRedoJournal(document)
    before = _picture(journal.document).tobytes()
    journal.record(
        parse_op(
            {
                "op": "edit_scanned_text",
                "page_index": 0,
                "rect": list(line.rect),
                "new_text": "Customer: Contoso Traders",
            }
        )
    )
    assert _picture(journal.document).tobytes() != before
    journal.undo()
    assert _picture(journal.document).tobytes() == before
    journal.redo()
    assert _picture(journal.document).tobytes() != before


@pytest.mark.feature("EDT-12", criterion=4)
def test_a_page_with_real_text_is_refused_and_pointed_to_the_text_editor() -> None:
    import pymupdf

    raw = pymupdf.open()
    raw.new_page().insert_text((72, 100), "Ordinary text", fontsize=14)
    document = Document.from_bytes(raw.tobytes())
    with pytest.raises(OcrError, match=r"not a scanned page.*normal text editor"):
        edit_scanned_text(document, 0, (60, 80, 200, 110), "other")
    assert page_has_text(document.raw[0])


@needs_tesseract
@pytest.mark.feature("EDT-12", criterion=4)
def test_bad_requests_are_refused_before_anything_changes() -> None:
    document = _scan()
    line = _line(document)
    before = _picture(document).tobytes()
    with pytest.raises(OpValidationError, match="can't be drawn"):
        edit_scanned_text(document, 0, line.rect, "café 你好")
    with pytest.raises(OpValidationError, match="line break"):
        edit_scanned_text(document, 0, line.rect, "two\nlines")
    with pytest.raises(OpValidationError, match="times wider"):
        edit_scanned_text(
            document,
            0,
            line.rect,
            "A very long replacement that cannot possibly fit in the small space it replaces " * 2,
        )
    with pytest.raises(OcrError, match="no text in that area"):
        edit_scanned_text(document, 0, (400, 600, 500, 650), "x")
    assert _picture(document).tobytes() == before


@needs_tesseract
@pytest.mark.feature("EDT-12", criterion=2)
def test_text_a_little_too_long_is_shrunk_to_fit_and_says_so() -> None:
    document = _scan()
    line = _line(document)
    result = edit_scanned_text(document, 0, line.rect, "Customer: Contoso Traders Ltd")
    assert any("reduced" in note for note in result.notes)
    assert result.style.size_pt < 16
