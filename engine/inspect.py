"""Document inspection report (COR-03).

Builds a single, typed snapshot of what a PDF contains -- version, fonts,
images, forms, signatures, layers, bookmarks and encryption -- so the UI,
CLI and reviewer tooling all read the same facts instead of poking at
PyMuPDF and pikepdf separately. Every field here is read directly from a
verified library call; nothing is inferred by guesswork.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
from pydantic import BaseModel, ConfigDict

from engine.security import EncryptionInfo, inspect_encryption


class FontInfo(BaseModel):
    """One font used somewhere in the document (deduplicated by xref)."""

    model_config = ConfigDict(frozen=True)

    xref: int
    basefont: str
    font_type: str
    embedded: bool


class InspectionReport(BaseModel):
    """Everything COR-03 promises: a full-document summary, read-only."""

    model_config = ConfigDict(frozen=True)

    path: str
    file_size_bytes: int
    pdf_version: str
    page_count: int
    is_repaired: bool
    encryption: EncryptionInfo
    fonts: list[FontInfo]
    image_count: int
    has_forms: bool
    has_signatures: bool
    has_layers: bool
    bookmark_count: int
    metadata: dict[str, str]


def _collect_fonts(doc: pymupdf.Document) -> list[FontInfo]:
    seen: dict[int, FontInfo] = {}
    for page_index in range(doc.page_count):
        for xref, ext, font_type, basefont, *_rest in doc[page_index].get_fonts(full=True):
            if xref not in seen:
                seen[xref] = FontInfo(xref=xref, basefont=basefont, font_type=font_type, embedded=ext != "n/a")
    return sorted(seen.values(), key=lambda f: f.xref)


def _count_images(doc: pymupdf.Document) -> int:
    seen_xrefs: set[int] = set()
    for page_index in range(doc.page_count):
        for image in doc[page_index].get_images(full=True):
            seen_xrefs.add(image[0])
    return len(seen_xrefs)


def _has_signatures(doc: pymupdf.Document) -> bool:
    for page_index in range(doc.page_count):
        for widget in doc[page_index].widgets():
            if widget.field_type_string == "Signature":
                return True
    return False


def inspect_document(
    doc: pymupdf.Document,
    *,
    path: str | Path,
    is_repaired: bool,
    password: str = "",
) -> InspectionReport:
    """Build the COR-03 report for an already-open document.

    ``password`` is only used to re-inspect encryption metadata (SEC-01/02);
    it is never required to already be authenticated, since the encryption
    dictionary itself is always readable.
    """
    path = Path(path)
    encryption = (
        inspect_encryption(path, password=password) if path.exists() else EncryptionInfo(is_encrypted=doc.is_encrypted)
    )
    # doc.metadata is always a dict (verified against pymupdf 1.28.2, including on a fresh
    # in-memory document); pymupdf's stub types the property as `dict | None`.
    metadata = doc.metadata
    assert metadata is not None  # nosec B101 -- type narrowing, not a security check; see comment above
    return InspectionReport(
        path=str(path),
        file_size_bytes=path.stat().st_size if path.exists() else 0,
        pdf_version=str(metadata.get("format", "")),
        page_count=doc.page_count,
        is_repaired=is_repaired,
        encryption=encryption,
        fonts=_collect_fonts(doc),
        image_count=_count_images(doc),
        has_forms=bool(doc.is_form_pdf),
        has_signatures=_has_signatures(doc),
        has_layers=bool(doc.get_ocgs()),
        bookmark_count=len(doc.get_toc(simple=True)),
        metadata={k: v for k, v in metadata.items() if v},
    )
