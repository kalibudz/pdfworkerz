"""FNT-11 (block detection half): group a page's spans into left-aligned,
single-column text blocks -- SPEC.md section 5.4's "block" that reflow
wraps text within.

Deliberately narrow scope, stated up front rather than discovered by a
caller later: a block is detected only for spans that share the same font
and size, sit at the same left margin (within a small tolerance), are
rotated 0 degrees, and are vertically stacked at a plausible single-line-
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

from dataclasses import dataclass

from engine.fonts.style import SpanTrace

_MIN_LINE_GAP_RATIO = 0.5
_MAX_LINE_GAP_RATIO = 3.0
_LEFT_MARGIN_TOLERANCE = 3.0


@dataclass(frozen=True)
class TextBlock:
    """One or more vertically stacked lines (SpanTrace) sharing a left margin,
    font and size -- a single-column paragraph, or a single unmergeable line."""

    lines: tuple[SpanTrace, ...]

    @property
    def text(self) -> str:
        """Each line's text, joined with a space -- the paragraph as continuous prose."""
        return " ".join(line.style.text for line in self.lines)


def _same_style(a: SpanTrace, b: SpanTrace) -> bool:
    return a.style.font == b.style.font and abs(a.style.size - b.style.size) < 0.01


def _left_aligned(a: SpanTrace, b: SpanTrace) -> bool:
    return abs(a.style.chars[0].origin[0] - b.style.chars[0].origin[0]) <= _LEFT_MARGIN_TOLERANCE


def _is_next_line_of(previous: SpanTrace, candidate: SpanTrace) -> bool:
    if previous.style.rotation_degrees != 0.0 or candidate.style.rotation_degrees != 0.0:
        return False
    if not previous.style.chars or not candidate.style.chars:
        return False
    if not _same_style(previous, candidate) or not _left_aligned(previous, candidate):
        return False
    gap = candidate.style.chars[0].origin[1] - previous.style.chars[0].origin[1]
    line_height = previous.style.size
    return _MIN_LINE_GAP_RATIO * line_height <= gap <= _MAX_LINE_GAP_RATIO * line_height


def detect_blocks(spans: list[SpanTrace]) -> list[TextBlock]:
    """Group spans, in the document order extract_page_spans already gives
    them, into blocks. A span that doesn't extend the current block starts a
    new one -- every span ends up in exactly one block, even if that block
    has only one line."""
    blocks: list[list[SpanTrace]] = []
    for span in spans:
        if blocks and _is_next_line_of(blocks[-1][-1], span):
            blocks[-1].append(span)
        else:
            blocks.append([span])
    return [TextBlock(lines=tuple(block)) for block in blocks]


def find_block_containing(blocks: list[TextBlock], span: SpanTrace) -> TextBlock | None:
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
