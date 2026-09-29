"""P5 slice 4, annotations (ANN-01..06): text markup, notes and comments, shape and freehand
annotations, flattening, a summary export, and editing or deleting what's already there.

Annotations are addressed by (page_index, xref): unlike an index into a fresh read, an
annotation's xref stays the same while other annotations are added or removed. Links and form
fields are not annotations here (they have their own editors, EDT-10 and P6).

Coordinates are PDF points in the unrotated page, top-left origin, like every other editor.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Literal

import pymupdf
from pydantic import BaseModel, ConfigDict

from engine.design import Color, _color
from engine.document import Document
from engine.errors import OpValidationError, OverwriteRefusedError
from engine.geometry import page_bounds
from engine.pages import _check_indices, refuse_own_file

Rect = tuple[float, float, float, float]
Point = tuple[float, float]
MarkupKind = Literal["highlight", "underline", "strikeout", "squiggly"]
ShapeKind = Literal["rectangle", "ellipse", "line", "arrow", "polyline", "polygon", "freehand"]

_DEFAULT_COLORS: dict[str, Color] = {
    "highlight": (1.0, 0.9, 0.0),
    "underline": (0.0, 0.4, 0.9),
    "strikeout": (0.85, 0.0, 0.0),
    "squiggly": (0.85, 0.0, 0.0),
}


class AnnotationInfo(BaseModel):
    """One annotation, as the UI, CLI and summary export see it."""

    model_config = ConfigDict(frozen=True)

    page_index: int
    xref: int
    kind: str
    rect: Rect
    author: str = ""
    contents: str = ""
    color: Color | None = None
    opacity: float = 1.0
    marked_text: str = ""
    """For highlight/underline/strikeout/squiggly: the text they mark."""
    modified: str = ""


def _marked_text(page: pymupdf.Page, annot: pymupdf.Annot) -> str:
    vertices = annot.vertices or []
    if len(vertices) < 4:
        return ""
    lines = []
    for i in range(0, len(vertices) - 3, 4):
        quad = pymupdf.Quad(vertices[i : i + 4])
        lines.append(page.get_textbox(quad.rect).strip())
    return " ".join(line for line in lines if line)


def _info(page: pymupdf.Page, annot: pymupdf.Annot) -> AnnotationInfo:
    info = annot.info
    stroke = annot.colors.get("stroke") if annot.colors else None
    kind = annot.type[1]
    return AnnotationInfo(
        page_index=page.number or 0,
        xref=annot.xref,
        kind=kind,
        rect=tuple(annot.rect),
        author=info.get("title", ""),
        contents=info.get("content", ""),
        color=tuple(stroke) if stroke and len(stroke) == 3 else None,
        opacity=annot.opacity if annot.opacity is not None and annot.opacity >= 0 else 1.0,
        marked_text=_marked_text(page, annot) if kind in {"Highlight", "Underline", "StrikeOut", "Squiggly"} else "",
        modified=info.get("modDate", ""),
    )


def list_annotations(document: Document, page_index: int | None = None) -> list[AnnotationInfo]:
    """Every annotation on one page, or on every page."""
    pages = range(document.page_count) if page_index is None else [page_index]
    if page_index is not None:
        _check_indices(document, [page_index])
    out: list[AnnotationInfo] = []
    for index in pages:
        page = document.raw[index]
        out.extend(_info(page, annot) for annot in page.annots())
    return out


def _annot(document: Document, page_index: int, xref: int) -> tuple[pymupdf.Page, pymupdf.Annot]:
    _check_indices(document, [page_index])
    page = document.raw[page_index]
    # page.annots() leaves out links and form fields, which have their own editors.
    for annot in page.annots():
        if annot.xref == xref:
            return page, annot
    raise OpValidationError(f"page {page_index + 1} has no annotation {xref}")


def _finish(
    annot: pymupdf.Annot,
    *,
    color: Color | None = None,
    fill: Color | None = None,
    opacity: float | None = None,
    author: str | None = None,
    note: str | None = None,
    width: float | None = None,
) -> None:
    if color is not None or fill is not None:
        annot.set_colors(stroke=color, fill=fill)
    if opacity is not None:
        if not 0 < opacity <= 1:
            raise OpValidationError("opacity is more than 0, up to 1")
        annot.set_opacity(opacity)
    if width is not None:
        if not 0 < width <= 50:
            raise OpValidationError("line width is more than 0, up to 50 points")
        annot.set_border(width=width)
    if author is not None or note is not None:
        info = {}
        if author is not None:
            info["title"] = author
        if note is not None:
            info["content"] = note
        annot.set_info(**info)
    annot.update()


# -- ANN-01 text markup --


def mark_text(
    document: Document,
    kind: MarkupKind,
    *,
    match: str | None = None,
    rects: list[Rect] | None = None,
    page_index: int | None = None,
    color: str | Color | None = None,
    author: str | None = None,
    note: str | None = None,
) -> list[AnnotationInfo]:
    """Highlight, underline, strike out or squiggle every occurrence of `match` (on one page or
    all), or the given `rects` on one page. One annotation per occurrence."""
    adders = {
        "highlight": pymupdf.Page.add_highlight_annot,
        "underline": pymupdf.Page.add_underline_annot,
        "strikeout": pymupdf.Page.add_strikeout_annot,
        "squiggly": pymupdf.Page.add_squiggly_annot,
    }
    if kind not in adders:
        raise OpValidationError(f"unknown markup {kind!r}: use {', '.join(adders)}")
    if (match is None) == (rects is None):
        raise OpValidationError("mark either matching text or rectangles")
    rgb = _color(color) if color is not None else _DEFAULT_COLORS[kind]
    targets: list[tuple[int, list[pymupdf.Quad]]] = []
    if rects is not None:
        if page_index is None:
            raise OpValidationError("rectangles need a page")
        _check_indices(document, [page_index])
        bounds = page_bounds(document.raw[page_index])
        quads = []
        for rect in rects:
            area = pymupdf.Rect(rect)
            if area.is_empty or not bounds.contains(area):
                raise OpValidationError(f"rectangle {rect} is empty or off the page")
            quads.append(area.quad)
        targets.append((page_index, quads))
    else:
        if not match or not match.strip():
            raise OpValidationError("give the text to mark")
        indices = range(document.page_count) if page_index is None else [page_index]
        if page_index is not None:
            _check_indices(document, [page_index])
        for index in indices:
            for hit in document.raw[index].search_for(match, quads=True):
                targets.append((index, [hit]))
        if not targets:
            raise OpValidationError(
                f"{match!r} isn't in the document"
                if page_index is None
                else f"{match!r} isn't on page {page_index + 1}"
            )
    made = []
    for index, quads in targets:
        page = document.raw[index]
        for quad in quads:
            annot = adders[kind](page, quad)
            _finish(annot, color=rgb, author=author, note=note)
            made.append(_info(page, annot))
    return made


# -- ANN-02 comments and sticky notes --

NOTE_ICONS = ("Note", "Comment", "Help", "Insert", "Key", "NewParagraph", "Paragraph")


def add_note(
    document: Document,
    page_index: int,
    point: Point,
    text: str,
    *,
    author: str | None = None,
    icon: str = "Note",
    color: str | Color | None = None,
) -> AnnotationInfo:
    """A sticky note at `point`: an icon that opens to show `text`."""
    _check_indices(document, [page_index])
    if not text.strip():
        raise OpValidationError("a note needs some text")
    if icon not in NOTE_ICONS:
        raise OpValidationError(f"unknown note icon {icon!r}: use {', '.join(NOTE_ICONS)}")
    page = document.raw[page_index]
    if not page_bounds(page).contains(pymupdf.Point(point)):
        raise OpValidationError(f"{point} is off the page")
    annot = page.add_text_annot(point, text, icon=icon)
    _finish(annot, color=_color(color) if color is not None else None, author=author)
    return _info(page, annot)


def comment_box(
    document: Document,
    page_index: int,
    rect: Rect,
    text: str,
    *,
    author: str | None = None,
    size: float = 11.0,
    color: str | Color = "black",
    fill: str | Color | None = (1.0, 1.0, 0.8),
) -> AnnotationInfo:
    """A comment shown directly on the page, as a text box (a FreeText annotation)."""
    _check_indices(document, [page_index])
    if not text.strip():
        raise OpValidationError("a comment needs some text")
    page = document.raw[page_index]
    area = pymupdf.Rect(rect)
    if area.is_empty or not page_bounds(page).contains(area):
        raise OpValidationError(f"rectangle {rect} is empty or off the page")
    annot = page.add_freetext_annot(
        area,
        text,
        fontsize=size,
        text_color=_color(color),
        fill_color=_color(fill) if fill is not None else None,
    )
    _finish(annot, author=author)
    return _info(page, annot)


# -- ANN-03 shapes, lines, arrows and freehand --


def add_shape(
    document: Document,
    page_index: int,
    kind: ShapeKind,
    *,
    rect: Rect | None = None,
    points: list[Point] | None = None,
    strokes: list[list[Point]] | None = None,
    color: str | Color = "red",
    fill: str | Color | None = None,
    width: float = 2.0,
    opacity: float = 1.0,
    author: str | None = None,
    note: str | None = None,
) -> AnnotationInfo:
    """A shape annotation: rectangle or ellipse (`rect`), line or arrow (two `points`),
    polyline or polygon (`points`), or freehand ink (`strokes`, each a list of points)."""
    _check_indices(document, [page_index])
    page = document.raw[page_index]
    bounds = page_bounds(page)

    def inside(pts: list[Point]) -> list[pymupdf.Point]:
        out = [pymupdf.Point(p) for p in pts]
        if any(not bounds.contains(p) for p in out):
            raise OpValidationError("every point must be on the page")
        return out

    if kind in ("rectangle", "ellipse"):
        if rect is None:
            raise OpValidationError(f"a {kind} needs a rect")
        area = pymupdf.Rect(rect)
        if area.is_empty or not bounds.contains(area):
            raise OpValidationError(f"rectangle {rect} is empty or off the page")
        annot = page.add_rect_annot(area) if kind == "rectangle" else page.add_circle_annot(area)
    elif kind in ("line", "arrow"):
        if not points or len(points) != 2:
            raise OpValidationError(f"a {kind} needs exactly two points")
        start, end = inside(points)
        annot = page.add_line_annot(start, end)
        if kind == "arrow":
            annot.set_line_ends(
                pymupdf.PDF_ANNOT_LE_NONE,  # type: ignore[attr-defined]
                pymupdf.PDF_ANNOT_LE_OPEN_ARROW,  # type: ignore[attr-defined]
            )
    elif kind in ("polyline", "polygon"):
        if not points or len(points) < (2 if kind == "polyline" else 3):
            raise OpValidationError(f"a {kind} needs at least {2 if kind == 'polyline' else 3} points")
        pts = inside(points)
        annot = page.add_polyline_annot(pts) if kind == "polyline" else page.add_polygon_annot(pts)
    elif kind == "freehand":
        if not strokes or any(len(s) < 2 for s in strokes):
            raise OpValidationError("freehand needs strokes of at least two points each")
        annot = page.add_ink_annot([[(p.x, p.y) for p in inside(s)] for s in strokes])
    else:
        raise OpValidationError(f"unknown shape {kind!r}")
    closed = kind in ("rectangle", "ellipse", "polygon")
    _finish(
        annot,
        color=_color(color),
        fill=_color(fill) if fill is not None and closed else None,
        opacity=opacity,
        width=width,
        author=author,
        note=note,
    )
    return _info(page, annot)


# -- ANN-04 flatten --


def flatten(document: Document) -> int:
    """Draw every annotation into its page's content and remove the annotation objects, so they
    print everywhere and can no longer be moved or edited. Form fields are left alone (P6).
    Returns how many were flattened."""
    count = sum(len(list(document.raw[i].annots())) for i in range(document.page_count))
    if count:
        document.raw.bake(annots=True, widgets=False)
    return count


# -- ANN-05 summary export --

SUMMARY_FIELDS = ("page", "kind", "author", "contents", "marked_text", "modified")


def summary(
    document: Document, out: str, *, fmt: Literal["markdown", "csv", "json"] = "markdown", overwrite: bool = False
) -> int:
    """Write every annotation (page, kind, author, comment, marked text) to `out`. The document
    is unchanged. Returns how many were listed."""
    target = Path(out)
    refuse_own_file(document, target)
    if target.exists() and not overwrite:
        raise OverwriteRefusedError(f"{target} already exists; pass overwrite to replace it")
    if not target.parent.is_dir():
        raise OpValidationError(f"folder {target.parent} does not exist")
    items = list_annotations(document)
    rows = [
        {
            "page": item.page_index + 1,
            "kind": item.kind,
            "author": item.author,
            "contents": item.contents,
            "marked_text": item.marked_text,
            "modified": item.modified,
        }
        for item in items
    ]
    if fmt == "json":
        text = json.dumps(rows, indent=2, ensure_ascii=False) + "\n"
    elif fmt == "csv":
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=SUMMARY_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        text = buffer.getvalue()
    elif fmt == "markdown":
        lines = [f"# Annotations ({len(rows)})", ""]
        for row in rows:
            head = f"- **Page {row['page']}, {row['kind']}**" + (f" by {row['author']}" if row["author"] else "")
            lines.append(head)
            if row["marked_text"]:
                lines.append(f"  > {row['marked_text']}")
            if row["contents"]:
                lines.append(f"  {row['contents']}")
        text = "\n".join(lines) + "\n"
    else:
        raise OpValidationError(f"unknown format {fmt!r}: use markdown, csv or json")
    target.write_text(text, encoding="utf-8")
    return len(rows)


# -- ANN-06 edit and delete --


def update_annotation(
    document: Document,
    page_index: int,
    xref: int,
    *,
    contents: str | None = None,
    author: str | None = None,
    color: str | Color | None = None,
    opacity: float | None = None,
    rect: Rect | None = None,
) -> AnnotationInfo:
    """Change an existing annotation's comment, author, color, opacity or position."""
    page, annot = _annot(document, page_index, xref)
    if rect is not None:
        area = pymupdf.Rect(rect)
        if area.is_empty or not page_bounds(page).contains(area):
            raise OpValidationError(f"rectangle {rect} is empty or off the page")
        annot.set_rect(area)
    _finish(
        annot,
        color=_color(color) if color is not None else None,
        opacity=opacity,
        author=author,
        note=contents,
    )
    return _info(page, annot)


def delete_annotation(document: Document, page_index: int, xref: int) -> AnnotationInfo:
    """Remove one annotation (and its popup)."""
    page, annot = _annot(document, page_index, xref)
    gone = _info(page, annot)
    page.delete_annot(annot)
    return gone
