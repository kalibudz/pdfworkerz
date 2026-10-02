"""OCR-01 / OCR-02: searchable PDFs through Tesseract, and its language packs."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from engine.document import Document
from engine.errors import MissingToolError, OcrError
from engine.external import find_tool
from engine.ocr import add_invisible_text, ocr_document, page_has_text
from engine.ocr_langs import install_language, installed_languages, resolve_languages
from engine.ops.base import parse_op
from engine.ops.journal import UndoRedoJournal
from tests import scans

needs_tesseract = pytest.mark.skipif(find_tool("tesseract") is None, reason="Tesseract is not installed")


@pytest.fixture
def scan(work_dir: Path) -> Document:
    path = work_dir / "scan.pdf"
    path.write_bytes(scans.scanned_pdf_bytes(scans.text_page_png()))
    return Document.open(path)


@needs_tesseract
@pytest.mark.feature("OCR-01", criterion=1)
def test_ocr_makes_a_scan_searchable_without_changing_how_it_looks(scan: Document) -> None:
    assert not page_has_text(scan.raw[0])
    before = scan.raw[0].get_pixmap(dpi=100).samples
    report = ocr_document(scan)
    assert report.pages[0].status == "recognised"
    assert report.words >= 12
    text = scan.raw[0].get_text("text")
    assert "Invoice Number 20481" in text.replace("\n", " ")
    assert scan.raw[0].search_for("Northwind")
    assert scan.raw[0].get_pixmap(dpi=100).samples == before


@needs_tesseract
@pytest.mark.feature("OCR-01", criterion=1)
def test_ocr_text_lands_on_the_words_of_a_page_stored_sideways(work_dir: Path) -> None:
    upright = Document.from_bytes(scans.scanned_pdf_bytes(scans.text_page_png()))
    ocr_document(upright)
    expected = upright.raw[0].search_for("Northwind")[0]
    sideways = Document.from_bytes(scans.sideways_scan_pdf_bytes(scans.text_page_png()))
    assert sideways.raw[0].rotation == 90
    ocr_document(sideways)
    found = sideways.raw[0].search_for("Northwind")
    assert found, "the word is found on a rotated page"
    shown = (found[0] * sideways.raw[0].rotation_matrix).normalize()  # search works on the unrotated page
    assert abs(shown.x0 - expected.x0) < 10 and abs(shown.y0 - expected.y0) < 10


@needs_tesseract
@pytest.mark.feature("OCR-01", criterion=2)
def test_pages_with_text_are_skipped_unless_forced(work_dir: Path) -> None:
    raw = pymupdf.open()
    raw.new_page().insert_text((72, 100), "Already searchable text", fontsize=14)
    document = Document.from_bytes(raw.tobytes())
    skipped = ocr_document(document)
    assert skipped.pages[0].status == "skipped"
    assert skipped.words == 0
    forced = ocr_document(document, force=True)
    assert forced.pages[0].status == "recognised"


@needs_tesseract
@pytest.mark.feature("OCR-01", criterion=3)
def test_ocr_is_one_undo_step_with_a_per_page_report(scan: Document) -> None:
    journal = UndoRedoJournal(scan)
    result = journal.record(parse_op({"op": "ocr", "page_indices": [0], "language": "eng"}))
    assert result.pages[0].page_index == 0 and result.pages[0].words > 0  # type: ignore[attr-defined]
    assert page_has_text(journal.document.raw[0])
    journal.undo()
    assert not page_has_text(journal.document.raw[0])
    journal.redo()
    assert page_has_text(journal.document.raw[0])


@pytest.mark.feature("OCR-01", criterion=3)
def test_ocr_refuses_a_page_outside_the_document(scan: Document) -> None:
    journal = UndoRedoJournal(scan)
    with pytest.raises(Exception, match="out of range"):
        journal.record(parse_op({"op": "ocr", "page_indices": [4]}))


@pytest.mark.feature("OCR-01", criterion=4)
def test_missing_tesseract_is_named_with_how_to_install_it(scan: Document, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PDFWORKERZ_TESSERACT", str(scan.source_path) + ".nowhere")
    with pytest.raises(MissingToolError, match=r"Tesseract OCR is not installed.*winget install"):
        ocr_document(scan)
    assert not page_has_text(scan.raw[0])


def test_non_latin_words_get_a_font_that_can_hold_them() -> None:
    from engine.ocr import OcrWord

    raw = pymupdf.open()
    page = raw.new_page()
    add_invisible_text(page, [OcrWord("Привет", (72, 100, 160, 120), 90, (1, 1, 1))])
    assert "Привет" in page.get_text("text")


@needs_tesseract
@pytest.mark.feature("OCR-02", criterion=1)
def test_installed_languages_are_listed_with_names() -> None:
    languages = {language.code: language for language in installed_languages()}
    assert languages["eng"].name == "English"
    assert languages["eng"].installed_by == "Tesseract"
    assert "osd" not in languages


@needs_tesseract
@pytest.mark.feature("OCR-02", criterion=2)
def test_a_combination_of_languages_resolves_to_one_folder(work_dir: Path) -> None:
    pack = next(p for p in Path(resolve_languages("eng")[1]).glob("eng.traineddata"))
    install_language("deu", pack.read_bytes())  # stands in for a second pack: same format, the user's folder
    spec, folder = resolve_languages("eng+deu")
    assert spec == "eng+deu"
    assert (folder / "eng.traineddata").is_file() and (folder / "deu.traineddata").is_file()


@needs_tesseract
@pytest.mark.feature("OCR-02", criterion=2)
def test_ocr_runs_with_a_chosen_language(scan: Document) -> None:
    report = ocr_document(scan, language="eng")
    assert report.language == "eng"


@needs_tesseract
@pytest.mark.feature("OCR-02", criterion=3)
def test_an_uninstalled_language_is_refused_before_any_ocr(scan: Document) -> None:
    with pytest.raises(OcrError, match=r"zzz is not installed. Installed: .*eng"):
        ocr_document(scan, language="eng+zzz")
    assert not page_has_text(scan.raw[0])
    with pytest.raises(OcrError, match="not a language code"):
        resolve_languages("eng; rm -rf /")


@needs_tesseract
@pytest.mark.feature("OCR-02", criterion=4)
def test_a_language_pack_is_added_to_the_users_folder_only(work_dir: Path, tmp_path: Path) -> None:
    from engine.ocr_langs import system_tessdata, user_tessdata

    system_before = sorted(p.name for p in system_tessdata().iterdir())
    donor = system_tessdata() / "eng.traineddata"
    language = install_language("fra", donor)  # a file on disk, as for an offline machine
    assert language.installed_by == "you"
    assert (user_tessdata() / "fra.traineddata").is_file()
    assert sorted(p.name for p in system_tessdata().iterdir()) == system_before
    assert any(row.code == "fra" and row.installed_by == "you" for row in installed_languages())
    with pytest.raises(OcrError, match="too small"):
        install_language("spa", b"not a pack")
