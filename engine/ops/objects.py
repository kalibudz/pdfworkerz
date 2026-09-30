"""EDT-13..EDT-15: move, copy and delete several page objects -- text blocks, images and
shapes -- as one Op, so align, distribute, nudge, paste and delete are each one undo.

A batch of single-object Ops can't do this: every text redraw, image move and shape
redraw renumbers what's on the page, so the second Op's index would point at the wrong
object. Instead, every target is resolved to an identity that survives renumbering
*before* anything changes, then found again by that identity just before it is changed:

- a text block: its first line's origin and text (engine.ops.text._span_by_origin);
- an image placement: its image xref and rectangle;
- a shape: its path signature (engine.shapes._signature).
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator

from engine.edit import EditResult, delete_block, move_resize_block
from engine.errors import OpValidationError
from engine.fonts.blocks import TextBlock, detect_blocks, find_block_containing
from engine.fonts.style import extract_page_spans
from engine.images import delete_image, duplicate_image, list_images, move_image
from engine.ops.base import Op, register_op
from engine.ops.text import _block_at, _check_tier, _font_index, _span_by_origin
from engine.shapes import _drawings, _page, _signature, delete_shape, duplicate_shape, edit_shape

if TYPE_CHECKING:
    from engine.document import Document

_SAME_RECT_TOLERANCE = 0.5  # pt


class ObjectRef(BaseModel):
    """One object on a page, as the UI selected it."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["text", "image", "shape"]
    page_index: int
    index: int
    """For text: any span of the block (its span index); for images and shapes, their index."""
    dx: float = 0.0
    """This item's own offset, added to the Op's shared dx/dy (align and distribute
    move each object by a different amount)."""
    dy: float = 0.0


class _Identity:
    """An object's identity across renumbering (see the module docstring)."""

    def __init__(self, ref: ObjectRef, key: Any) -> None:
        self.ref = ref
        self.key = key


def _identify(document: Document, ref: ObjectRef) -> _Identity:
    if ref.kind == "text":
        block = _block_at(document, ref.page_index, ref.index)
        first = block.lines[0].style
        if not first.chars:
            raise OpValidationError("the text block has no characters to identify it by")
        return _Identity(ref, (first.chars[0].origin, first.text))
    if ref.kind == "image":
        images = list_images(document, ref.page_index)
        if not 0 <= ref.index < len(images):
            raise OpValidationError(
                f"page {ref.page_index} has {len(images)} image(s); index {ref.index} is out of range"
            )
        return _Identity(ref, (images[ref.index].xref, images[ref.index].rect))
    drawings = _drawings(_page(document, ref.page_index))
    if not 0 <= ref.index < len(drawings):
        raise OpValidationError(
            f"page {ref.page_index} has {len(drawings)} shape(s); index {ref.index} is out of range"
        )
    return _Identity(ref, _signature(drawings[ref.index]))


def _current_block(document: Document, identity: _Identity) -> TextBlock:
    origin, text = identity.key
    page_index = identity.ref.page_index
    span = _span_by_origin(document, page_index, origin, text)  # raises if it's gone
    spans = extract_page_spans(document.raw, page_index)
    index = next(i for i, candidate in enumerate(spans) if _same_line(candidate, span))
    return _block_at(document, page_index, index)


def _current_index(document: Document, identity: _Identity) -> int:
    page_index = identity.ref.page_index
    if identity.ref.kind == "image":
        xref, rect = identity.key
        for info in list_images(document, page_index):
            if info.xref == xref and all(
                math.isclose(a, b, abs_tol=_SAME_RECT_TOLERANCE) for a, b in zip(info.rect, rect, strict=True)
            ):
                return info.index
        raise OpValidationError("an image changed or disappeared while the others were being changed")
    for index, path in enumerate(_drawings(_page(document, page_index))):
        if _signature(path) == identity.key:
            return index
    raise OpValidationError("a shape changed or disappeared while the others were being changed")


def _identify_all(document: Document, items: list[ObjectRef]) -> list[_Identity]:
    """Every item's identity, taken before anything changes. Two items naming the same
    text block (two of its lines selected) are one object, moved or copied once."""
    identities: list[_Identity] = []
    for item in items:
        identity = _identify(document, item)
        if not any(other.ref.kind == item.kind and other.key == identity.key for other in identities):
            identities.append(identity)
    return identities


class _ObjectsOp(Op):
    items: list[ObjectRef]
    require_tier: Literal["exact", "approximate", "fallback"] = "approximate"
    """For text blocks: the weakest font match accepted (see ReplaceSpanTextOp)."""
    verify: bool = True

    @field_validator("items")
    @classmethod
    def _not_empty(cls, value: list[ObjectRef]) -> list[ObjectRef]:
        if not value:
            raise ValueError("select at least one object")
        return value

    def check_pages(self, document: Document) -> None:
        for item in self.items:
            if not 0 <= item.page_index < document.page_count:
                raise OpValidationError(f"page_index {item.page_index} is out of range (0-{document.page_count - 1})")

    def _text_results(self, results: list[EditResult]) -> list[EditResult]:
        for result in results:
            _check_tier(result, self.require_tier, where=type(self).__name__)
        return results


@register_op
class MoveObjectsOp(_ObjectsOp):
    """EDT-13/EDT-14: move every item by the shared (`dx`, `dy`) plus its own offset --
    what align, distribute, nudging and dragging several objects all send."""

    op: Literal["move_objects"] = "move_objects"
    dx: float = 0.0
    dy: float = 0.0

    def apply(self, document: Document) -> list[Any]:
        identities = _identify_all(document, self.items)
        results: list[Any] = []
        for identity in identities:
            ref = identity.ref
            dx, dy = self.dx + ref.dx, self.dy + ref.dy
            if dx == 0 and dy == 0:
                continue
            if ref.kind == "text":
                block = _current_block(document, identity)
                moved = move_resize_block(
                    document, ref.page_index, block, dx=dx, dy=dy, font_index=_font_index(), verify=self.verify
                )
                results.extend(self._text_results(moved))
            elif ref.kind == "image":
                index = _current_index(document, identity)
                x0, y0, x1, y1 = list_images(document, ref.page_index)[index].rect
                results.append(move_image(document, ref.page_index, index, (x0 + dx, y0 + dy, x1 + dx, y1 + dy)))
            else:
                index = _current_index(document, identity)
                x0, y0, x1, y1 = _drawings(_page(document, ref.page_index))[index]["rect"]
                results.append(edit_shape(document, ref.page_index, index, rect=(x0 + dx, y0 + dy, x1 + dx, y1 + dy)))
        if not results:
            raise OpValidationError("move_objects: nothing to move (every offset is zero)")
        return results


@register_op
class DuplicateObjectsOp(_ObjectsOp):
    """EDT-15: copy every item, offset by (`dx`, `dy`), onto `target_page_index` (default:
    each item's own page) -- paste and duplicate. The originals are untouched."""

    op: Literal["duplicate_objects"] = "duplicate_objects"
    dx: float = 10.0
    dy: float = 10.0
    target_page_index: int | None = None

    def check_pages(self, document: Document) -> None:
        super().check_pages(document)
        if self.target_page_index is not None and not 0 <= self.target_page_index < document.page_count:
            raise OpValidationError(f"target_page_index {self.target_page_index} is out of range")

    def apply(self, document: Document) -> list[Any]:
        identities = _identify_all(document, self.items)
        results: list[Any] = []
        for identity in identities:
            ref = identity.ref
            dx, dy = self.dx + ref.dx, self.dy + ref.dy
            if ref.kind == "text":
                block = _current_block(document, identity)
                copied = move_resize_block(
                    document,
                    ref.page_index,
                    block,
                    dx=dx,
                    dy=dy,
                    font_index=_font_index(),
                    verify=self.verify,
                    keep_original=True,
                    target_page_index=self.target_page_index,
                )
                results.extend(self._text_results(copied))
            elif ref.kind == "image":
                index = _current_index(document, identity)
                results.append(
                    duplicate_image(document, ref.page_index, index, dx, dy, target_page_index=self.target_page_index)
                )
            else:
                index = _current_index(document, identity)
                results.append(
                    duplicate_shape(document, ref.page_index, index, dx, dy, target_page_index=self.target_page_index)
                )
        return results


@register_op
class DeleteObjectsOp(_ObjectsOp):
    """EDT-15: remove every item -- the Delete key on a selection."""

    op: Literal["delete_objects"] = "delete_objects"

    def apply(self, document: Document) -> list[Any]:
        identities = _identify_all(document, self.items)
        results: list[Any] = []
        for identity in identities:
            ref = identity.ref
            if ref.kind == "text":
                results.append(
                    delete_block(document, ref.page_index, _current_block(document, identity), verify=self.verify)
                )
            elif ref.kind == "image":
                results.append(delete_image(document, ref.page_index, _current_index(document, identity)))
            else:
                results.append(delete_shape(document, ref.page_index, _current_index(document, identity)))
        return results


@register_op
class PageBlocksOp(Op):
    """The page's text blocks (engine.fonts.blocks), each as its span indices and the
    box around them -- what the UI selects, snaps and aligns as one text object.
    Read-only, never journaled; served from a GET route."""

    op: Literal["page_blocks"] = "page_blocks"
    page_index: int = 0

    def apply(self, document: Document) -> list[dict[str, Any]]:
        # One extraction for the whole page, the same two orders _block_at tries.
        spans = extract_page_spans(document.raw, self.page_index)
        visual = sorted(
            spans, key=lambda s: (round(s.style.chars[0].origin[1], 1) if s.style.chars else 0.0, s.style.bbox[0])
        )
        orders = [detect_blocks(spans), detect_blocks(visual)]
        seen: set[int] = set()
        blocks: list[dict[str, Any]] = []
        for index, span in enumerate(spans):
            if index in seen:
                continue
            found = [find_block_containing(order, span) for order in orders]
            block = max((b for b in found if b is not None), key=lambda b: len(b.lines), default=None)
            lines = block.lines if block is not None else (span,)
            members = [i for i, other in enumerate(spans) if i not in seen and any(other is line for line in lines)]
            seen.update(members or [index])
            boxes = [line.style.bbox for line in lines]
            blocks.append(
                {
                    "span_indices": members or [index],
                    "bbox": (
                        min(b[0] for b in boxes),
                        min(b[1] for b in boxes),
                        max(b[2] for b in boxes),
                        max(b[3] for b in boxes),
                    ),
                }
            )
        return blocks


def _same_line(a: Any, b: Any) -> bool:
    return bool(a.style.text == b.style.text and a.style.bbox == b.style.bbox)
