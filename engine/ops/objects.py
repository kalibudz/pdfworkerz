"""EDT-13..EDT-15, EDT-18: move, copy and delete several page objects -- text blocks, lines
and words, images and shapes -- as one Op, so align, distribute, nudge, paste and delete
are each one undo.

A batch of single-object Ops can't do this: every text redraw, image move and shape
redraw renumbers what's on the page, so the second Op's index would point at the wrong
object. Instead, every target is resolved to an identity that survives renumbering
*before* anything changes, then found again by that identity just before it is changed:

- a text block: its first line's origin and text (engine.ops.text._span_by_origin);
- a line or a word (EDT-18): its granularity, its first glyph's origin and its text
  (engine.fonts.units);
- an image placement: its image xref and rectangle;
- a shape: its path signature (engine.shapes._signature).

A unit that lies inside another selected unit (a word of a selected line, a line of a
selected block) is dropped: the larger unit already carries it. Words of one line are
changed in a single pass, because closing the gap a deleted word leaves moves its
neighbours, and they could not be found again afterwards.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from engine.edit import (
    EditResult,
    GlyphRange,
    delete_block,
    delete_glyph_ranges,
    delete_line_words,
    move_glyph_ranges,
    move_resize_block,
)
from engine.errors import OpValidationError
from engine.fonts.blocks import TextBlock
from engine.fonts.style import SpanTrace, extract_page_spans
from engine.fonts.units import Segment, TextLine, TextWord, group_blocks, group_lines, split_words, text_units
from engine.images import delete_image, duplicate_image, list_images, move_image
from engine.ops.base import Op, register_op
from engine.ops.text import _block_at, _check_tier, _font_index, _span_by_origin
from engine.shapes import _drawings, _page, _signature, delete_shape, duplicate_shape, edit_shape

if TYPE_CHECKING:
    from collections.abc import Sequence

    from engine.document import Document

_SAME_RECT_TOLERANCE = 0.5  # pt
_SAME_ORIGIN_TOLERANCE = 0.05  # pt, the same as engine.fonts.units.find_unit


class ObjectRef(BaseModel):
    """One object on a page, as the UI selected it."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["text", "image", "shape"]
    page_index: int
    index: int
    """For a text block: any span of the block (its span index). For a text line or word:
    its index among the page's lines or words (GET .../text_units). For images and shapes,
    their index."""
    dx: float = 0.0
    """This item's own offset, added to the Op's shared dx/dy (align and distribute
    move each object by a different amount)."""
    dy: float = 0.0
    unit: Literal["block", "line", "word"] = "block"
    """EDT-18: what `index` names, for kind "text" (SPEC.md 8.2 item 6)."""
    expect_text: str | None = None
    """The unit's text as the UI last saw it. If the unit at `index` no longer has that
    text the Op is refused, rather than changing whatever is there now."""

    @model_validator(mode="after")
    def _unit_is_for_text(self) -> ObjectRef:
        if self.kind != "text" and (self.unit != "block" or self.expect_text is not None):
            raise ValueError('unit and expect_text are only for kind "text"')
        if self.unit != "block" and self.expect_text is None:
            raise ValueError(f"expect_text is required for a {self.unit}: the unit's text as last seen")
        return self


Glyph = tuple[int, int]
"""(span index, character index) on the page as it was when the selection was identified."""


class _Identity:
    """An object's identity across renumbering (see the module docstring)."""

    def __init__(self, ref: ObjectRef, key: Any, glyphs: frozenset[Glyph] = frozenset(), line_key: Any = None) -> None:
        self.ref = ref
        self.key = key
        self.glyphs = glyphs
        """For text: the glyphs it covers, to tell which selected units contain which."""
        self.line_key = line_key
        """For a word: its line's identity, so words of one line are changed together."""


def _segment_glyphs(segments: Sequence[Segment]) -> frozenset[Glyph]:
    return frozenset((seg.span_index, char) for seg in segments for char in range(seg.start, seg.end))


def _check_expected(ref: ObjectRef, text: str) -> None:
    if ref.expect_text is not None and ref.expect_text != text:
        raise OpValidationError(
            f"the {ref.unit} at index {ref.index} on page {ref.page_index} is now {text!r}, not "
            f"{ref.expect_text!r}; check the page again"
        )


def _unit_out_of_range(ref: ObjectRef, count: int) -> OpValidationError:
    return OpValidationError(f"page {ref.page_index} has {count} {ref.unit}(s); index {ref.index} is out of range")


def _identify_text(document: Document, ref: ObjectRef) -> _Identity:
    spans = extract_page_spans(document.raw, ref.page_index)
    if ref.unit == "block":
        block = _block_at(document, ref.page_index, ref.index)
        first = block.lines[0].style
        if not first.chars:
            raise OpValidationError("the text block has no characters to identify it by")
        if ref.expect_text is not None:
            # The same text GET .../text_units reported for the block this span is in.
            unit = next((u for u in text_units(spans, "block") if ref.index in u.span_indices), None)
            _check_expected(ref, unit.text if unit is not None else block.text)
        glyphs = frozenset(
            (line.style.span_index, char) for line in block.lines for char in range(len(line.style.chars))
        )
        return _Identity(ref, (first.chars[0].origin, first.text), glyphs)
    lines = group_lines(spans)
    if ref.unit == "line":
        if not 0 <= ref.index < len(lines):
            raise _unit_out_of_range(ref, len(lines))
        line = lines[ref.index]
        _check_expected(ref, line.text)
        return _Identity(ref, ("line", line.origin, line.text), _segment_glyphs(line.segments))
    words = split_words(lines)
    if not 0 <= ref.index < len(words):
        raise _unit_out_of_range(ref, len(words))
    word = words[ref.index]
    _check_expected(ref, word.text)
    home = lines[word.line_index]
    return _Identity(
        ref, ("word", word.origin, word.text), _segment_glyphs(word.segments), line_key=(home.origin, home.text)
    )


def _identify(document: Document, ref: ObjectRef) -> _Identity:
    if ref.kind == "text":
        return _identify_text(document, ref)
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


def _closest(units: Sequence[Any], origin: tuple[float, float], text: str) -> Any:
    """Re-find a line or word by its text and first-glyph origin (as find_unit does)."""
    near = [u for u in units if u.text == text and math.dist(u.origin, origin) <= _SAME_ORIGIN_TOLERANCE]
    return min(near, key=lambda u: (math.dist(u.origin, origin), u.index), default=None)


def _gone(identity: _Identity) -> OpValidationError:
    return OpValidationError(
        f"the {identity.ref.unit} {identity.key[2]!r} changed or disappeared while the others were being changed"
    )


def _ranges(spans: list[SpanTrace], segments: Sequence[Segment]) -> list[GlyphRange]:
    return [(spans[segment.span_index], segment.start, segment.end) for segment in segments]


def _current_line(document: Document, identity: _Identity) -> tuple[list[SpanTrace], TextLine]:
    spans = extract_page_spans(document.raw, identity.ref.page_index)
    line = _closest(group_lines(spans), identity.key[1], identity.key[2])
    if line is None:
        raise _gone(identity)
    return spans, line


def _current_words(document: Document, group: list[_Identity]) -> tuple[list[SpanTrace], TextLine, list[TextWord]]:
    """The words of one line, found again on the page as it is now."""
    spans = extract_page_spans(document.raw, group[0].ref.page_index)
    lines = group_lines(spans)
    every = split_words(lines)
    words: list[TextWord] = []
    for identity in group:
        word = _closest(every, identity.key[1], identity.key[2])
        if word is None:
            raise _gone(identity)
        words.append(word)
    if len({word.line_index for word in words}) != 1:
        raise OpValidationError("the selected words are no longer on one line; check the page again")
    return spans, lines[words[0].line_index], words


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
    text block (two of its lines selected) are one object, moved or copied once; and a
    text unit inside another selected one (a word of a selected line or block, a line of a
    selected block) is dropped, so nothing is moved, copied or deleted twice."""
    identities: list[_Identity] = []
    for item in items:
        identity = _identify(document, item)
        same = (item.kind, item.page_index, identity.key)
        if not any((other.ref.kind, other.ref.page_index, other.key) == same for other in identities):
            identities.append(identity)

    def contained(position: int, identity: _Identity) -> bool:
        if identity.ref.kind != "text" or not identity.glyphs:
            return False
        for other_position, other in enumerate(identities):
            if other is identity or other.ref.kind != "text" or other.ref.page_index != identity.ref.page_index:
                continue
            if identity.glyphs < other.glyphs or (identity.glyphs == other.glyphs and other_position < position):
                return True
        return False

    return [identity for position, identity in enumerate(identities) if not contained(position, identity)]


def _is_unit(identity: _Identity, unit: str) -> bool:
    return identity.ref.kind == "text" and identity.ref.unit == unit


def _grouped(identities: list[_Identity], *, by_offset: bool) -> list[list[_Identity]]:
    """The identities in order, with the words of one line gathered into one group (and,
    `by_offset`, only those moving by the same amount: align gives each its own)."""
    groups: list[list[_Identity]] = []
    word_groups: dict[Any, list[_Identity]] = {}
    for identity in identities:
        if not _is_unit(identity, "word"):
            groups.append([identity])
            continue
        ref = identity.ref
        key = (ref.page_index, identity.line_key, (ref.dx, ref.dy) if by_offset else None)
        if key in word_groups:
            word_groups[key].append(identity)
        else:
            word_groups[key] = [identity]
            groups.append(word_groups[key])
    return groups


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

    def _move_unit(
        self,
        document: Document,
        group: list[_Identity],
        dx: float,
        dy: float,
        *,
        keep_original: bool = False,
        target_page_index: int | None = None,
    ) -> list[EditResult]:
        """EDT-18: move or copy one line, or the selected words of one line, glyph for glyph."""
        if _is_unit(group[0], "line"):
            spans, line = _current_line(document, group[0])
            ranges = _ranges(spans, line.segments)
        else:
            spans, _line, words = _current_words(document, group)
            ranges = [glyph_range for word in words for glyph_range in _ranges(spans, word.segments)]
        moved = move_glyph_ranges(
            document,
            group[0].ref.page_index,
            ranges,
            dx=dx,
            dy=dy,
            keep_original=keep_original,
            target_page_index=target_page_index,
            font_index=_font_index(),
            verify=self.verify,
        )
        return self._text_results([moved])


@register_op
class MoveObjectsOp(_ObjectsOp):
    """EDT-13/EDT-14/EDT-18: move every item by the shared (`dx`, `dy`) plus its own offset --
    what align, distribute, nudging and dragging several objects all send."""

    op: Literal["move_objects"] = "move_objects"
    dx: float = 0.0
    dy: float = 0.0

    def apply(self, document: Document) -> list[Any]:
        identities = _identify_all(document, self.items)
        results: list[Any] = []
        for group in _grouped(identities, by_offset=True):
            identity = group[0]
            ref = identity.ref
            dx, dy = self.dx + ref.dx, self.dy + ref.dy
            if dx == 0 and dy == 0:
                continue
            if ref.kind == "text" and ref.unit != "block":
                results.extend(self._move_unit(document, group, dx, dy))
            elif ref.kind == "text":
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
    """EDT-15/EDT-18: copy every item, offset by (`dx`, `dy`), onto `target_page_index`
    (default: each item's own page) -- paste and duplicate. The originals are untouched."""

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
        for group in _grouped(identities, by_offset=True):
            identity = group[0]
            ref = identity.ref
            dx, dy = self.dx + ref.dx, self.dy + ref.dy
            if ref.kind == "text" and ref.unit != "block":
                results.extend(
                    self._move_unit(
                        document, group, dx, dy, keep_original=True, target_page_index=self.target_page_index
                    )
                )
            elif ref.kind == "text":
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
    """EDT-15/EDT-18: remove every item -- the Delete key on a selection."""

    op: Literal["delete_objects"] = "delete_objects"
    close_gap: bool = True
    """EDT-18, words only: after a word (and one adjacent space) is removed, close the gap
    by moving the rest of its line, following the line's alignment. False leaves the gap."""

    def apply(self, document: Document) -> list[Any]:
        identities = _identify_all(document, self.items)
        results: list[Any] = []
        for group in _grouped(identities, by_offset=False):
            identity = group[0]
            ref = identity.ref
            if _is_unit(identity, "word"):
                _spans, line, words = _current_words(document, group)
                deleted = delete_line_words(
                    document,
                    ref.page_index,
                    line,
                    words,
                    close_gap=self.close_gap,
                    font_index=_font_index(),
                    verify=self.verify,
                )
                results.extend(self._text_results([deleted]))
            elif _is_unit(identity, "line"):
                spans, line = _current_line(document, identity)
                results.append(
                    delete_glyph_ranges(document, ref.page_index, _ranges(spans, line.segments), verify=self.verify)
                )
            elif ref.kind == "text":
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
        # One grouping rule, shared with Block mode's text units (engine.fonts.units).
        spans = extract_page_spans(document.raw, self.page_index)
        return [{"span_indices": list(block.span_indices), "bbox": block.bbox} for block in group_blocks(spans)]


def _same_line(a: Any, b: Any) -> bool:
    return bool(a.style.text == b.style.text and a.style.bbox == b.style.bbox)
