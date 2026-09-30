"""EDT-16: the text units a click selects in Block, Line or Word mode (SPEC.md 8.2 item 6).

The server computes the units so the browser never duplicates the grouping rules.
Everything here is a pure function over the spans extract_page_spans already gives
(no PyMuPDF calls), so the same grouping serves the GET route, the Ops that edit a
unit, and the tests.

- A **line** is every glyph on one baseline, whatever its style runs. MuPDF splits a
  visual line into many spans (a bold word, a colour change, a PDF writer that places
  every glyph with its own ``Tm``), so spans are clustered by their baseline: a span
  joins a row when its baseline is within half the largest font size of the row's,
  which keeps superscripts and subscripts on their line. Within a row, glyphs are
  ordered along the writing direction, and a glyph-box gap wider than
  :data:`COLUMN_GAP_RATIO` x the font size splits the row into separate lines
  (columns). That ratio sits just above the 1.5 that justified text may stretch a
  word gap to, so a justified line is never cut in two.
- A **word** is a maximal run of non-space glyphs on a line. A gap between glyphs of
  more than :data:`SPACE_GAP_RATIO` x the font size counts as a space even when the
  PDF drew none (words separated by ``TJ`` offsets or separate ``Tm`` placements), so
  the line's text gets a synthetic space there, mapped to no glyph.
- A **block** is a paragraph of lines (engine.fonts.blocks.detect_blocks, which stacks
  the lines above -- EDT-19 -- via the same two-order resolution PageBlocksOp uses), so
  Block mode selects exactly what ``/blocks`` reports.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

from engine.fonts.blocks import TextBlock, detect_blocks, find_block_containing
from engine.fonts.style import CharBox, SpanTrace

Granularity = Literal["block", "line", "word"]
GRANULARITIES: tuple[Granularity, ...] = ("block", "line", "word")

BASELINE_TOLERANCE_RATIO = 0.5
"""A span joins a row when its baseline is within this fraction of the larger font
size (the same threshold engine.fonts.style uses to split a span into lines)."""
COLUMN_GAP_RATIO = 1.6
"""A glyph-box gap wider than this many font sizes splits a row into two lines."""
SPACE_GAP_RATIO = 0.15
"""A glyph-box gap wider than this many font sizes is a word break."""
_ROTATION_DECIMALS = 1
_NBSP = chr(0xA0)  # NO-BREAK SPACE


@dataclass(frozen=True)
class Segment:
    """A ``[start, end)`` character range within one span (indices into
    ``SpanStyle.chars`` and ``SpanStyle.text``)."""

    span_index: int
    start: int
    end: int


@dataclass(frozen=True)
class TextLine:
    """Every glyph on one baseline, in writing order (see the module docstring)."""

    index: int
    segments: tuple[Segment, ...]
    text: str
    glyph_map: tuple[tuple[int, int] | None, ...]
    """One entry per character of ``text``: its ``(span_index, char_index)``, or None
    for a synthetic space inserted at a gap the PDF drew no space glyph in."""
    bbox: tuple[float, float, float, float]
    origin: tuple[float, float]
    """The first glyph's origin (on the baseline)."""
    rotation_degrees: float
    size: float
    """The largest font size on the line."""
    boxes: tuple[CharBox | None, ...] = ()
    """Parallel to ``glyph_map``: each character's glyph box, None for a synthetic space."""

    @property
    def span_indices(self) -> list[int]:
        return _unique_spans(self.segments)


@dataclass(frozen=True)
class TextWord:
    """A maximal run of non-space glyphs on one line."""

    index: int
    line_index: int
    line_start: int
    """Offset of the word's first character in its line's ``text``."""
    line_end: int
    """Offset just past the word's last character in its line's ``text``."""
    segments: tuple[Segment, ...]
    text: str
    bbox: tuple[float, float, float, float]
    origin: tuple[float, float]

    @property
    def span_indices(self) -> list[int]:
        return _unique_spans(self.segments)


@dataclass(frozen=True)
class BlockGroup:
    """One text block, exactly as PageBlocksOp reports it."""

    span_indices: tuple[int, ...]
    bbox: tuple[float, float, float, float]
    text: str = ""
    """The block's lines joined with spaces (engine.fonts.blocks.TextBlock.text)."""


class TextUnit(BaseModel):
    """The wire shape of one unit (SPEC.md 8.2 item 6's ``TextUnit``).

    ``index`` is the block's first span index for a block, and the position in that
    granularity's list for a line or word. ``line_index`` is set for words only."""

    model_config = ConfigDict(frozen=True)

    granularity: Granularity
    index: int
    text: str
    bbox: tuple[float, float, float, float]
    origin: tuple[float, float]
    rotation_degrees: float
    span_indices: list[int]
    segments: list[Segment]
    line_index: int | None = None


# -- helpers -------------------------------------------------------------------------


def _unique_spans(segments: Sequence[Segment]) -> list[int]:
    seen: list[int] = []
    for segment in segments:
        if segment.span_index not in seen:
            seen.append(segment.span_index)
    return seen


def _is_space(char: str) -> bool:
    return char.isspace() or char == _NBSP


def _rotation_key(degrees: float) -> float:
    """The rotation rounded to 0.1 degree and normalised to (-180, 180], so 180 and
    -180 (and 0.0 and -0.0) group together."""
    key = round(degrees, _ROTATION_DECIMALS)
    if key <= -180.0:
        key += 360.0
    return key + 0.0


def _union(boxes: Sequence[tuple[float, float, float, float]]) -> tuple[float, float, float, float]:
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def _segments(glyphs: Sequence[tuple[int, int]]) -> tuple[Segment, ...]:
    """Consecutive glyphs of one span with consecutive char indices become one segment."""
    segments: list[Segment] = []
    for span_index, char_index in glyphs:
        last = segments[-1] if segments else None
        if last is not None and last.span_index == span_index and last.end == char_index:
            segments[-1] = Segment(span_index, last.start, char_index + 1)
        else:
            segments.append(Segment(span_index, char_index, char_index + 1))
    return tuple(segments)


@dataclass(frozen=True)
class _Glyph:
    span_index: int
    char_index: int
    box: CharBox
    size: float
    start: float
    """The glyph box's extent along the writing direction."""
    end: float


class _Frame:
    """Writing direction ``u`` and its perpendicular ``v`` for one rotation, with the
    same sign convention as engine.fonts.style._split_lines (y-down page space)."""

    def __init__(self, degrees: float) -> None:
        radians = math.radians(degrees)
        self.dx = math.cos(radians)
        self.dy = math.sin(radians)

    def u(self, point: tuple[float, float]) -> float:
        return self.dx * point[0] + self.dy * point[1]

    def v(self, point: tuple[float, float]) -> float:
        return -self.dy * point[0] + self.dx * point[1]

    def extent(self, bbox: tuple[float, float, float, float]) -> tuple[float, float]:
        corners = ((bbox[0], bbox[1]), (bbox[2], bbox[1]), (bbox[0], bbox[3]), (bbox[2], bbox[3]))
        values = [self.u(corner) for corner in corners]
        return min(values), max(values)


@dataclass
class _Row:
    baseline: float
    size: float
    spans: list[SpanTrace]


def _rows(spans: Sequence[SpanTrace], frame: _Frame) -> list[_Row]:
    """Cluster one rotation's spans by baseline. The largest spans are placed first, so
    a row's baseline is its body text's, and a smaller superscript or subscript then
    joins the nearest row within tolerance instead of starting its own."""
    ordered = sorted(
        spans,
        key=lambda s: (
            -s.style.size,
            frame.v(s.style.chars[0].origin),
            frame.u(s.style.chars[0].origin),
            s.style.span_index,
        ),
    )
    rows: list[_Row] = []
    for span in ordered:
        v = frame.v(span.style.chars[0].origin)
        best: _Row | None = None
        for row in rows:
            limit = BASELINE_TOLERANCE_RATIO * max(row.size, span.style.size)
            if abs(v - row.baseline) <= limit and (best is None or abs(v - row.baseline) < abs(v - best.baseline)):
                best = row
        if best is None:
            rows.append(_Row(baseline=v, size=span.style.size, spans=[span]))
        else:
            best.spans.append(span)
            best.size = max(best.size, span.style.size)
    return rows


def _row_lines(row: _Row, frame: _Frame) -> list[list[_Glyph]]:
    """A row's glyphs in writing order, split into lines at column-sized gaps."""
    glyphs: list[_Glyph] = []
    for span in row.spans:
        for char_index, box in enumerate(span.style.chars):
            start, end = frame.extent(box.bbox)
            glyphs.append(_Glyph(span.style.span_index, char_index, box, span.style.size, start, end))
    glyphs.sort(key=lambda g: (frame.u(g.box.origin), g.span_index, g.char_index))
    lines: list[list[_Glyph]] = []
    reach = -math.inf
    for glyph in glyphs:
        gap = glyph.start - reach
        if lines and gap > COLUMN_GAP_RATIO * max(glyph.size, lines[-1][-1].size):
            lines.append([glyph])
            reach = glyph.end
        elif lines:
            lines[-1].append(glyph)
            reach = max(reach, glyph.end)
        else:
            lines.append([glyph])
            reach = glyph.end
    return lines


def _build_line(glyphs: list[_Glyph]) -> tuple[str, list[_Glyph | None]]:
    """The line's text and glyph map, with a synthetic space wherever a word-sized gap
    has no space glyph on either side of it."""
    chars: list[str] = []
    placed: list[_Glyph | None] = []
    reach = -math.inf
    previous: _Glyph | None = None
    for glyph in glyphs:
        if previous is not None:
            gap = glyph.start - reach
            spaced = _is_space(previous.box.char) or _is_space(glyph.box.char)
            if gap > SPACE_GAP_RATIO * max(glyph.size, previous.size) and not spaced:
                chars.append(" ")
                placed.append(None)
        chars.append(glyph.box.char)
        placed.append(glyph)
        reach = max(reach, glyph.end)
        previous = glyph
    return "".join(chars), placed


# -- public API ----------------------------------------------------------------------


def group_lines(spans: Sequence[SpanTrace]) -> list[TextLine]:
    """Group a page's spans into lines, numbered by rotation (upright first), then
    baseline, then position along the line. Spans with no glyphs belong to no line. Deterministic: the same spans
    always give the same lines in the same order."""
    by_rotation: dict[float, list[SpanTrace]] = {}
    for span in spans:
        if span.style.chars:
            by_rotation.setdefault(_rotation_key(span.style.rotation_degrees), []).append(span)

    drafts: list[tuple[float, float, float, list[_Glyph], float]] = []
    for rotation in sorted(by_rotation):
        frame = _Frame(rotation)
        for row in _rows(by_rotation[rotation], frame):
            for glyphs in _row_lines(row, frame):
                drafts.append((rotation, row.baseline, glyphs[0].start, glyphs, row.size))
    # Upright text first (the page's reading order), then each other rotation in turn.
    drafts.sort(key=lambda d: (d[0] != 0.0, d[0], d[1], d[2], d[3][0].span_index, d[3][0].char_index))

    lines: list[TextLine] = []
    for index, (rotation, _baseline, _start, glyphs, _row_size) in enumerate(drafts):
        text, placed = _build_line(glyphs)
        lines.append(
            TextLine(
                index=index,
                segments=_segments([(g.span_index, g.char_index) for g in glyphs]),
                text=text,
                glyph_map=tuple(None if g is None else (g.span_index, g.char_index) for g in placed),
                bbox=_union([g.box.bbox for g in glyphs]),
                origin=glyphs[0].box.origin,
                rotation_degrees=rotation,
                size=max(g.size for g in glyphs),
                boxes=tuple(None if g is None else g.box for g in placed),
            )
        )
    return lines


def split_words(lines: Sequence[TextLine]) -> list[TextWord]:
    """Split lines into words: maximal runs of non-space characters of each line's
    text. The synthetic spaces group_lines inserted already mark the geometric word
    gaps, so a word may cross style runs (and spans) but never a gap. Punctuation
    stays with its word."""
    words: list[TextWord] = []
    for line in lines:
        start: int | None = None
        for offset in range(len(line.text) + 1):
            breaks = offset == len(line.text) or _is_space(line.text[offset])
            if not breaks and start is None:
                start = offset
            elif breaks and start is not None:
                glyphs = [g for g in line.glyph_map[start:offset] if g is not None]
                boxes = [b for b in line.boxes[start:offset] if b is not None]
                words.append(
                    TextWord(
                        index=len(words),
                        line_index=line.index,
                        line_start=start,
                        line_end=offset,
                        segments=_segments(glyphs),
                        text=line.text[start:offset],
                        bbox=_union([box.bbox for box in boxes]),
                        origin=boxes[0].origin,
                    )
                )
                start = None
    return words


def group_blocks(spans: Sequence[SpanTrace]) -> list[BlockGroup]:
    """The page's text blocks, the same grouping PageBlocksOp serves at ``/blocks``:
    detect_blocks (paragraphs of lines, EDT-19) over content-stream order and over
    top-to-bottom order, the block with more lines winning, each span in the first
    block that claims it."""
    ordered = list(spans)
    visual = sorted(
        ordered, key=lambda s: (round(s.style.chars[0].origin[1], 1) if s.style.chars else 0.0, s.style.bbox[0])
    )
    orders = [detect_blocks(ordered), detect_blocks(visual)]
    seen: set[int] = set()
    blocks: list[BlockGroup] = []
    for index, span in enumerate(ordered):
        if index in seen:
            continue
        found = [find_block_containing(order, span) for order in orders]
        block: TextBlock | None = max(
            (b for b in found if b is not None), key=lambda b: (len(b.rows), len(b.lines)), default=None
        )
        lines = block.lines if block is not None else (span,)
        members = [i for i, other in enumerate(ordered) if i not in seen and any(other is line for line in lines)]
        seen.update(members or [index])
        if block is not None and len(members) == len(lines):
            text = block.text
        else:  # part of the block was already claimed by another: only what is left
            text = " ".join(ordered[i].style.text for i in members or [index])
        blocks.append(
            BlockGroup(
                span_indices=tuple(members or [index]), bbox=_union([line.style.bbox for line in lines]), text=text
            )
        )
    return blocks


def _span_origin(span: SpanTrace) -> tuple[float, float]:
    if span.style.chars:
        return span.style.chars[0].origin
    return (span.style.bbox[0], span.style.bbox[3])


def text_units(spans: Sequence[SpanTrace], granularity: Granularity) -> list[TextUnit]:
    """Every unit of one granularity on a page, in that granularity's order."""
    if granularity == "block":
        units = []
        for block in group_blocks(spans):
            members = [spans[i] for i in block.span_indices]
            first = members[0].style
            units.append(
                TextUnit(
                    granularity="block",
                    index=block.span_indices[0],
                    text=block.text,
                    bbox=block.bbox,
                    origin=_span_origin(members[0]),
                    rotation_degrees=_rotation_key(first.rotation_degrees),
                    span_indices=list(block.span_indices),
                    segments=[Segment(m.style.span_index, 0, len(m.style.chars)) for m in members],
                )
            )
        return units
    lines = group_lines(spans)
    if granularity == "line":
        return [
            TextUnit(
                granularity="line",
                index=line.index,
                text=line.text,
                bbox=line.bbox,
                origin=line.origin,
                rotation_degrees=line.rotation_degrees,
                span_indices=line.span_indices,
                segments=list(line.segments),
            )
            for line in lines
        ]
    return [
        TextUnit(
            granularity="word",
            index=word.index,
            text=word.text,
            bbox=word.bbox,
            origin=word.origin,
            rotation_degrees=lines[word.line_index].rotation_degrees,
            span_indices=word.span_indices,
            segments=list(word.segments),
            line_index=word.line_index,
        )
        for word in split_words(lines)
    ]


def find_unit(
    units: Sequence[TextUnit], granularity: Granularity, origin: tuple[float, float], text: str, tol: float = 0.05
) -> TextUnit | None:
    """Re-find a unit after an earlier change renumbered the page: the unit of that
    granularity with the same text whose origin is within ``tol`` points, the closest
    one if several qualify. None when it's gone."""
    candidates = [
        unit
        for unit in units
        if unit.granularity == granularity and unit.text == text and math.dist(unit.origin, origin) <= tol
    ]
    return min(candidates, key=lambda unit: (math.dist(unit.origin, origin), unit.index), default=None)
