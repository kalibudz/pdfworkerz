"""EDT-08: insert, replace, move/resize, crop and delete images.

An image is addressed by its placement's position in
``Page.get_image_info()`` (``index``) -- one entry per *drawing* of an
image, so the same image object shown twice on a page is two placements
that can be changed independently. Only that placement is ever touched:
PyMuPDF's own ``Page.delete_image(xref)`` blanks the image object itself,
which would also change every other place it's shown, so it's not used.

Removing one placement is done with a redaction that removes images but
leaves text and vector graphics alone, shrunk to a 1pt square at a point
inside the target and outside every other image on the page -- MuPDF
removes any image the redaction touches, so this is what keeps it from
taking an overlapping neighbour along (confirmed against pymupdf 1.28.2).
If no such point exists (the target is entirely covered by other
images), the operation is refused rather than guessed at.

Incoming image data is decoded with Pillow before it goes near the PDF:
anything Pillow can't open, or that exceeds its decompression-bomb pixel
limit, is refused.
"""

from __future__ import annotations

import base64
import binascii
import io
from typing import Any

import pymupdf
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict

from engine.document import Document
from engine.errors import OpValidationError

Rect = tuple[float, float, float, float]

MAX_IMAGE_BYTES = 25 * 1024 * 1024
_PASSTHROUGH_FORMATS = frozenset({"PNG", "JPEG"})
# Verified at runtime (pymupdf 1.28.2); missing from pymupdf's stub, like engine.edit's.
_REMOVE_IMAGES_ONLY: dict[str, int] = {
    "images": pymupdf.PDF_REDACT_IMAGE_REMOVE,  # type: ignore[attr-defined]
    "graphics": pymupdf.PDF_REDACT_LINE_ART_NONE,  # type: ignore[attr-defined]
    "text": pymupdf.PDF_REDACT_TEXT_NONE,  # type: ignore[attr-defined]
}
_PIN_GRID = 7


class ImageInfo(BaseModel):
    """One image placement on a page."""

    model_config = ConfigDict(frozen=True)

    index: int
    xref: int
    """0 for an inline image (which can be moved/deleted but not reused)."""
    rect: Rect
    pixel_width: int
    pixel_height: int
    axis_aligned: bool
    """False when the placement is rotated or skewed; such an image can be
    deleted or replaced, but not cropped."""


def decode_image(data_base64: str) -> bytes:
    """Validate base64 image data and return bytes PyMuPDF can embed: PNG and
    JPEG as given, anything else Pillow reads re-encoded as PNG."""
    try:
        raw = base64.b64decode(data_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise OpValidationError("image data is not valid base64") from exc
    if len(raw) > MAX_IMAGE_BYTES:
        raise OpValidationError(f"image is {len(raw)} bytes; the limit is {MAX_IMAGE_BYTES}")
    try:
        with Image.open(io.BytesIO(raw)) as image:
            image.load()
            if image.format in _PASSTHROUGH_FORMATS:
                return raw
            out = io.BytesIO()
            image.save(out, format="PNG")
            return out.getvalue()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as exc:
        raise OpValidationError(f"image data could not be read as an image: {exc}") from exc


def _fit(area: pymupdf.Rect, stream: bytes) -> pymupdf.Rect:
    """`area` shrunk to the image's aspect ratio, centered. Done here rather
    than with insert_image(keep_proportion=True), which in pymupdf 1.28.2
    stretched a square image across a 2:1 rect anyway (confirmed by
    rendering it)."""
    with Image.open(io.BytesIO(stream)) as image:
        aspect = image.width / image.height
    width, height = area.width, area.height
    if width / height > aspect:
        width = height * aspect
    else:
        height = width / aspect
    x0 = area.x0 + (area.width - width) / 2
    y0 = area.y0 + (area.height - height) / 2
    return pymupdf.Rect(x0, y0, x0 + width, y0 + height)


def _is_axis_aligned(transform: tuple[float, ...]) -> bool:
    a, b, c, d, _e, _f = transform
    return abs(b) < 1e-6 and abs(c) < 1e-6 and a > 0 and d > 0


def _info(index: int, raw: dict[str, Any]) -> ImageInfo:
    return ImageInfo(
        index=index,
        xref=raw.get("xref", 0),
        rect=tuple(raw["bbox"]),
        pixel_width=raw["width"],
        pixel_height=raw["height"],
        axis_aligned=_is_axis_aligned(tuple(raw["transform"])),
    )


def _page(document: Document, page_index: int) -> pymupdf.Page:
    if not 0 <= page_index < document.page_count:
        raise OpValidationError(f"page_index {page_index} is out of range (document has {document.page_count} pages)")
    return document.raw[page_index]


def _placements(page: pymupdf.Page) -> list[dict[str, Any]]:
    return list(page.get_image_info(xrefs=True))


def _placement_at(document: Document, page_index: int, index: int) -> tuple[pymupdf.Page, dict[str, Any]]:
    page = _page(document, page_index)
    placements = _placements(page)
    if not 0 <= index < len(placements):
        raise OpValidationError(f"page {page_index} has {len(placements)} image(s); index {index} is out of range")
    return page, placements[index]


def _validate_rect(page: pymupdf.Page, rect: Rect) -> pymupdf.Rect:
    area = pymupdf.Rect(rect)
    if area.is_empty or area.is_infinite:
        raise OpValidationError(f"image rectangle {rect} is empty")
    if not area.intersects(page.rect):
        raise OpValidationError(f"image rectangle {rect} lies entirely outside the page")
    return area


def list_images(document: Document, page_index: int) -> list[ImageInfo]:
    return [_info(index, raw) for index, raw in enumerate(_placements(_page(document, page_index)))]


def _pin_point(target: pymupdf.Rect, others: list[pymupdf.Rect]) -> pymupdf.Point | None:
    """A point inside `target` and outside every rect in `others`, if any."""
    for i in range(_PIN_GRID):
        for j in range(_PIN_GRID):
            point = pymupdf.Point(
                target.x0 + target.width * (i + 0.5) / _PIN_GRID,
                target.y0 + target.height * (j + 0.5) / _PIN_GRID,
            )
            if not any(point in other for other in others):
                return point
    return None


def _remove_placement(document: Document, page: pymupdf.Page, index: int) -> pymupdf.Page:
    placements = _placements(page)
    target = pymupdf.Rect(placements[index]["bbox"])
    others = [pymupdf.Rect(raw["bbox"]) for i, raw in enumerate(placements) if i != index]
    point = _pin_point(target, others)
    if point is None:
        raise OpValidationError("this image is entirely covered by other images and can't be removed on its own")
    page.add_redact_annot(pymupdf.Rect(point.x, point.y, point.x + 1, point.y + 1))
    page.apply_redactions(**_REMOVE_IMAGES_ONLY)
    return _reload(document, page)


def _reload(document: Document, page: pymupdf.Page) -> pymupdf.Page:
    """Like links, a page's image list is cached until the page is reloaded
    (confirmed against pymupdf 1.28.2)."""
    return document.raw.reload_page(page)


def _placement_bytes(document: Document, raw: dict[str, Any]) -> bytes:
    """The placement's image as PNG bytes, alpha (SMask) included."""
    xref = raw.get("xref", 0)
    if not xref:
        raise OpValidationError("inline images can't be reused or cropped; replace or delete it instead")
    pixmap = pymupdf.Pixmap(document.raw, xref)
    smask = document.raw.xref_get_key(xref, "SMask")
    if smask[0] == "xref":
        pixmap = pymupdf.Pixmap(pixmap, pymupdf.Pixmap(document.raw, int(smask[1].split()[0])))
    if pixmap.colorspace and pixmap.colorspace.n not in (1, 3):  # CMYK etc.: PNG needs gray or RGB
        pixmap = pymupdf.Pixmap(pymupdf.csRGB, pixmap)
    data: bytes = pixmap.tobytes("png")
    return data


def _new_index(document: Document, page_index: int, before: list[ImageInfo]) -> ImageInfo:
    """The placement that wasn't there before (placement order isn't the
    order they were added in once other images already exist)."""
    seen = {(info.xref, info.rect) for info in before}
    after = list_images(document, page_index)
    fresh = [info for info in after if (info.xref, info.rect) not in seen]
    return fresh[-1] if fresh else after[-1]


def insert_image(
    document: Document, page_index: int, rect: Rect, data_base64: str, *, keep_proportion: bool = True
) -> ImageInfo:
    page = _page(document, page_index)
    area = _validate_rect(page, rect)
    stream = decode_image(data_base64)
    before = list_images(document, page_index)
    page.insert_image(_fit(area, stream) if keep_proportion else area, stream=stream, keep_proportion=False)
    _reload(document, page)
    return _new_index(document, page_index, before)


def delete_image(document: Document, page_index: int, index: int) -> ImageInfo:
    page, raw = _placement_at(document, page_index, index)
    removed = _info(index, raw)
    _remove_placement(document, page, index)
    return removed


def move_image(document: Document, page_index: int, index: int, rect: Rect) -> ImageInfo:
    """Move and/or resize one placement to exactly `rect` (its aspect ratio
    follows `rect`), drawing the same image object again rather than a copy."""
    page, raw = _placement_at(document, page_index, index)
    area = _validate_rect(page, rect)
    xref = raw.get("xref", 0)
    stream = None if xref else _placement_bytes(document, raw)
    page = _remove_placement(document, page, index)
    before = list_images(document, page_index)
    if xref:
        page.insert_image(area, xref=xref, keep_proportion=False)
    else:
        page.insert_image(area, stream=stream, keep_proportion=False)
    _reload(document, page)
    return _new_index(document, page_index, before)


def replace_image(document: Document, page_index: int, index: int, data_base64: str) -> ImageInfo:
    """Draw a new image in this placement's rectangle, fitted inside it with
    its own aspect ratio kept; other placements of the old image are untouched."""
    stream = decode_image(data_base64)
    page, raw = _placement_at(document, page_index, index)
    area = pymupdf.Rect(raw["bbox"])
    page = _remove_placement(document, page, index)
    before = list_images(document, page_index)
    page.insert_image(_fit(area, stream), stream=stream, keep_proportion=False)
    _reload(document, page)
    return _new_index(document, page_index, before)


def crop_image(document: Document, page_index: int, index: int, rect: Rect) -> ImageInfo:
    """Keep only the part of the image inside `rect` (page points, within
    the placement), drawn exactly where it was -- the image's pixels are
    really cut, not just clipped from view."""
    page, raw = _placement_at(document, page_index, index)
    if not _is_axis_aligned(tuple(raw["transform"])):
        raise OpValidationError("cropping a rotated or skewed image is not supported")
    bbox = pymupdf.Rect(raw["bbox"])
    crop = pymupdf.Rect(rect) & bbox
    if crop.is_empty:
        raise OpValidationError(f"crop rectangle {rect} doesn't overlap the image at {raw['bbox']}")

    with Image.open(io.BytesIO(_placement_bytes(document, raw))) as image:
        sx, sy = image.width / bbox.width, image.height / bbox.height
        box = (
            round((crop.x0 - bbox.x0) * sx),
            round((crop.y0 - bbox.y0) * sy),
            max(round((crop.x1 - bbox.x0) * sx), round((crop.x0 - bbox.x0) * sx) + 1),
            max(round((crop.y1 - bbox.y0) * sy), round((crop.y0 - bbox.y0) * sy) + 1),
        )
        out = io.BytesIO()
        image.crop(box).save(out, format="PNG")

    page = _remove_placement(document, page, index)
    before = list_images(document, page_index)
    page.insert_image(crop, stream=out.getvalue(), keep_proportion=False)
    _reload(document, page)
    return _new_index(document, page_index, before)
