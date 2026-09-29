"""P5 slice 3, page design (DES-01..06): page numbers, Bates numbers, headers and footers,
watermarks, backgrounds and stamps.

Everything here is added as ordinary page content (text stays real, searchable, editable
text), except the preset stamps, which are standard rubber-stamp annotations so they can be
moved or deleted like any annotation. Positions are in the page as the reader sees it:
rotated and cropped pages get their text upright and inside the visible area (measured on
pymupdf 1.28.2: convert the visible point with ``page.derotation_matrix`` and draw with
``morph=(point, Matrix(+page.rotation))``).
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pymupdf

from engine.document import Document
from engine.errors import OpValidationError
from engine.pages import _check_indices

POSITIONS = (
    "top-left",
    "top-center",
    "top-right",
    "center",
    "bottom-left",
    "bottom-center",
    "bottom-right",
)
# Base-14 fonts only: always available, never embedded, so nothing here can fail on a missing font.
FONTS = {"helvetica": "helv", "helvetica-bold": "hebo", "times": "tiro", "times-bold": "tibo", "courier": "cour"}
COLORS = {
    "black": (0.0, 0.0, 0.0),
    "gray": (0.5, 0.5, 0.5),
    "grey": (0.5, 0.5, 0.5),
    "red": (0.8, 0.0, 0.0),
    "green": (0.0, 0.5, 0.0),
    "blue": (0.0, 0.2, 0.8),
}
Color = tuple[float, float, float]


def _color(value: str | Color) -> Color:
    if isinstance(value, tuple):
        if len(value) != 3 or not all(0 <= c <= 1 for c in value):
            raise OpValidationError("colors are three numbers from 0 to 1")
        return value
    key = value.lower()
    if key in COLORS:
        return COLORS[key]
    if len(key) == 7 and key.startswith("#"):
        try:
            return tuple(int(key[i : i + 2], 16) / 255 for i in (1, 3, 5))  # type: ignore[return-value]
        except ValueError:
            pass
    raise OpValidationError(f"unknown color {value!r}: use {', '.join(COLORS)} or #rrggbb")


def _font(name: str) -> str:
    key = name.lower()
    if key not in FONTS:
        raise OpValidationError(f"unknown font {name!r}: use {', '.join(FONTS)}")
    return FONTS[key]


def _check_position(position: str) -> None:
    if position not in POSITIONS:
        raise OpValidationError(f"unknown position {position!r}: use {', '.join(POSITIONS)}")


def _pages(document: Document, pages: list[int] | None, skip: list[int] | None = None) -> list[int]:
    chosen = list(range(document.page_count)) if pages is None else pages
    _check_indices(document, chosen)
    if skip:
        _check_indices(document, skip)
        chosen = [p for p in chosen if p not in set(skip)]
    if not chosen:
        raise OpValidationError("that leaves no pages to change")
    return chosen


def _box_at(visible: pymupdf.Rect, width: float, height: float, position: str, margin: float) -> pymupdf.Rect:
    """A width x height box at a named position of the visible page, `margin` from its edges."""
    vertical, _, horizontal = position.partition("-") if position != "center" else ("center", "", "center")
    x = {"left": margin, "center": (visible.width - width) / 2, "right": visible.width - margin - width}[horizontal]
    y = {"top": margin, "center": (visible.height - height) / 2, "bottom": visible.height - margin - height}[vertical]
    return pymupdf.Rect(x, y, x + width, y + height)


def place_text(
    page: pymupdf.Page,
    text: str,
    position: str,
    *,
    margin: float = 36.0,
    size: float = 10.0,
    font: str = "helvetica",
    color: str | Color = "black",
    opacity: float = 1.0,
) -> None:
    """Draw one line of text at a named position of the visible page, upright to the reader."""
    _check_position(position)
    if not text:
        return
    if not 1 <= size <= 400:
        raise OpValidationError("text size must be 1 to 400 points")
    fontname = _font(font)
    visible = page.rect
    width = pymupdf.get_text_length(text, fontname=fontname, fontsize=size)
    point = _box_at(visible, width, size, position, margin).bl * page.derotation_matrix  # baseline start
    page.insert_text(
        point,
        text,
        fontsize=size,
        fontname=fontname,
        color=_color(color),
        fill_opacity=opacity,
        morph=(point, pymupdf.Matrix(page.rotation)) if page.rotation else None,
    )


def _format(template: str, **values: object) -> str:
    try:
        return template.format(**values)
    except (KeyError, IndexError, ValueError) as exc:
        names = ", ".join("{" + k + "}" for k in values)
        raise OpValidationError(f"can't use {template!r}: the placeholders are {names}") from exc


# -- DES-01 page numbers --


def page_numbers(
    document: Document,
    *,
    pages: list[int] | None = None,
    skip: list[int] | None = None,
    template: str = "{n}",
    start: int = 1,
    position: str = "bottom-center",
    size: float = 10.0,
    margin: float = 30.0,
    font: str = "helvetica",
    color: str | Color = "black",
) -> list[str]:
    """Number `pages` (default all) minus `skip`, counting from `start` on the first numbered
    page. `template` uses {n} (the number) and {total} (how many pages are numbered)."""
    chosen = _pages(document, pages, skip)
    total = len(chosen)
    labels = []
    for offset, index in enumerate(chosen):
        label = _format(template, n=start + offset, total=start + total - 1)
        place_text(document.raw[index], label, position, margin=margin, size=size, font=font, color=color)
        labels.append(label)
    return labels


# -- DES-02 Bates numbering --


def bates(
    document: Document,
    *,
    prefix: str = "",
    suffix: str = "",
    start: int = 1,
    digits: int = 6,
    position: str = "bottom-right",
    size: float = 9.0,
    margin: float = 20.0,
) -> tuple[str, str]:
    """Stamp every page with a sequential identifier (prefix + zero-padded number + suffix).
    Returns the first and last numbers, so the next document can continue from there."""
    if start < 0 or not 1 <= digits <= 12:
        raise OpValidationError("Bates numbers start at 0 or more, with 1 to 12 digits")
    last = start + document.page_count - 1
    if len(str(last)) > digits:
        raise OpValidationError(f"{digits} digits can't hold the number {last}")
    numbers = [f"{prefix}{n:0{digits}d}{suffix}" for n in range(start, last + 1)]
    for index, number in enumerate(numbers):
        place_text(document.raw[index], number, position, margin=margin, size=size, font="courier")
    return numbers[0], numbers[-1]


# -- DES-03 headers and footers --


def header_footer(
    document: Document,
    *,
    header: tuple[str, str, str] = ("", "", ""),
    footer: tuple[str, str, str] = ("", "", ""),
    pages: list[int] | None = None,
    skip: list[int] | None = None,
    size: float = 9.0,
    margin: float = 30.0,
    font: str = "helvetica",
    color: str | Color = "black",
    date: str | None = None,
) -> int:
    """Left, center and right header and footer text. Placeholders: {page}, {total}, {title},
    {file} and {date} (today, as YYYY-MM-DD, unless `date` is given). Returns pages changed."""
    if not any(header) and not any(footer):
        raise OpValidationError("give some header or footer text")
    chosen = _pages(document, pages, skip)
    values = {
        "total": document.page_count,
        "title": document.raw.metadata.get("title", "") if document.raw.metadata else "",
        "file": Path(document.raw.name).name if document.raw.name else "",
        "date": date or _dt.date.today().isoformat(),
    }
    for index in chosen:
        page = document.raw[index]
        for row, texts in (("top", header), ("bottom", footer)):
            for column, template in zip(("left", "center", "right"), texts, strict=True):
                text = _format(template, page=index + 1, **values)
                place_text(page, text, f"{row}-{column}", margin=margin, size=size, font=font, color=color)
    return len(chosen)


# -- DES-04 watermarks --


def watermark(
    document: Document,
    *,
    text: str | None = None,
    image: str | None = None,
    pages: list[int] | None = None,
    size: float = 60.0,
    color: str | Color = "gray",
    opacity: float = 0.3,
    rotation: float = 45.0,
    behind: bool = False,
    font: str = "helvetica-bold",
    scale: float = 0.5,
) -> int:
    """A text or image watermark centered on each page, `opacity` 0-1, text turned `rotation`
    degrees, over the content or `behind` it. An image fills `scale` of the page width."""
    if (text is None) == (image is None):
        raise OpValidationError("watermark with either text or an image")
    if not 0 < opacity <= 1:
        raise OpValidationError("opacity is more than 0, up to 1")
    chosen = _pages(document, pages)
    pixmap = _faded_image(image, opacity) if image is not None else None
    label = text or ""
    for index in chosen:
        page = document.raw[index]
        visible = page.rect
        if pixmap is not None:
            if not 0.05 <= scale <= 1:
                raise OpValidationError("image scale is 0.05 to 1 of the page width")
            width = visible.width * scale
            height = width * pixmap.height / pixmap.width
            box = _box_at(visible, width, height, "center", 0)
            page.insert_image(box * page.derotation_matrix, pixmap=pixmap, overlay=not behind, rotate=page.rotation)
            continue
        fontname = _font(font)
        width = pymupdf.get_text_length(label, fontname=fontname, fontsize=size)
        center = pymupdf.Point(visible.width / 2, visible.height / 2)
        start = pymupdf.Point(center.x - width / 2, center.y + size / 3)
        # Upright to the reader and tilted about the page center: move the start point by the tilt
        # first, then turn the text about that point (morph can't carry a translation). morph turns
        # the opposite way to point arithmetic (measured); positive `rotation` is counterclockwise.
        c_u = center * page.derotation_matrix
        s_u = (start * page.derotation_matrix - c_u) * pymupdf.Matrix(-rotation) + c_u
        turn = pymupdf.Matrix(page.rotation) * pymupdf.Matrix(rotation)
        page.insert_text(
            s_u,
            label,
            fontsize=size,
            fontname=fontname,
            color=_color(color),
            fill_opacity=opacity,
            overlay=not behind,
            morph=(s_u, turn) if (rotation or page.rotation) else None,
        )
    return len(chosen)


def _faded_image(path: str, opacity: float) -> pymupdf.Pixmap:
    source = Path(path)
    if not source.is_file():
        raise OpValidationError(f"image {path!r} does not exist")
    try:
        pixmap = pymupdf.Pixmap(str(source))
    except Exception as exc:  # pymupdf raises plain RuntimeError/ValueError for unreadable images
        raise OpValidationError(f"can't read image {path!r}: {exc}") from exc
    if pixmap.colorspace and pixmap.colorspace.n != 3:
        pixmap = pymupdf.Pixmap(pymupdf.csRGB, pixmap)
    if not pixmap.alpha:
        pixmap = pymupdf.Pixmap(pixmap, 1)
    alphas = bytes(round(a * opacity) for a in pixmap.samples[pixmap.n - 1 :: pixmap.n])
    pixmap.set_alpha(alphas)
    return pixmap


# -- DES-05 backgrounds --


def background(
    document: Document,
    *,
    color: str | Color | None = None,
    image: str | None = None,
    pages: list[int] | None = None,
) -> int:
    """Fill each page's background (behind all content) with a color or a stretched image."""
    if (color is None) == (image is None):
        raise OpValidationError("background with either a color or an image")
    chosen = _pages(document, pages)
    if image is not None and not Path(image).is_file():
        raise OpValidationError(f"image {image!r} does not exist")
    for index in chosen:
        page = document.raw[index]
        area = page.rect * page.derotation_matrix  # the visible area, in unrotated coordinates
        if color is not None:
            page.draw_rect(area, color=None, fill=_color(color), overlay=False)
        else:
            page.insert_image(area, filename=image, overlay=False, keep_proportion=False)
    return len(chosen)


# -- DES-06 stamps --

STAMPS = {
    "approved": pymupdf.STAMP_Approved,
    "not approved": pymupdf.STAMP_NotApproved,
    "draft": pymupdf.STAMP_Draft,
    "final": pymupdf.STAMP_Final,
    "confidential": pymupdf.STAMP_Confidential,
    "for comment": pymupdf.STAMP_ForComment,
    "expired": pymupdf.STAMP_Expired,
    "top secret": pymupdf.STAMP_TopSecret,
}


def stamp(
    document: Document,
    *,
    name: str = "approved",
    text: str | None = None,
    pages: list[int] | None = None,
    position: str = "top-right",
    color: str | Color = "red",
    width: float = 180.0,
    margin: float = 36.0,
) -> int:
    """A preset rubber stamp (a standard stamp annotation: movable, deletable) or, with `text`,
    a custom one drawn as a bordered box. Returns pages stamped."""
    _check_position(position)
    if text is None and name.lower() not in STAMPS:
        raise OpValidationError(f"unknown stamp {name!r}: use {', '.join(STAMPS)}, or give custom text")
    if not 40 <= width <= 600:
        raise OpValidationError("stamp width is 40 to 600 points")
    height = width / 3.5
    chosen = _pages(document, pages)
    for index in chosen:
        page = document.raw[index]
        visible = page.rect
        box = _box_at(visible, width, height, position, margin)
        if text is None:
            page.add_stamp_annot(box * page.derotation_matrix, stamp=STAMPS[name.lower()])
            continue
        rgb = _color(color)
        page.draw_rect(box * page.derotation_matrix, color=rgb, width=3, radius=0.15)
        size = min(height * 0.5, (width - 16) / max(1.0, pymupdf.get_text_length(text, "hebo", 1)))
        place_text_in_box(page, text, box, size=size, color=rgb)
    return len(chosen)


def place_text_in_box(page: pymupdf.Page, text: str, box: pymupdf.Rect, *, size: float, color: Color) -> None:
    """Center one line of bold text in a box given in visible coordinates."""
    width = pymupdf.get_text_length(text, fontname="hebo", fontsize=size)
    point = pymupdf.Point(box.x0 + (box.width - width) / 2, box.y0 + (box.height + size * 0.7) / 2)
    point = point * page.derotation_matrix
    page.insert_text(
        point,
        text,
        fontsize=size,
        fontname="hebo",
        color=color,
        morph=(point, pymupdf.Matrix(page.rotation)) if page.rotation else None,
    )
