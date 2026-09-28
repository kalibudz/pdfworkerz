"""EDT-09: draw and edit vector shapes and lines.

Existing vector art is addressed by its position in ``Page.get_drawings()``
(``index``) -- one entry per path MuPDF paints, only valid against the
page's current state.

Drawing new shapes is plain ``Page.new_shape()``. Removing one existing
path is the hard part: PDF has no object for "a shape", only operators in
a content stream, and MuPDF's line-art redaction removes *every* path
fully inside the redaction area, not just one. So a path is removed by
redacting its own area (graphics only -- text and images are untouched),
then diffing the page's drawings before and after: any *other* path that
disappeared with it (a small shape inside a big box being deleted) is
drawn back from its own path data. If the target somehow survives, the
operation raises; the undo journal (engine.ops.journal) rolls the whole
document back when an Op raises, so nothing is left half-done.

Editing (move, resize, restyle) is "remove, then redraw from the same path
data with changes". A redrawn path is geometrically and visually the same
path, but as fresh operators: exotic features MuPDF doesn't report back
(blend modes, patterns/shadings as paint, optional-content membership) are
not carried over.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import Any, Literal

import pymupdf
from pydantic import BaseModel, ConfigDict

from engine.document import Document
from engine.errors import OpValidationError
from engine.geometry import page_bounds

Color = tuple[float, float, float]
Point = tuple[float, float]
Rect = tuple[float, float, float, float]
ShapeKind = Literal["line", "rect", "ellipse", "polyline", "polygon"]

# Verified at runtime (pymupdf 1.28.2); missing from pymupdf's stub, like engine.edit's.
_REMOVE_LINE_ART_ONLY: dict[str, int] = {
    "images": pymupdf.PDF_REDACT_IMAGE_NONE,  # type: ignore[attr-defined]
    "graphics": pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,  # type: ignore[attr-defined]
    "text": pymupdf.PDF_REDACT_TEXT_NONE,  # type: ignore[attr-defined]
}
# MuPDF's "fully covered" test is conservative: it allows for joins at the
# default miter limit (10), so a path is only removed once the redaction
# reaches ~10x its stroke width past its bbox (confirmed against pymupdf
# 1.28.2: 1pt line or circle -> 10-15pt, 3pt line -> 30pt).
_MITER_LIMIT = 10.0
_COVER_SLACK = 5.0


class ShapeInfo(BaseModel):
    """One vector path on a page."""

    model_config = ConfigDict(frozen=True)

    index: int
    rect: Rect
    kind: Literal["line", "rect", "curve", "path"]
    stroke_color: Color | None
    fill_color: Color | None
    line_width: float | None


def _kind(path: dict[str, Any]) -> Literal["line", "rect", "curve", "path"]:
    ops = {item[0] for item in path["items"]}
    if ops == {"re"} and len(path["items"]) == 1:
        return "rect"
    if ops == {"l"}:
        return "line"
    if "c" in ops:
        return "curve"
    return "path"


def _info(index: int, path: dict[str, Any]) -> ShapeInfo:
    stroked = "s" in path["type"]
    return ShapeInfo(
        index=index,
        rect=tuple(path["rect"]),
        kind=_kind(path),
        stroke_color=tuple(path["color"]) if stroked and path.get("color") else None,
        fill_color=tuple(path["fill"]) if path.get("fill") else None,
        line_width=path.get("width") if stroked else None,
    )


def _page(document: Document, page_index: int) -> pymupdf.Page:
    if not 0 <= page_index < document.page_count:
        raise OpValidationError(f"page_index {page_index} is out of range (document has {document.page_count} pages)")
    return document.raw[page_index]


def _drawings(page: pymupdf.Page) -> list[dict[str, Any]]:
    return list(page.get_drawings())


def _drawing_at(document: Document, page_index: int, index: int) -> tuple[pymupdf.Page, list[dict[str, Any]]]:
    page = _page(document, page_index)
    drawings = _drawings(page)
    if not 0 <= index < len(drawings):
        raise OpValidationError(f"page {page_index} has {len(drawings)} shape(s); index {index} is out of range")
    return page, drawings


def _reload(document: Document, page: pymupdf.Page) -> pymupdf.Page:
    """A page's drawing list, like its links and images, is cached until the
    page is reloaded (confirmed against pymupdf 1.28.2)."""
    return document.raw.reload_page(page)


def list_shapes(document: Document, page_index: int) -> list[ShapeInfo]:
    return [_info(index, path) for index, path in enumerate(_drawings(_page(document, page_index)))]


def _signature(path: dict[str, Any]) -> tuple[Any, ...]:
    """What identifies a path across a before/after comparison of one page."""

    def rounded(value: Any) -> Any:
        if isinstance(value, (pymupdf.Point, pymupdf.Rect, pymupdf.Quad)):
            value = tuple(value)  # type: ignore[arg-type]  # sequences at runtime; pymupdf's stubs don't say so
        if isinstance(value, (tuple, list)):
            return tuple(rounded(v) for v in value)
        if isinstance(value, float):
            return round(value, 2)
        return value

    return (
        path["type"],
        rounded([tuple(item) for item in path["items"]]),
        rounded(path.get("color")),
        rounded(path.get("fill")),
        rounded(path.get("width")),
    )


def _validate_color(color: Color | None, what: str) -> Color | None:
    if color is not None and not all(0.0 <= c <= 1.0 for c in color):
        raise OpValidationError(f"{what} {color} must have every channel between 0 and 1")
    return color


def _line_cap(path: dict[str, Any]) -> int:
    cap = path.get("lineCap") or 0
    return int(max(cap)) if isinstance(cap, (tuple, list)) else int(cap)


def _draw_path(
    page: pymupdf.Page,
    path: dict[str, Any],
    *,
    transform: Callable[[pymupdf.Point], pymupdf.Point] | None = None,
    stroke_color: Color | None = None,
    fill_color: Color | None = None,
    no_fill: bool = False,
    line_width: float | None = None,
) -> None:
    """Draw `path` (a get_drawings() entry) again, optionally transformed
    and/or restyled."""

    def at(point: Any) -> pymupdf.Point:
        p = pymupdf.Point(point)
        return transform(p) if transform else p

    shape = page.new_shape()
    for item in path["items"]:
        op = item[0]
        if op == "l":
            shape.draw_line(at(item[1]), at(item[2]))
        elif op == "c":
            shape.draw_bezier(at(item[1]), at(item[2]), at(item[3]), at(item[4]))
        elif op == "re":
            rect = pymupdf.Rect(item[1])
            shape.draw_rect(pymupdf.Rect(at(rect.tl), at(rect.br)))
        elif op == "qu":
            quad = pymupdf.Quad(item[1])
            shape.draw_quad(pymupdf.Quad(at(quad.ul), at(quad.ur), at(quad.ll), at(quad.lr)))
    stroked = "s" in path["type"]
    color = stroke_color if stroke_color is not None else (path.get("color") if stroked else None)
    fill = None if no_fill else (fill_color if fill_color is not None else path.get("fill"))
    shape.finish(
        color=color,
        fill=fill,
        width=line_width if line_width is not None else (path.get("width") or 1.0),
        lineCap=_line_cap(path),
        lineJoin=int(path.get("lineJoin") or 0),
        dashes=path.get("dashes") or None,
        closePath=bool(path.get("closePath")),
        even_odd=bool(path.get("even_odd")),
        stroke_opacity=path.get("stroke_opacity") if path.get("stroke_opacity") is not None else 1,
        fill_opacity=path.get("fill_opacity") if path.get("fill_opacity") is not None else 1,
    )
    shape.commit()


def _remove_path(document: Document, page: pymupdf.Page, index: int) -> pymupdf.Page:
    """Remove exactly the path at `index`, restoring any collateral removals."""
    before = _drawings(page)
    target = before[index]
    margin = _COVER_SLACK + _MITER_LIMIT * max(target.get("width") or 0.0, 1.0)
    box = pymupdf.Rect(target["rect"])
    area = pymupdf.Rect(box.x0 - margin, box.y0 - margin, box.x1 + margin, box.y1 + margin)
    page.add_redact_annot(area)
    page.apply_redactions(**_REMOVE_LINE_ART_ONLY)
    page = _reload(document, page)

    remaining = Counter(_signature(path) for path in _drawings(page))
    wanted = Counter(_signature(path) for i, path in enumerate(before) if i != index)
    if remaining[_signature(target)] >= wanted[_signature(target)] + 1:
        raise OpValidationError("this shape could not be isolated for removal")
    for path in before:  # in original order, so restored paths keep their relative stacking
        signature = _signature(path)
        if path is not target and remaining[signature] < wanted[signature]:
            _draw_path(page, path)
            remaining[signature] += 1
    return _reload(document, page)


def draw_shape(
    document: Document,
    page_index: int,
    kind: ShapeKind,
    points: list[Point],
    *,
    stroke_color: Color | None = (0.0, 0.0, 0.0),
    fill_color: Color | None = None,
    line_width: float = 1.0,
    dashed: bool = False,
) -> ShapeInfo:
    """Draw a new shape. `points`: two for line/rect/ellipse (for rect and
    ellipse, opposite corners of the bounding box), two or more for a
    polyline, three or more for a (closed) polygon."""
    page = _page(document, page_index)
    _validate_color(stroke_color, "stroke_color")
    _validate_color(fill_color, "fill_color")
    if stroke_color is None and fill_color is None:
        raise OpValidationError("a shape needs a stroke_color, a fill_color, or both")
    if line_width <= 0:
        raise OpValidationError("line_width must be positive")
    needed = {"line": 2, "rect": 2, "ellipse": 2, "polyline": 2, "polygon": 3}[kind]
    if len(points) < needed or (kind in ("line", "rect", "ellipse") and len(points) != 2):
        raise OpValidationError(
            f"a {kind} needs {'exactly' if kind in ('line', 'rect', 'ellipse') else 'at least'} {needed} points"
        )
    corners = [pymupdf.Point(p) for p in points]
    if not any(p in page_bounds(page) for p in corners):
        raise OpValidationError("the shape lies entirely outside the page")

    shape = page.new_shape()
    if kind == "line":
        shape.draw_line(corners[0], corners[1])
    elif kind in ("rect", "ellipse"):
        box = pymupdf.Rect(corners[0], corners[1]).normalize()
        if box.is_empty:
            raise OpValidationError(f"a {kind} needs a non-empty area")
        if kind == "rect":
            shape.draw_rect(box)
        else:
            shape.draw_oval(box)
    else:
        shape.draw_polyline(corners)
    shape.finish(
        color=stroke_color,
        fill=fill_color if kind != "line" else None,
        width=line_width,
        dashes="[4 3] 0" if dashed else None,
        closePath=kind in ("rect", "ellipse", "polygon"),
    )
    shape.commit()
    _reload(document, page)
    return list_shapes(document, page_index)[-1]


def delete_shape(document: Document, page_index: int, index: int) -> ShapeInfo:
    page, drawings = _drawing_at(document, page_index, index)
    removed = _info(index, drawings[index])
    _remove_path(document, page, index)
    return removed


def edit_shape(
    document: Document,
    page_index: int,
    index: int,
    *,
    rect: Rect | None = None,
    stroke_color: Color | None = None,
    fill_color: Color | None = None,
    no_fill: bool = False,
    line_width: float | None = None,
) -> ShapeInfo:
    """Move/resize (by mapping the shape's bounding box onto `rect`) and/or
    restyle one existing shape."""
    if rect is None and stroke_color is None and fill_color is None and not no_fill and line_width is None:
        raise OpValidationError("edit_shape: nothing to change")
    if line_width is not None and line_width <= 0:
        raise OpValidationError("line_width must be positive")
    _validate_color(stroke_color, "stroke_color")
    _validate_color(fill_color, "fill_color")
    page, drawings = _drawing_at(document, page_index, index)
    path = drawings[index]

    transform = None
    if rect is not None:
        old = pymupdf.Rect(path["rect"])
        new = pymupdf.Rect(rect)
        if new.is_empty and not (new.width > 0 or new.height > 0):
            raise OpValidationError(f"shape rectangle {rect} is empty")
        if not new.intersects(page_bounds(page)) and not page_bounds(page).contains(new.tl):
            raise OpValidationError(f"shape rectangle {rect} lies entirely outside the page")
        sx = new.width / old.width if old.width else 1.0
        sy = new.height / old.height if old.height else 1.0

        def transform(p: pymupdf.Point) -> pymupdf.Point:
            return pymupdf.Point(new.x0 + (p.x - old.x0) * sx, new.y0 + (p.y - old.y0) * sy)

    page = _remove_path(document, page, index)
    _draw_path(
        page,
        path,
        transform=transform,
        stroke_color=stroke_color,
        fill_color=fill_color,
        no_fill=no_fill,
        line_width=line_width,
    )
    _reload(document, page)
    return list_shapes(document, page_index)[-1]
