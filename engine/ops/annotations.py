"""P5 slice 4: typed annotation Ops (ANN-01..06); see engine.annotations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import engine.annotations as ann
from engine.annotations import AnnotationInfo, MarkupKind, Point, Rect, ShapeKind
from engine.ops.base import Op, register_op

if TYPE_CHECKING:
    from engine.document import Document

Color = str | tuple[float, float, float]


@register_op
class PageAnnotationsOp(Op):
    """Every annotation on one page (or all pages, with page_index None). Read-only, so --
    like PageLinksOp -- never journaled; server/app.py serves it from a GET route."""

    op: Literal["page_annotations"] = "page_annotations"
    page_index: int | None = None

    def apply(self, document: Document) -> list[AnnotationInfo]:
        return ann.list_annotations(document, self.page_index)


@register_op
class MarkTextOp(Op):
    """ANN-01: highlight / underline / strikeout / squiggly every `match` (or `rects`)."""

    op: Literal["mark_text"] = "mark_text"
    kind: MarkupKind = "highlight"
    match: str | None = None
    rects: list[Rect] | None = None
    page_index: int | None = None
    color: Color | None = None
    author: str | None = None
    note: str | None = None

    def apply(self, document: Document) -> list[AnnotationInfo]:
        return ann.mark_text(
            document,
            self.kind,
            match=self.match,
            rects=self.rects,
            page_index=self.page_index,
            color=self.color,
            author=self.author,
            note=self.note,
        )


@register_op
class AddNoteOp(Op):
    """ANN-02: a sticky note at `point`."""

    op: Literal["add_note"] = "add_note"
    page_index: int
    point: Point
    text: str
    author: str | None = None
    icon: str = "Note"
    color: Color | None = None

    def apply(self, document: Document) -> AnnotationInfo:
        return ann.add_note(
            document, self.page_index, self.point, self.text, author=self.author, icon=self.icon, color=self.color
        )


@register_op
class AddCommentOp(Op):
    """ANN-02: a comment shown on the page as a text box."""

    op: Literal["add_comment"] = "add_comment"
    page_index: int
    rect: Rect
    text: str
    author: str | None = None
    size: float = 11.0

    def apply(self, document: Document) -> AnnotationInfo:
        return ann.comment_box(document, self.page_index, self.rect, self.text, author=self.author, size=self.size)


@register_op
class AddAnnotationShapeOp(Op):
    """ANN-03: rectangle, ellipse, line, arrow, polyline, polygon or freehand annotation."""

    op: Literal["add_annotation_shape"] = "add_annotation_shape"
    page_index: int
    kind: ShapeKind
    rect: Rect | None = None
    points: list[Point] | None = None
    strokes: list[list[Point]] | None = None
    color: Color = "red"
    fill: Color | None = None
    width: float = 2.0
    opacity: float = 1.0
    author: str | None = None
    note: str | None = None

    def apply(self, document: Document) -> AnnotationInfo:
        return ann.add_shape(
            document,
            self.page_index,
            self.kind,
            rect=self.rect,
            points=self.points,
            strokes=self.strokes,
            color=self.color,
            fill=self.fill,
            width=self.width,
            opacity=self.opacity,
            author=self.author,
            note=self.note,
        )


@register_op
class FlattenAnnotationsOp(Op):
    """ANN-04: draw every annotation into the page content; returns how many."""

    op: Literal["flatten_annotations"] = "flatten_annotations"

    def apply(self, document: Document) -> int:
        return ann.flatten(document)


@register_op
class AnnotationSummaryOp(Op):
    """ANN-05: write every annotation to `out` (markdown, csv or json). Document unchanged."""

    op: Literal["annotation_summary"] = "annotation_summary"
    out: str
    format: Literal["markdown", "csv", "json"] = "markdown"
    overwrite: bool = False

    def apply(self, document: Document) -> int:
        return ann.summary(document, self.out, fmt=self.format, overwrite=self.overwrite)


@register_op
class UpdateAnnotationOp(Op):
    """ANN-06: change an annotation's comment, author, color, opacity or position."""

    op: Literal["update_annotation"] = "update_annotation"
    page_index: int
    xref: int
    contents: str | None = None
    author: str | None = None
    color: Color | None = None
    opacity: float | None = None
    rect: Rect | None = None

    def apply(self, document: Document) -> AnnotationInfo:
        return ann.update_annotation(
            document,
            self.page_index,
            self.xref,
            contents=self.contents,
            author=self.author,
            color=self.color,
            opacity=self.opacity,
            rect=self.rect,
        )


@register_op
class DeleteAnnotationOp(Op):
    """ANN-06: remove one annotation."""

    op: Literal["delete_annotation"] = "delete_annotation"
    page_index: int
    xref: int

    def apply(self, document: Document) -> AnnotationInfo:
        return ann.delete_annotation(document, self.page_index, self.xref)
