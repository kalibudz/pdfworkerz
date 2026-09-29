"""P5 slice 2, page geometry (ORG-09..12): crop, resize, N-up and booklet imposition.

Crop changes a page's visible area (its crop box) and nothing else: the content outside is
hidden, not removed, so it can be uncropped. Resize, N-up and booklet build new pages that
show the old ones scaled into place (``show_pdf_page``): the text stays text, drawn inside a
Form XObject, which the editor already edits. Links and annotations of rebuilt pages are not
carried over -- a documented limitation of placing pages this way.

Coordinates are PDF points in the unrotated page, top-left origin (measured: pymupdf 1.28.2's
``set_cropbox`` takes them that way).
"""

from __future__ import annotations

from collections.abc import Iterable

import pymupdf

from engine.document import Document
from engine.errors import OpValidationError
from engine.pages import PAGE_SIZES, _check_indices
from engine.pdfbytes import plain_bytes

Rect = tuple[float, float, float, float]


def crop(document: Document, pages: list[int], *, box: Rect | None = None, margins: Rect | None = None) -> None:
    """ORG-09: show only `box` (x0, y0, x1, y1) of each page, or trim `margins` (left, top,
    right, bottom) off each page's current visible area. Content outside stays in the file."""
    if (box is None) == (margins is None):
        raise OpValidationError("crop with either a box or margins")
    _check_indices(document, pages)
    for index in pages:
        page = document.raw[index]
        media = page.mediabox
        if box is not None:
            area = pymupdf.Rect(box)
        else:
            left, top, right, bottom = margins or (0, 0, 0, 0)
            if min(left, top, right, bottom) < 0:
                raise OpValidationError("margins can't be negative")
            current = page.cropbox
            area = pymupdf.Rect(current.x0 + left, current.y0 + top, current.x1 - right, current.y1 - bottom)
        area = area & media
        if area.is_empty or area.width < 10 or area.height < 10:
            raise OpValidationError(f"that would leave page {index + 1} less than 10 points wide or tall")
        page.set_cropbox(area)


def uncrop(document: Document, pages: list[int]) -> None:
    """ORG-09: show each page's whole media area again."""
    _check_indices(document, pages)
    for index in pages:
        page = document.raw[index]
        page.set_cropbox(page.mediabox)


def _size(name_or_size: str | tuple[float, float]) -> tuple[float, float]:
    if isinstance(name_or_size, tuple):
        width, height = name_or_size
    else:
        key = name_or_size.lower()
        landscape = key.endswith("-landscape")
        base = key.removesuffix("-landscape")
        if base not in PAGE_SIZES:
            raise OpValidationError(f"unknown page size {name_or_size!r}: use {', '.join(PAGE_SIZES)} or custom points")
        width, height = PAGE_SIZES[base]
        if landscape:
            width, height = height, width
    if not (36 <= width <= 14400 and 36 <= height <= 14400):
        raise OpValidationError("page sizes must be between 36 and 14400 points")
    return width, height


def _replace_all_pages(document: Document, built: pymupdf.Document) -> None:
    """Swap every page for the pages of `built`, keeping this Document (and its encryption)."""
    old = document.page_count
    document.raw.insert_pdf(built, start_at=0)
    document.raw.delete_pages(built.page_count, built.page_count + old - 1)


def _source_copy(document: Document) -> pymupdf.Document:
    return pymupdf.open(stream=plain_bytes(document.raw), filetype="pdf")


def resize(document: Document, pages: list[int], size: str | tuple[float, float], *, scale: bool = True) -> None:
    """ORG-10: give pages a new size (a named size such as "a4" or "letter-landscape", or
    (width, height) points). With `scale`, content is scaled to fit and centered; without,
    it keeps its size, centered on the new page (cropped if the page got smaller)."""
    _check_indices(document, pages)
    width, height = _size(size)
    source = _source_copy(document)
    built = pymupdf.open()
    try:
        chosen = set(pages)
        for index in range(document.page_count):
            if index not in chosen:
                built.insert_pdf(source, from_page=index, to_page=index)
                continue
            old = source[index].rect
            page = built.new_page(width=width, height=height)
            if scale:
                page.show_pdf_page(page.rect, source, index, keep_proportion=True)
            else:
                x0, y0 = (width - old.width) / 2, (height - old.height) / 2
                target = pymupdf.Rect(x0, y0, x0 + old.width, y0 + old.height)
                page.show_pdf_page(target & page.rect, source, index, clip=_clip_for(target, page.rect, old))
        _replace_all_pages(document, built)
    finally:
        built.close()
        source.close()


def _clip_for(target: pymupdf.Rect, page: pymupdf.Rect, old: pymupdf.Rect) -> pymupdf.Rect:
    """The part of the old page that is still visible when centered on a smaller new page."""
    visible = target & page
    return pymupdf.Rect(
        visible.x0 - target.x0,
        visible.y0 - target.y0,
        old.width - (target.x1 - visible.x1),
        old.height - (target.y1 - visible.y1),
    )


def _impose(
    source: pymupdf.Document, order: Iterable[int | None], cols: int, rows: int, sheet: tuple[float, float], gap: float
) -> pymupdf.Document:
    """Place pages (None = leave the slot empty) cols x rows per sheet, left to right, top to bottom."""
    width, height = sheet
    cell_w = (width - gap * (cols + 1)) / cols
    cell_h = (height - gap * (rows + 1)) / rows
    if cell_w < 20 or cell_h < 20:
        raise OpValidationError("that many pages per sheet leaves no room")
    built = pymupdf.open()
    slots = list(order)
    per_sheet = cols * rows
    for start in range(0, len(slots), per_sheet):
        page = built.new_page(width=width, height=height)
        for slot, index in enumerate(slots[start : start + per_sheet]):
            if index is None:
                continue
            col, row = slot % cols, slot // cols
            x0 = gap + col * (cell_w + gap)
            y0 = gap + row * (cell_h + gap)
            page.show_pdf_page(pymupdf.Rect(x0, y0, x0 + cell_w, y0 + cell_h), source, index, keep_proportion=True)
    return built


def n_up(
    document: Document, *, cols: int, rows: int, sheet: str | tuple[float, float] = "a4-landscape", gap: float = 12.0
) -> int:
    """ORG-11: put `cols` x `rows` pages on each sheet, in reading order. Returns sheets made."""
    if not (1 <= cols <= 8 and 1 <= rows <= 8) or cols * rows < 2:
        raise OpValidationError("N-up needs 2 to 64 pages per sheet (1-8 columns and rows)")
    source = _source_copy(document)
    try:
        built = _impose(source, range(document.page_count), cols, rows, _size(sheet), gap)
        try:
            _replace_all_pages(document, built)
            return built.page_count
        finally:
            built.close()
    finally:
        source.close()


def booklet_order(page_count: int) -> list[int | None]:
    """The saddle-stitch order: pages padded with blanks to a multiple of 4, then for each sheet
    (front: last, first; back: second, second-last), inward. None is a blank slot."""
    total = -(-page_count // 4) * 4
    pages: list[int | None] = [i if i < page_count else None for i in range(total)]
    order: list[int | None] = []
    low, high = 0, total - 1
    while low < high:
        order += [pages[high], pages[low], pages[low + 1], pages[high - 1]]
        low, high = low + 2, high - 2
    return order


def booklet(document: Document, *, sheet: str | tuple[float, float] = "a4-landscape", gap: float = 0.0) -> int:
    """ORG-12: impose for a folded booklet -- two pages side by side per sheet side, in saddle-
    stitch order, so printing double-sided and folding gives the pages in sequence."""
    source = _source_copy(document)
    try:
        built = _impose(source, booklet_order(document.page_count), 2, 1, _size(sheet), gap)
        try:
            _replace_all_pages(document, built)
            return built.page_count
        finally:
            built.close()
    finally:
        source.close()
