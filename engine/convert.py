"""Making a PDF from files or a web address -- the one entry point the CLI and the server share.

What a file *is* decides how it is converted (by its extension, and then by what is inside it:
see engine.import_docs), so a person only has to hand over the files.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from engine.errors import ConversionError
from engine.import_docs import (
    OFFICE_EXTENSIONS,
    html_to_pdf,
    images_to_pdf,
    markdown_to_pdf,
    office_to_pdf,
    text_to_pdf,
    url_to_pdf,
)
from engine.rasters import NamedFile, Paper

Kind = Literal["word", "excel", "powerpoint", "image", "html", "markdown", "text"]
_OTHER_EXTENSIONS: dict[Kind, tuple[str, ...]] = {
    "image": (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp"),
    "html": (".html", ".htm"),
    "markdown": (".md", ".markdown"),
    "text": (".txt", ".text", ".log"),
}
ACCEPTED_EXTENSIONS: tuple[str, ...] = (
    *(ext for exts in OFFICE_EXTENSIONS.values() for ext in exts),
    *(ext for exts in _OTHER_EXTENSIONS.values() for ext in exts),
)


@dataclass(frozen=True)
class Converted:
    pdf: bytes
    name: str
    """A suggested file name for the PDF."""
    notes: list[str] = field(default_factory=list)


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def convert_files(files: Sequence[NamedFile], *, paper: Paper | None = None, margin: float | None = None) -> Converted:
    """One PDF from the files. Pictures may be several (a page each, in order); anything else is
    one file at a time. ``paper`` and ``margin`` shape the page for pictures (default: a page the
    size of each picture), HTML, text and Markdown (default: A4 with a 54 pt margin); Office files
    keep their own page setup."""
    if not files:
        raise ConversionError("there is nothing to convert: no files were given")
    kinds = {_kind_of(file) for file in files}
    stem = Path(files[0].name).stem or "document"
    name = f"{stem}.pdf"
    if kinds == {"image"}:
        return Converted(images_to_pdf(files, paper=paper or "fit", margin=margin or 0.0), name)
    if len(files) > 1:
        raise ConversionError("only pictures can be combined into one PDF; convert the other files one at a time")
    (kind,) = kinds
    file = files[0]
    sheet: Paper = paper if paper and paper != "fit" else "a4"
    gap = 54.0 if margin is None else margin
    if kind in ("word", "excel", "powerpoint"):
        return Converted(office_to_pdf(file, kind), name)
    if kind == "html":
        return Converted(html_to_pdf(_decode(file.data), paper=sheet, margin=gap), name)
    if kind == "markdown":
        return Converted(markdown_to_pdf(_decode(file.data), paper=sheet, margin=gap), name)
    return Converted(text_to_pdf(_decode(file.data), paper=sheet, margin=gap), name)


def _kind_of(file: NamedFile) -> Kind:
    extension = Path(file.name).suffix.lower()
    for kind, extensions in OFFICE_EXTENSIONS.items():
        if extension in extensions:
            return kind
    for other, extensions in _OTHER_EXTENSIONS.items():
        if extension in extensions:
            return other
    shown = ", ".join(sorted(ACCEPTED_EXTENSIONS))
    raise ConversionError(f"{file.name} is not a type PDFWorkerz converts to PDF (it reads {shown})")


def convert_url(url: str, *, paper: Paper = "a4", margin: float = 54.0) -> Converted:
    """A web page as a PDF."""
    result = url_to_pdf(url, paper="a4" if paper == "fit" else paper, margin=margin)
    return Converted(result.pdf, "web-page.pdf", result.notes)
