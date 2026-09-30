"""FNT-11 (block detection half): group a page's text into left-aligned,
single-column text blocks -- SPEC.md section 5.4's "block" that reflow
wraps text within.

EDT-19: a block is made of **lines**, not spans. MuPDF reports one span per
style run, so a line with a bold word, a recoloured word, or glyphs an
in-line edit redrew in several pieces is several spans on one baseline.
Those are first gathered into visual lines (engine.fonts.units.group_lines,
the same grouping Line mode selects), and the lines are then stacked into
blocks -- so editing one word never drops its line out of its paragraph.

Deliberately narrow scope, stated up front rather than discovered by a
caller later: two lines are stacked only when they share their main font
size and at least one font, start at the same left margin (within a small
tolerance), are rotated 0 degrees, and sit at a plausible single-line-
spacing gap (0.5x-3x the font size -- wide enough for single- and
double-spaced text, narrow enough not to bridge an unrelated paragraph
with a larger gap between them; both bounds confirmed against a real
three-line paragraph and the gap to a following, separate paragraph).
Right-aligned, centered, justified and multi-column layouts are not
detected as single blocks; each of their lines stands as its own
one-line block instead, which reflow (engine.fonts.reflow) can still
handle, just without spreading new text across their other lines.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from engine.fonts.style import SpanTrace

_MIN_LINE_GAP_RATIO = 0.5
_MAX_LINE_GAP_RATIO = 3.0
_LEFT_MARGIN_TOLERANCE = 3.0

_Style = tuple[str, float, tuple[float, float, float]]


def _style_key(span: SpanTrace) -> _Style:
    return (span.style.font, round(span.style.size, 2), span.style.color)


def dominant_span(spans: Sequence[SpanTrace]) -> SpanTrace:
    """The first span of the style (font, size, color) that draws the most visible
    characters -- the body text, rather than a bold word or a superscript in it."""
    weight: Counter[_Style] = Counter()
    for span in spans:
        weight[_style_key(span)] += sum(1 for char in span.style.text if not char.isspace())
    best = max(weight.values())
    return next(span for span in spans if weight[_style_key(span)] == best)


@dataclass(frozen=True)
class TextBlock:
    """One or more vertically stacked lines sharing a left margin and a main font
    size -- a single-column paragraph, or a single unmergeable line.

    ``lines`` is every span of the block, line by line and in writing order within a
    line; for plain text, where each line is one span, that is simply its lines (the
    name predates EDT-19). ``rows`` groups those same spans by visual line. A block
    built from ``lines`` alone treats each span as its own line."""

    lines: tuple[SpanTrace, ...]
    rows: tuple[tuple[SpanTrace, ...], ...] = ()
    row_texts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.rows:
            object.__setattr__(self, "rows", tuple((line,) for line in self.lines))
        if len(self.row_texts) != len(self.rows):
            object.__setattr__(self, "row_texts", tuple("".join(s.style.text for s in row) for row in self.rows))

    @property
    def text(self) -> str:
        """Each line's text, joined with a space -- the paragraph as continuous prose."""
        return " ".join(self.row_texts)

    @property
    def fragmented(self) -> bool:
        """True when some line is more than one span (style runs, or pieces of a redraw)."""
        return any(len(row) > 1 for row in self.rows)

    @property
    def mixed_style(self) -> bool:
        """True when the block's spans differ in font, size or color: text that a
        redraw in one style would visibly change."""
        return len({_style_key(span) for span in self.lines if span.style.text.strip()}) > 1

    @property
    def dominant(self) -> SpanTrace:
        """The span whose style most of the block is drawn in (see dominant_span)."""
        return dominant_span(self.lines)

    def off_style_runs(self) -> int:
        """How many visible spans are not in the dominant style."""
        main = _style_key(self.dominant)
        return sum(1 for span in self.lines if span.style.text.strip() and _style_key(span) != main)

    def row_origin(self, index: int) -> tuple[float, float]:
        """Where a line starts: its first glyph's x, on its main text's baseline (a
        leading superscript doesn't set the baseline)."""
        row = self.rows[index]
        return (row[0].style.chars[0].origin[0], dominant_span(row).style.chars[0].origin[1])

    def row_bbox(self, index: int) -> tuple[float, float, float, float]:
        boxes = [span.style.bbox for span in self.rows[index]]
        return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


_SPAN_GAP_RATIO = 0.8
"""Where one span ends and the next begins, a gap wider than this many font sizes is a
gutter, not a word space, for block detection (see _rows)."""
_BETWEEN_TOLERANCE = 0.5  # pt

_Box = tuple[float, float, float, float]


@dataclass(frozen=True)
class _Row:
    """One visual line of a paragraph candidate: its spans in writing order, and the
    span that sets its baseline."""

    spans: tuple[SpanTrace, ...]
    text: str
    main: SpanTrace
    start: float
    """The x of its first glyph."""
    x_range: tuple[float, float]
    whole: bool
    """False when one of its spans has glyphs in another row too (a span that runs on
    across a gutter): such a row is never stacked into a paragraph."""

    @property
    def baseline(self) -> float:
        if self.main.style.chars:
            return self.main.style.chars[0].origin[1]
        return self.main.style.bbox[3]

    @property
    def origin(self) -> tuple[float, float]:
        """The left end of the line, on the main text's baseline."""
        return (self.start, self.baseline)


def _lone_row(span: SpanTrace) -> _Row:
    x0, _y0, x1, _y1 = span.style.bbox
    return _Row(spans=(span,), text=span.style.text, main=span, start=x0, x_range=(x0, x1), whole=True)


def _rows(spans: Sequence[SpanTrace]) -> list[_Row]:
    """The spans gathered into visual lines, in the order each line first appears in
    `spans`. A span with no glyphs is a line of its own, as it always was.

    The lines are engine.fonts.units.group_lines' (Line mode's), with one difference:
    a line is also cut where one span ends and another begins across a gap wider than
    _SPAN_GAP_RATIO x the font size. group_lines only cuts at 1.6x (SPEC.md 8.2 item 6,
    so a justified line stays one Line unit), which would let two columns with a narrow
    gutter pass as one paragraph line -- and then editing or deleting one column's block
    would take the other column with it. A gap inside one span (justified text set with
    TJ offsets or word spacing) never cuts, so a justified paragraph is still one block.
    """
    # Imported here: engine.fonts.units builds its block units on this module.
    from engine.fonts.units import group_lines

    # group_lines identifies spans by span_index; number them by position so a subset
    # of a page's spans, or hand-built spans, can't collide.
    numbered = [
        span.model_copy(update={"style": span.style.model_copy(update={"span_index": position})})
        for position, span in enumerate(spans)
    ]
    # Each piece: its line's text, and its glyphs as (offset in that text, span position, box).
    pieces: list[tuple[str, list[tuple[int, int, _Box]]]] = []
    for line in group_lines(numbered):
        current: list[tuple[int, int, _Box]] = []
        reach = -math.inf
        for offset, (entry, box) in enumerate(zip(line.glyph_map, line.boxes, strict=True)):
            if entry is None or box is None:
                continue
            position = entry[0]
            if current and position != current[-1][1] and line.rotation_degrees == 0.0:
                size = max(spans[position].style.size, spans[current[-1][1]].style.size)
                if box.bbox[0] - reach > _SPAN_GAP_RATIO * size:
                    pieces.append((line.text, current))
                    current = []
                    reach = -math.inf
            current.append((offset, position, box.bbox))
            reach = max(reach, box.bbox[2])
        if current:
            pieces.append((line.text, current))

    home: dict[int, int] = {}
    members: dict[int, list[int]] = {}
    for number, (_text, glyphs) in enumerate(pieces):
        for _offset, position, _bbox in glyphs:
            if position not in home:
                home[position] = number
                members.setdefault(number, []).append(position)

    rows: list[_Row] = []
    done: set[int] = set()
    for position, span in enumerate(spans):
        if position in done:
            continue
        if position not in home:
            rows.append(_lone_row(span))
            continue
        number = home[position]
        line_text, glyphs = pieces[number]
        row = tuple(spans[i] for i in members[number])
        done.update(members[number])
        whole = sum(len(s.style.chars) for s in row) == len(glyphs)
        if len(row) == 1:
            text = span.style.text
        elif whole:
            text = line_text[glyphs[0][0] : glyphs[-1][0] + 1]
        else:  # a span runs on into another column: the spans' own text, not the line's
            text = "".join(s.style.text for s in row)
        rows.append(
            _Row(
                spans=row,
                text=text,
                main=dominant_span(row),
                start=glyphs[0][2][0],
                x_range=(min(g[2][0] for g in glyphs), max(g[2][2] for g in glyphs)),
                whole=whole,
            )
        )
    return rows


def _same_style(a: _Row, b: _Row) -> bool:
    """The same main size, and at least one font in common: a bold word doesn't end a
    paragraph, but a heading set entirely in another face is not part of it."""
    if abs(a.main.style.size - b.main.style.size) >= 0.01:
        return False
    return bool({s.style.font for s in a.spans} & {s.style.font for s in b.spans})


def _is_next_line_of(previous: _Row, candidate: _Row) -> bool:
    if not (previous.whole and candidate.whole):
        return False
    spans = (*previous.spans, *candidate.spans)
    if any(span.style.rotation_degrees != 0.0 or not span.style.chars for span in spans):
        return False
    if not _same_style(previous, candidate):
        return False
    if abs(previous.origin[0] - candidate.origin[0]) > _LEFT_MARGIN_TOLERANCE:
        return False
    gap = candidate.origin[1] - previous.origin[1]
    line_height = previous.main.style.size
    return _MIN_LINE_GAP_RATIO * line_height <= gap <= _MAX_LINE_GAP_RATIO * line_height


def _line_between(previous: _Row, candidate: _Row, rows: Sequence[_Row]) -> bool:
    """Whether some other visible line sits between the two, overlapping them across the
    page. Stacking them would skip it: after an edit redraws a paragraph's middle line
    (appended to the content stream, so it no longer comes between them in stream order),
    its first and last lines must not be joined around it."""
    low = min(previous.x_range[0], candidate.x_range[0])
    high = max(previous.x_range[1], candidate.x_range[1])
    top = previous.baseline + _BETWEEN_TOLERANCE
    bottom = candidate.baseline - _BETWEEN_TOLERANCE
    return any(
        row is not previous
        and row is not candidate
        and row.text.strip()
        and top < row.baseline < bottom
        and row.x_range[0] < high
        and row.x_range[1] > low
        for row in rows
    )


def detect_blocks(spans: Sequence[SpanTrace]) -> list[TextBlock]:
    """Group spans into blocks: first into visual lines, then each line, in the order
    its first span comes in `spans` (the document order extract_page_spans gives), onto
    the block just before it when it is that block's next line -- and no other line lies
    between them. A line that doesn't extend the current block starts a new one -- every
    span ends up in exactly one block, even if that block has only one line.

    Only the block just before it, deliberately: columns written line by line across the
    page (a table, an invoice's items and amounts) alternate in the stream and so stay one
    block per line, as they always have, rather than turning a column of table cells into
    a paragraph that an edit would re-wrap across its rows."""
    rows = _rows(spans)
    blocks: list[list[_Row]] = []
    for row in rows:
        if blocks and _is_next_line_of(blocks[-1][-1], row) and not _line_between(blocks[-1][-1], row, rows):
            blocks[-1].append(row)
        else:
            blocks.append([row])
    return [
        TextBlock(
            lines=tuple(span for row in block for span in row.spans),
            rows=tuple(row.spans for row in block),
            row_texts=tuple(row.text for row in block),
        )
        for block in blocks
    ]


def find_block_containing(blocks: Sequence[TextBlock], span: SpanTrace) -> TextBlock | None:
    """The block a specific span (matched by identity, not equality -- two
    blank lines can otherwise compare equal) belongs to, if any."""
    for block in blocks:
        if any(line is span for line in block.lines):
            return block
    return None


_ALIGN_TOLERANCE = 1.5


def _center(span: SpanTrace) -> float:
    return (span.style.bbox[0] + span.style.bbox[2]) / 2


def detect_alignment(span: SpanTrace, page_spans: list[SpanTrace], page_width: float) -> str:
    """How a single line is aligned -- "left", "right" or "center" -- so an
    edit that changes its width can keep the edge (or center) that matters.

    Found on a real statement: a replacement always kept the original's left
    edge, so shorter text in a right-aligned header no longer ended at the
    margin. The evidence, in order, from the other lines on the page:

    1. starting at the page's text left margin, or sharing a left edge with
       another line -> left (the common case, and a column of body text);
    2. sharing a right edge with another line that starts elsewhere -> right;
    3. sharing a center with another line that starts elsewhere, or sitting
       on the page's center -> center;
    4. ending at the page's text right margin -> right;
    5. otherwise left. Rotated text is always treated as left.
    """
    if span.style.rotation_degrees != 0.0 or not span.style.text.strip():
        return "left"
    x0, _y0, x1, _y1 = span.style.bbox
    baseline = span.style.chars[0].origin[1] if span.style.chars else span.style.bbox[3]
    visible = [s for s in page_spans if s.style.text.strip() and s.style.rotation_degrees == 0.0]
    others = [s for s in visible if s.style.chars and abs(s.style.chars[0].origin[1] - baseline) > _ALIGN_TOLERANCE]

    def near(a: float, b: float) -> bool:
        return abs(a - b) <= _ALIGN_TOLERANCE

    left_margin = min(s.style.bbox[0] for s in visible) if visible else x0
    right_margin = max(s.style.bbox[2] for s in visible) if visible else x1
    if near(x0, left_margin) or any(near(x0, o.style.bbox[0]) for o in others):
        return "left"
    if any(near(x1, o.style.bbox[2]) and not near(x0, o.style.bbox[0]) for o in others):
        return "right"
    if near(_center(span), page_width / 2) or any(
        near(_center(span), _center(o)) and not near(x0, o.style.bbox[0]) for o in others
    ):
        return "center"
    if near(x1, right_margin):
        return "right"
    return "left"
