"""EDT-08: typed image Ops (see engine.images). Image data travels as
base64 (`image_base64`) so an Op stays plain JSON end to end."""

from __future__ import annotations

from typing import Literal

from engine.document import Document
from engine.images import (
    ImageInfo,
    Rect,
    crop_image,
    delete_image,
    insert_image,
    list_images,
    move_image,
    replace_image,
)
from engine.ops.base import Op, register_op


@register_op
class PageImagesOp(Op):
    """Every image placement on one page. Read-only, so -- like
    PageSpansOp -- never journaled; server/app.py serves it from a GET route."""

    op: Literal["page_images"] = "page_images"
    page_index: int = 0

    def apply(self, document: Document) -> list[ImageInfo]:
        return list_images(document, self.page_index)


@register_op
class InsertImageOp(Op):
    """Add a PNG/JPEG (or anything Pillow reads, re-encoded as PNG) at `rect`."""

    op: Literal["insert_image"] = "insert_image"
    page_index: int
    rect: Rect
    image_base64: str
    keep_proportion: bool = True

    def apply(self, document: Document) -> ImageInfo:
        return insert_image(
            document, self.page_index, self.rect, self.image_base64, keep_proportion=self.keep_proportion
        )


@register_op
class ReplaceImageOp(Op):
    """Swap the image at placement `index` for new image data, fitted into the same area."""

    op: Literal["replace_image"] = "replace_image"
    page_index: int
    index: int
    image_base64: str

    def apply(self, document: Document) -> ImageInfo:
        return replace_image(document, self.page_index, self.index, self.image_base64)


@register_op
class MoveImageOp(Op):
    """Move and/or resize the image at placement `index` to exactly `rect`."""

    op: Literal["move_image"] = "move_image"
    page_index: int
    index: int
    rect: Rect

    def apply(self, document: Document) -> ImageInfo:
        return move_image(document, self.page_index, self.index, self.rect)


@register_op
class CropImageOp(Op):
    """Cut the image at placement `index` down to the part inside `rect`."""

    op: Literal["crop_image"] = "crop_image"
    page_index: int
    index: int
    rect: Rect

    def apply(self, document: Document) -> ImageInfo:
        return crop_image(document, self.page_index, self.index, self.rect)


@register_op
class DeleteImageOp(Op):
    """Remove the image at placement `index` (only that placement)."""

    op: Literal["delete_image"] = "delete_image"
    page_index: int
    index: int

    def apply(self, document: Document) -> ImageInfo:
        return delete_image(document, self.page_index, self.index)
