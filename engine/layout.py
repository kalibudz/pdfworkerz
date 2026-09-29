"""P5 slice 2, page geometry (ORG-09..12): crop, resize, N-up and booklet imposition.

Crop changes a page's visible area (its crop box) and nothing else: the content outside is
hidden, not removed, so it can be uncropped. Resize, N-up and booklet build new pages that
show the old ones scaled into place (``show_pdf_page``): the text stays text, drawn inside a
Form XObject, which the editor already edits. Bookmarks are kept (resize keeps links too);
what can't be carried onto a rebuilt page -- annotations and form fields, and links on imposed
sheets -- is refused unless the caller chooses to drop it, never lost silently.

Coordinates are PDF points in the unrotated page, top-left origin (measured: pymupdf 1.28.2's
``set_cropbox`` takes them that way).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

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


def _source_copy(document: Document) -> pymupdf.Document:
    return pymupdf.open(stream=plain_bytes(document.raw), filetype="pdf")


def _interactive(document: Document, pages: Iterable[int], *, links: bool) -> dict[str, int]:
    """What placing these pages onto new ones can't carry over: annotations and form fields
    (and, when `links`, links too), counted per kind."""
    counts = {"annotation": 0, "form field": 0, "link": 0}
    for index in pages:
        page = document.raw[index]
        counts["annotation"] += len(list(page.annots()))
        counts["form field"] += len(list(page.widgets()))
        if links:
            counts["link"] += len(page.get_links())
    return {kind: n for kind, n in counts.items() if n}


def _refuse_dropping(action: str, counts: dict[str, int], drop_interactive: bool) -> None:
    if counts and not drop_interactive:
        found = ", ".join(f"{n} {kind}{'s' if n > 1 else ''}" for kind, n in counts.items())
        raise OpValidationError(
            f"{action} can't carry over {found}: flatten annotations first, or drop them "
            '(drop_interactive; in the command bar, add "dropping annotations")'
        )


def _placement(old: pymupdf.Rect, target: pymupdf.Rect) -> pymupdf.Matrix:
    """The matrix show_pdf_page(keep_proportion=True) uses to put `old` into `target`."""
    scale = min(target.width / old.width, target.height / old.height)
    dx = target.x0 + (target.width - old.width * scale) / 2 - old.x0 * scale
    dy = target.y0 + (target.height - old.height * scale) / 2 - old.y0 * scale
    return pymupdf.Matrix(scale, 0, 0, scale, dx, dy)


def _link_key(link: dict[str, Any]) -> tuple[Any, ...]:
    return (link.get("kind"), tuple(round(v, 1) for v in link["from"]), link.get("page"), link.get("uri"))


def resize(
    document: Document,
    pages: list[int],
    size: str | tuple[float, float],
    *,
    scale: bool = True,
    drop_interactive: bool = False,
) -> None:
    """ORG-10: give pages a new size (a named size such as "a4" or "letter-landscape", or
    (width, height) points). With `scale`, content is scaled to fit and centered; without,
    it keeps its size, centered on the new page (cropped if the page got smaller).

    Only the chosen pages are rebuilt. The outline and every link are kept (links on a resized
    page move and scale with its content). Annotations and form fields on a resized page can't
    be carried over, so the resize is refused unless `drop_interactive`."""
    _check_indices(document, pages)
    width, height = _size(size)
    chosen = sorted(set(pages))
    rotated = [i for i in chosen if document.raw[i].rotation and document.raw[i].get_links()]
    counts = _interactive(document, chosen, links=False)
    if rotated:
        counts["link on a rotated page"] = sum(len(document.raw[i].get_links()) for i in rotated)
    _refuse_dropping("resizing those pages", counts, drop_interactive)

    toc = document.raw.get_toc(simple=False)
    saved_links = [document.raw[i].get_links() for i in range(document.page_count)]
    matrices: dict[int, pymupdf.Matrix] = {}
    source = _source_copy(document)
    try:
        for index in chosen:
            old = source[index].rect
            page = document.raw.new_page(index, width=width, height=height)
            if scale:
                target = page.rect
                page.show_pdf_page(target, source, index, keep_proportion=True)
                matrix = _placement(old, target)
            else:
                x0, y0 = (width - old.width) / 2, (height - old.height) / 2
                target = pymupdf.Rect(x0, y0, x0 + old.width, y0 + old.height)
                page.show_pdf_page(target & page.rect, source, index, clip=_clip_for(target, page.rect, old))
                matrix = pymupdf.Matrix(1, 0, 0, 1, x0, y0)
            document.raw.delete_page(index + 1)
            matrices[index] = matrix
    finally:
        source.close()
    # Deleting old pages also removed the bookmarks and links that pointed at them -- including
    # links on pages rebuilt earlier in the loop -- so every link is restored only once all
    # pages are rebuilt: moved and scaled on resized pages, as they were everywhere else.
    document.raw.set_toc(toc)
    for index in range(document.page_count):
        if index in rotated:
            continue
        page = document.raw[index]
        present = {_link_key(link) for link in page.get_links()}
        for link in saved_links[index]:
            if index in matrices:
                moved = (pymupdf.Rect(link["from"]) * matrices[index]) & page.rect
                if moved.is_empty:
                    continue
                link = {**link, "from": moved}
            if _link_key(link) not in present:
                page.insert_link(link)


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
    if gap < 0:
        raise OpValidationError("the gap between pages can't be negative")
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


def _replace_with_sheets(
    document: Document,
    order: list[int | None],
    cols: int,
    rows: int,
    sheet: str | tuple[float, float],
    gap: float,
    drop_interactive: bool,
    action: str,
) -> int:
    """Swap every page for imposed sheets, keeping this Document (and its encryption); each
    bookmark then points at the sheet its page landed on."""
    _refuse_dropping(action, _interactive(document, range(document.page_count), links=True), drop_interactive)
    per_sheet = cols * rows
    sheet_of = {page: slot // per_sheet for slot, page in enumerate(order) if page is not None}
    toc = [
        [level, title, sheet_of[page - 1] + 1 if page >= 1 else page]
        for level, title, page, *_ in document.raw.get_toc()
    ]
    source = _source_copy(document)
    try:
        built = _impose(source, order, cols, rows, _size(sheet), gap)
    finally:
        source.close()
    try:
        old = document.page_count
        document.raw.insert_pdf(built, start_at=0)
        document.raw.delete_pages(built.page_count, built.page_count + old - 1)
        document.raw.set_toc(toc)
        return built.page_count
    finally:
        built.close()


def n_up(
    document: Document,
    *,
    cols: int,
    rows: int,
    sheet: str | tuple[float, float] = "a4-landscape",
    gap: float = 12.0,
    drop_interactive: bool = False,
) -> int:
    """ORG-11: put `cols` x `rows` pages on each sheet, in reading order. Returns sheets made.
    Bookmarks follow their pages onto the sheets; links, annotations and form fields can't, so
    they are refused unless `drop_interactive`."""
    if not (1 <= cols <= 8 and 1 <= rows <= 8) or cols * rows < 2:
        raise OpValidationError("N-up needs 2 to 64 pages per sheet (1-8 columns and rows)")
    order: list[int | None] = list(range(document.page_count))
    return _replace_with_sheets(document, order, cols, rows, sheet, gap, drop_interactive, "N-up")


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


def booklet(
    document: Document,
    *,
    sheet: str | tuple[float, float] = "a4-landscape",
    gap: float = 0.0,
    drop_interactive: bool = False,
) -> int:
    """ORG-12: impose for a folded booklet -- two pages side by side per sheet side, in saddle-
    stitch order, so printing double-sided and folding gives the pages in sequence. Bookmarks
    follow their pages; links, annotations and form fields are refused unless `drop_interactive`."""
    order = booklet_order(document.page_count)
    return _replace_with_sheets(document, order, 2, 1, sheet, gap, drop_interactive, "a booklet")
