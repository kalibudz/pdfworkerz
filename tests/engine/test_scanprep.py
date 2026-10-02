"""OCR-03: deskew, denoise and auto-rotate."""

from __future__ import annotations

import cv2
import numpy as np
import pymupdf
import pytest

from engine.document import Document
from engine.external import find_tool
from engine.ocr import ocr_document
from engine.ops.base import parse_op
from engine.ops.journal import UndoRedoJournal
from engine.scanprep import MIN_SPECKS, clean_scanned_pages, denoise, estimate_skew
from tests import scans

needs_tesseract = pytest.mark.skipif(find_tool("tesseract") is None, reason="Tesseract is not installed")


def _scan_of(image: np.ndarray, *, rotation: int = 0) -> Document:
    return Document.from_bytes(scans.scanned_pdf_bytes(scans.encode(image), rotation=rotation))


def _stored_picture(document: Document) -> np.ndarray:
    xref = document.raw[0].get_images()[0][0]
    return cv2.imdecode(np.frombuffer(document.raw.extract_image(xref)["image"], np.uint8), cv2.IMREAD_COLOR)


def _words_read(document: Document) -> str:
    ocr_document(document, force=True)
    return " ".join(document.raw[0].get_text("text").split())


@pytest.mark.parametrize("tilt", [-4.0, -1.5, 2.0, 5.0])
@pytest.mark.feature("OCR-03", criterion=1)
def test_the_skew_angle_is_found_within_half_a_degree(tilt: float) -> None:
    skewed = scans.rotate_image(scans.decode(scans.text_page_png()), tilt)
    assert abs(estimate_skew(skewed) - tilt) < 0.5


@needs_tesseract
@pytest.mark.feature("OCR-03", criterion=1)
def test_a_skewed_scan_is_turned_straight_and_reads_cleanly() -> None:
    skewed = scans.rotate_image(scans.decode(scans.text_page_png()), 4.0)
    document = _scan_of(skewed)
    [result] = clean_scanned_pages(document, rotate=False, remove_noise=False)
    assert result.status == "cleaned" and abs(result.skew_corrected - 4.0) < 0.5
    assert abs(estimate_skew(_stored_picture(document))) < 0.5
    assert "Northwind Traders" in _words_read(document)


@needs_tesseract
@pytest.mark.feature("OCR-03", criterion=2)
@pytest.mark.parametrize("stored_turn", [90, 180, 270])
def test_a_page_scanned_sideways_or_upside_down_is_turned_upright(stored_turn: int) -> None:
    upright = scans.decode(scans.text_page_png(dpi=250))
    codes = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}
    stored = cv2.rotate(upright, codes[stored_turn])
    sheet = pymupdf.open()
    width, height = (612, 792) if stored_turn == 180 else (792, 612)  # paper fed sideways gives a landscape sheet
    page = sheet.new_page(width=width, height=height)
    page.insert_image(page.rect, stream=scans.encode(stored))
    document = Document.from_bytes(sheet.tobytes())
    [result] = clean_scanned_pages(document, straighten=False, remove_noise=False)
    assert result.status == "cleaned"
    assert result.rotated_by == (360 - stored_turn) % 360
    assert "Invoice Number 20481" in _words_read(document)


@pytest.mark.parametrize("fraction", [0.01, 0.02])
@pytest.mark.feature("OCR-03", criterion=3)
def test_denoising_removes_the_specks_and_keeps_the_text(fraction: float) -> None:
    clean = scans.decode(scans.text_page_png())
    noisy = scans.add_speckle(clean, fraction=fraction)
    tidied, removed = denoise(noisy)
    assert removed >= MIN_SPECKS
    ink = lambda image: int((cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) < 128).sum())  # noqa: E731
    assert ink(tidied) < 0.6 * ink(noisy)
    assert ink(tidied) >= 0.97 * ink(clean), "the characters themselves keep their ink"


@needs_tesseract
@pytest.mark.feature("OCR-03", criterion=3)
def test_denoising_does_not_make_the_text_harder_to_read() -> None:
    noisy = scans.add_speckle(scans.decode(scans.text_page_png()), fraction=0.02)
    before = _words_read(_scan_of(noisy))
    document = _scan_of(noisy)
    clean_scanned_pages(document, rotate=False, straighten=False)
    after = _words_read(document)
    target = "Customer: Northwind Traders"
    assert target in after
    assert len(before.split()) <= len(after.split()) + 2 or target in before


@needs_tesseract
@pytest.mark.feature("OCR-03", criterion=4)
def test_each_step_can_be_switched_off_and_the_result_says_what_ran() -> None:
    tilted = scans.add_speckle(scans.rotate_image(scans.decode(scans.text_page_png()), 3.0), fraction=0.02)
    document = _scan_of(tilted)
    [only_noise] = clean_scanned_pages(document, rotate=False, straighten=False)
    assert only_noise.specks_removed > 0 and only_noise.skew_corrected == 0 and only_noise.rotated_by == 0
    [only_skew] = clean_scanned_pages(_scan_of(tilted), rotate=False, remove_noise=False)
    assert only_skew.skew_corrected != 0 and only_skew.specks_removed == 0


@needs_tesseract
@pytest.mark.feature("OCR-03", criterion=4)
def test_a_clean_straight_page_is_left_exactly_as_it_was() -> None:
    document = _scan_of(scans.decode(scans.text_page_png(dpi=250)))
    before = (_stored_picture(document).tobytes(), document.raw[0].rotation)
    [result] = clean_scanned_pages(document)
    assert result.status == "unchanged"
    assert result.rotated_by == 0 and result.skew_corrected == 0 and result.specks_removed == 0
    assert (_stored_picture(document).tobytes(), document.raw[0].rotation) == before


@pytest.mark.feature("OCR-03", criterion=4)
def test_pages_with_real_text_are_skipped_and_cleanup_is_one_undo_step() -> None:
    raw = pymupdf.open()
    raw.new_page().insert_text((72, 100), "Real text, not a scan", fontsize=14)
    [skipped] = clean_scanned_pages(Document.from_bytes(raw.tobytes()), rotate=False)
    assert skipped.status == "skipped" and "text already" in skipped.note

    tilted = scans.rotate_image(scans.decode(scans.text_page_png()), 4.0)
    journal = UndoRedoJournal(_scan_of(tilted))
    before = _stored_picture(journal.document).tobytes()
    journal.record(parse_op({"op": "clean_scan", "rotate": False, "remove_noise": False}))
    assert _stored_picture(journal.document).tobytes() != before
    journal.undo()
    assert _stored_picture(journal.document).tobytes() == before
