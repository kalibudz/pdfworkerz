"""EDT-09: typed vector-shape Ops (see engine.shapes)."""

from __future__ import annotations

from typing import Literal

from engine.document import Document
from engine.ops.base import Op, register_op
from engine.shapes import Color, Point, Rect, ShapeInfo, ShapeKind, delete_shape, draw_shape, edit_shape, list_shapes


@register_op
class PageShapesOp(Op):
    """Every vector path on one page. Read-only, so -- like PageSpansOp --
    never journaled; server/app.py serves it from a GET route."""

    op: Literal["page_shapes"] = "page_shapes"
    page_index: int = 0

    def apply(self, document: Document) -> list[ShapeInfo]:
        return list_shapes(document, self.page_index)


@register_op
class DrawShapeOp(Op):
    """Draw a new line, rectangle, ellipse, polyline or polygon (see engine.shapes.draw_shape)."""

    op: Literal["draw_shape"] = "draw_shape"
    page_index: int
    kind: ShapeKind
    points: list[Point]
    stroke_color: Color | None = (0.0, 0.0, 0.0)
    fill_color: Color | None = None
    line_width: float = 1.0
    dashed: bool = False

    def apply(self, document: Document) -> ShapeInfo:
        return draw_shape(
            document,
            self.page_index,
            self.kind,
            self.points,
            stroke_color=self.stroke_color,
            fill_color=self.fill_color,
            line_width=self.line_width,
            dashed=self.dashed,
        )


@register_op
class EditShapeOp(Op):
    """Move/resize (`rect`: the shape's new bounding box) and/or restyle one
    existing shape. `no_fill` removes its fill."""

    op: Literal["edit_shape"] = "edit_shape"
    page_index: int
    index: int
    rect: Rect | None = None
    stroke_color: Color | None = None
    fill_color: Color | None = None
    no_fill: bool = False
    line_width: float | None = None

    def apply(self, document: Document) -> ShapeInfo:
        return edit_shape(
            document,
            self.page_index,
            self.index,
            rect=self.rect,
            stroke_color=self.stroke_color,
            fill_color=self.fill_color,
            no_fill=self.no_fill,
            line_width=self.line_width,
        )


@register_op
class DeleteShapeOp(Op):
    """Remove one vector path; text, images and other paths stay."""

    op: Literal["delete_shape"] = "delete_shape"
    page_index: int
    index: int

    def apply(self, document: Document) -> ShapeInfo:
        return delete_shape(document, self.page_index, self.index)
