"""Every way to take a PDF's content out as another format, behind one function.

The CLI and the server both call :func:`export_document`, so a format behaves the same from a
click, a command line or a script (SPEC.md section 4.2 rule 1: one code path per capability).
A result is always *one* file to hand over: a format that makes several (an image per page, a
CSV per table) is zipped, unless it made just one.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal, get_args

from engine.document import Document
from engine.errors import OpValidationError
from engine.export_images import (
    DEFAULT_MIN_SIZE,
    ExportedFile,
    ImageFormat,
    extract_embedded_images,
    pages_to_images,
    zip_files,
)
from engine.export_office import Fidelity, to_docx, to_pptx, to_xlsx
from engine.export_text import to_html, to_markdown, to_text
from engine.pagerange import parse_page_range
from engine.pdfa import Level, convert_to_pdfa
from engine.tables import Strategy, find_tables, tables_to_files

ExportFormat = Literal[
    "docx", "xlsx", "pptx", "images", "text", "markdown", "html", "tables", "pdfa", "embedded-images"
]
FORMATS: tuple[str, ...] = get_args(ExportFormat)

_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_IMAGE_TYPES = {"png": "image/png", "jpg": "image/jpeg", "tiff": "image/tiff"}


@dataclass(frozen=True)
class ExportOptions:
    """Every option any format takes; a format reads the ones that concern it."""

    pages: str | None = None
    """Pages as people write them: ``1-3,5``. None means all."""
    dpi: int = 150
    image_format: ImageFormat = "png"
    single_tiff: bool = False
    fidelity: Fidelity = "editable"
    skip_running: bool = False
    strategy: Strategy = "auto"
    numbers: bool = True
    level: Level = "2b"
    min_size: int = DEFAULT_MIN_SIZE


@dataclass(frozen=True)
class ExportResult:
    """The one file an export hands back."""

    name: str
    data: bytes
    media_type: str
    notes: list[str] = field(default_factory=list)


def _stem(document: Document) -> str:
    return document.source_path.stem if document.source_path else "document"


def _one_or_zip(files: Sequence[ExportedFile], stem: str, single_type: Callable[[str], str]) -> ExportResult:
    if len(files) == 1:
        return ExportResult(files[0].name, files[0].data, single_type(files[0].name))
    return ExportResult(f"{stem}.zip", zip_files(files), "application/zip")


def _extension_type(name: str) -> str:
    extension = name.rsplit(".", 1)[-1].lower()
    return _IMAGE_TYPES.get(extension, "text/csv" if extension == "csv" else "application/octet-stream")


def export_document(document: Document, fmt: str, options: ExportOptions | None = None) -> ExportResult:
    """The document (as it is now, unsaved edits included) in format ``fmt``."""
    if fmt not in FORMATS:
        raise OpValidationError(f"unknown export format {fmt!r}; choose one of {', '.join(FORMATS)}")
    options = options or ExportOptions()
    pages = parse_page_range(options.pages, document.page_count)
    stem = _stem(document)
    notes: list[str] = []

    if fmt == "docx":
        return ExportResult(f"{stem}.docx", to_docx(document, pages), _DOCX)
    if fmt == "xlsx":
        workbook = to_xlsx(document, pages, strategy=options.strategy, numbers=options.numbers)
        notes = [f"{name}: {table.label}" for name, table in workbook.sheets]
        return ExportResult(f"{stem}.xlsx", workbook.data, _XLSX, notes)
    if fmt == "pptx":
        return ExportResult(f"{stem}.pptx", to_pptx(document, pages, mode=options.fidelity), _PPTX)
    if fmt == "images":
        files = pages_to_images(
            document, pages, fmt=options.image_format, dpi=options.dpi, single_tiff=options.single_tiff, stem=stem
        )
        return _one_or_zip(files, stem, _extension_type)
    if fmt == "text":
        return ExportResult(
            f"{stem}.txt",
            to_text(document, pages, skip_running=options.skip_running).encode(),
            "text/plain; charset=utf-8",
        )
    if fmt == "markdown":
        return ExportResult(
            f"{stem}.md",
            to_markdown(document, pages, skip_running=options.skip_running).encode(),
            "text/markdown; charset=utf-8",
        )
    if fmt == "html":
        return ExportResult(
            f"{stem}.html",
            to_html(document, pages, skip_running=options.skip_running, title=stem).encode(),
            "text/html; charset=utf-8",
        )
    if fmt == "tables":
        found = find_tables(document, pages, strategy=options.strategy)
        if not found:
            raise OpValidationError("no tables were found on these pages")
        files = [ExportedFile(name, data) for name, data in tables_to_files(found)]
        return _one_or_zip(files, f"{stem}-tables", _extension_type)
    if fmt == "pdfa":
        result = convert_to_pdfa(document, level=options.level)
        return ExportResult(f"{stem}-pdfa.pdf", result.data, "application/pdf", result.notes)
    embedded = extract_embedded_images(document, pages, min_size=options.min_size)
    if not embedded:
        raise OpValidationError("no pictures were found in these pages")
    return _one_or_zip([image.file for image in embedded], f"{stem}-images", _extension_type)
