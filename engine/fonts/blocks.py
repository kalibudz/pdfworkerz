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
