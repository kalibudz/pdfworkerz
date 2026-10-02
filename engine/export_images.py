"""CVF-04 / CVF-08: pages as pictures, and the pictures inside pages.

``pages_to_images`` renders each page (what you *see*, text and all) as PNG, JPEG or TIFF.
``extract_embedded_images`` pulls out the pictures the PDF *contains*, exactly as stored: a
JPEG inside the PDF comes out as the same JPEG bytes, with no second compression.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import pymupdf
from PIL import Image

from engine.document import Document
from engine.errors import ConversionError, OpValidationError

ImageFormat = Literal["png", "jpg", "tiff"]
MIN_DPI, MAX_DPI = 36, 600
DEFAULT_MIN_SIZE = 32
"""Pictures whose shorter side is under this many pixels (bullets, rules, spacer dots) are left out."""
_JPEG_QUALITY = 92


@dataclass(frozen=True)
class ExportedFile:
    """One file produced by an export: its name and bytes."""

    name: str
    data: bytes


@dataclass(frozen=True)
class EmbeddedImage:
    """One picture found inside the document."""

    file: ExportedFile
    width: int
    height: int
    pages: list[int]
    """The pages (0-based) it is drawn on."""


def zip_files(files: Sequence[ExportedFile]) -> bytes:
    """Many exported files as one zip."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in files:
            archive.writestr(file.name, file.data)
    return buffer.getvalue()


def _picture(page: pymupdf.Page, dpi: int) -> Image.Image:
    pix = page.get_pixmap(dpi=dpi, alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def pages_to_images(
    document: Document,
    page_indices: Sequence[int] | None = None,
    *,
    fmt: ImageFormat = "png",
    dpi: int = 150,
    single_tiff: bool = False,
    stem: str = "document",
) -> list[ExportedFile]:
    """One file per page, named ``document-page-1.png`` (by the page's own number). With
    ``single_tiff``, one multi-page TIFF holding them all."""
    if not MIN_DPI <= dpi <= MAX_DPI:
        raise OpValidationError(f"dpi must be between {MIN_DPI} and {MAX_DPI}")
    if single_tiff and fmt != "tiff":
        raise OpValidationError("a single multi-page file is only possible as TIFF")
    indices = range(document.page_count) if page_indices is None else page_indices
    for index in indices:
        if not 0 <= index < document.page_count:
            raise OpValidationError(f"page {index + 1} is outside this document's pages 1-{document.page_count}")
    if single_tiff:
        pictures = [_picture(document.raw[i], dpi) for i in indices]
        if not pictures:
            raise ConversionError("there are no pages to export")
        buffer = io.BytesIO()
        pictures[0].save(
            buffer, format="TIFF", save_all=True, append_images=pictures[1:], compression="tiff_lzw", dpi=(dpi, dpi)
        )
        return [ExportedFile(f"{stem}.tiff", buffer.getvalue())]
    files = []
    for index in indices:
        name = f"{stem}-page-{index + 1}.{fmt}"
        if fmt == "png":
            data = document.raw[index].get_pixmap(dpi=dpi, alpha=False).tobytes("png")
        else:
            buffer = io.BytesIO()
            if fmt == "jpg":
                _picture(document.raw[index], dpi).save(buffer, format="JPEG", quality=_JPEG_QUALITY, dpi=(dpi, dpi))
            else:
                _picture(document.raw[index], dpi).save(buffer, format="TIFF", compression="tiff_lzw", dpi=(dpi, dpi))
            data = buffer.getvalue()
        files.append(ExportedFile(name, data))
    return files


def extract_embedded_images(
    document: Document, page_indices: Sequence[int] | None = None, *, min_size: int = DEFAULT_MIN_SIZE
) -> list[EmbeddedImage]:
    """Every picture stored in the pages (all by default), once each however many times it is
    drawn, in its original encoding, with the pages it appears on."""
    indices = range(document.page_count) if page_indices is None else page_indices
    pages_of: dict[int, list[int]] = {}
    for index in indices:
        for entry in document.raw[index].get_images(full=True):
            xref = entry[0]
            if index not in pages_of.setdefault(xref, []):
                pages_of[xref].append(index)
    found: list[EmbeddedImage] = []
    for xref, pages in pages_of.items():
        info = document.raw.extract_image(xref)
        if not info or min(info["width"], info["height"]) < min_size:
            continue
        name = f"image-{len(found) + 1}-page-{pages[0] + 1}.{info['ext']}"
        found.append(EmbeddedImage(ExportedFile(name, info["image"]), info["width"], info["height"], pages))
    return found
