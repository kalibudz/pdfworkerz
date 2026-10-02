"""The shared export and convert entry points the CLI and the server both use."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pymupdf
import pytest

from engine.convert import convert_files, convert_url
from engine.document import Document
from engine.errors import ConversionError, OpValidationError
from engine.exports import FORMATS, ExportOptions, export_document
from engine.external import find_tool
from engine.rasters import NamedFile
from tests import pdfs


@pytest.fixture(scope="module")
def report() -> Document:
    return Document.from_bytes(pdfs.report_pdf())


def _names(archive: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        return sorted(zipped.namelist())


def test_every_format_is_listed_and_unknown_ones_are_refused(report: Document) -> None:
    assert set(FORMATS) == {
        "docx",
        "xlsx",
        "pptx",
        "images",
        "text",
        "markdown",
        "html",
        "tables",
        "pdfa",
        "embedded-images",
    }
    with pytest.raises(OpValidationError, match="unknown export format 'rtf'"):
        export_document(report, "rtf")


@pytest.mark.parametrize(
    ("fmt", "suffix", "media"),
    [
        ("docx", ".docx", "wordprocessingml"),
        ("xlsx", ".xlsx", "spreadsheetml"),
        ("pptx", ".pptx", "presentationml"),
        ("text", ".txt", "text/plain"),
        ("markdown", ".md", "text/markdown"),
        ("html", ".html", "text/html"),
    ],
)
def test_each_single_file_format_names_and_types_its_result(
    report: Document, fmt: str, suffix: str, media: str
) -> None:
    result = export_document(report, fmt)
    assert result.name == f"document{suffix}" and media in result.media_type and result.data


def test_the_name_follows_the_documents_own_file_name(work_dir: Path) -> None:
    path = work_dir / "Quarterly Report.pdf"
    path.write_bytes(pdfs.report_pdf())
    assert export_document(Document.open(path), "text").name == "Quarterly Report.txt"


def test_pages_option_limits_every_format(report: Document) -> None:
    only_second = ExportOptions(pages="2")
    assert "Appendix" in export_document(report, "text", only_second).data.decode()
    assert "Quarterly" not in export_document(report, "markdown", only_second).data.decode()
    with pytest.raises(OpValidationError, match="outside this document"):
        export_document(report, "text", ExportOptions(pages="9"))


def test_images_are_one_file_when_there_is_one_page_and_a_zip_when_there_are_several(report: Document) -> None:
    one = export_document(report, "images", ExportOptions(pages="1", dpi=72))
    assert one.name == "document-page-1.png" and one.media_type == "image/png"
    several = export_document(report, "images", ExportOptions(dpi=72, image_format="jpg"))
    assert several.media_type == "application/zip" and _names(several.data) == [
        "document-page-1.jpg",
        "document-page-2.jpg",
    ]
    tiff = export_document(report, "images", ExportOptions(dpi=72, image_format="tiff", single_tiff=True))
    assert tiff.name == "document.tiff" and tiff.media_type == "image/tiff"


def test_tables_and_embedded_pictures_export_or_say_there_are_none(report: Document) -> None:
    csv = export_document(report, "tables")
    assert csv.name == "page-1-table-1.csv" and csv.media_type == "text/csv"
    pictures = export_document(report, "embedded-images", ExportOptions(min_size=10))
    assert pictures.name.endswith(".png") or pictures.name.endswith(".jpeg")
    blank = Document.from_bytes(_blank_pdf())
    with pytest.raises(OpValidationError, match="no tables"):
        export_document(blank, "tables")
    with pytest.raises(OpValidationError, match="no pictures"):
        export_document(blank, "embedded-images")


def _blank_pdf() -> bytes:
    raw = pymupdf.open()
    raw.new_page().insert_text((72, 100), "text only", fontsize=12)
    return raw.tobytes()


@pytest.mark.skipif(find_tool("ghostscript") is None, reason="Ghostscript is not installed")
def test_pdfa_comes_back_as_a_pdf_with_the_validation_notes(report: Document) -> None:
    result = export_document(report, "pdfa")
    assert result.name == "document-pdfa.pdf" and result.data.startswith(b"%PDF") and result.notes


# ---------- convert ----------


def test_a_text_file_a_markdown_file_and_an_html_file_each_become_a_pdf() -> None:
    text = convert_files([NamedFile("log.txt", b"line one\nline two\n")])
    assert text.name == "log.pdf" and "line two" in pymupdf.open("pdf", text.pdf)[0].get_text()
    markdown = convert_files([NamedFile("notes.md", b"# Heading\n\nbody text")], paper="letter")
    assert pymupdf.open("pdf", markdown.pdf)[0].rect.width == 612
    html = convert_files([NamedFile("page.html", b"<h1>Title</h1><p>Words</p>")])
    assert "Title" in pymupdf.open("pdf", html.pdf)[0].get_text()


def test_pictures_combine_but_other_files_come_one_at_a_time() -> None:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (50, 40), (255, 0, 0)).save(buffer, format="PNG")
    pictures = [NamedFile("a.png", buffer.getvalue()), NamedFile("b.png", buffer.getvalue())]
    assert pymupdf.open("pdf", convert_files(pictures).pdf).page_count == 2
    with pytest.raises(ConversionError, match="only pictures can be combined"):
        convert_files([NamedFile("a.txt", b"x"), NamedFile("b.txt", b"y")])


def test_an_unknown_type_or_nothing_at_all_is_refused_plainly() -> None:
    with pytest.raises(ConversionError, match=r"archive\.zip is not a type PDFWorkerz converts"):
        convert_files([NamedFile("archive.zip", b"PK")])
    with pytest.raises(ConversionError, match="nothing to convert"):
        convert_files([])
    with pytest.raises(ConversionError, match="only http:// and https://"):
        convert_url("ftp://example.com/x")
