"""EDT-01, EDT-02, EDT-03, EDT-04, EDT-05, EDT-06, EDT-07, FNT-11: typed text-editing Ops.

Each Op finds its target span(s) with :func:`engine.fonts.style.extract_page_spans`
and draws through :mod:`engine.edit`. A match is only supported when it lies
entirely within one span (SPEC.md's Tier model operates per span); a search
string split across two spans is not found. `require_tier` is how these Ops
implement "never guess silently" ahead of a UI: a font resolution weaker
than the tier requested raises :class:`~engine.errors.OpValidationError`
instead of drawing with it.
"""

from __future__ import annotations

import math
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict

from engine.document import Document
from engine.edit import (
    EditResult,
    copy_span_style,
    insert_styled_text,
    insert_text_near,
    move_resize_block,
    reflow_block,
    replace_span_text,
    resolve_font_for_span,
)
from engine.errors import OpValidationError
from engine.fonts.blocks import TextBlock, detect_blocks, find_block_containing
from engine.fonts.choose import family_of, is_bold_italic, resolve_chosen_font
from engine.fonts.match import FontCandidate, build_font_index
from engine.fonts.style import SpanTrace, dedupe_texttrace, extract_page_spans
from engine.ops.base import Op, register_op

_TIER_ORDER = {"exact": 0, "approximate": 1, "fallback": 2}
_SAME_ORIGIN_TOLERANCE = 0.05  # pt

_font_index_cache: list[FontCandidate] | None = None


def _font_index() -> list[FontCandidate]:
    """A process-wide cache: scanning system fonts is expensive and the result
    doesn't change within a session. A longer-lived cache belongs at the server
    layer once one exists; this is enough for the CLI and for tests."""
    global _font_index_cache
    if _font_index_cache is None:
        _font_index_cache = build_font_index()
    return _font_index_cache


def _tier_problem(result: EditResult, require_tier: str) -> str | None:
    if _TIER_ORDER[result.tier] <= _TIER_ORDER[require_tier]:
        return None
    return (
        f"font resolution fell back to tier {result.tier!r}, weaker than the "
        f"required {require_tier!r} (confidence {result.confidence:.2f}: {result.note})"
    )


def _check_tier(result: EditResult, require_tier: str, *, where: str) -> None:
    problem = _tier_problem(result, require_tier)
    if problem:
        raise OpValidationError(f"{where}: {problem}")


def _compile_pattern(match: str, mode: str, case_sensitive: bool, whole_word: bool = False) -> re.Pattern[str]:
    flags = 0 if case_sensitive else re.IGNORECASE
    pattern = match if mode == "regex" else re.escape(match)
    if whole_word:
        # Lookarounds rather than a word boundary, so a match that starts or ends with punctuation still works.
        pattern = rf"(?<!\w)(?:{pattern})(?!\w)"
    return re.compile(pattern, flags)


def _page_might_match(document: Document, page_index: int, pattern: re.Pattern[str]) -> bool:
    """A cheap texttrace-only check, so pages without a match skip extract_page_spans (which
    serializes and re-parses the whole document): a no-match search of 1000 pages took ~100s."""
    spans = dedupe_texttrace(document.raw[page_index].get_texttrace())
    return any(pattern.search("".join(chr(char[0]) for char in span["chars"])) for span in spans)


def _pages_to_search(document: Document, page_index: int | None) -> range:
    return range(document.page_count) if page_index is None else range(page_index, page_index + 1)


def _find_first_match(
    document: Document, page_index: int, pattern: re.Pattern[str]
) -> tuple[SpanTrace, re.Match[str]] | None:
    for span in extract_page_spans(document.raw, page_index):
        found = pattern.search(span.style.text)
        if found:
            return span, found
    return None


def _span_at(document: Document, page_index: int, span_index: int) -> SpanTrace:
    """The span UI-02's click-to-edit overlay is targeting, found by its
    position in a fresh extraction rather than by searching for its text --
    unlike ReplaceTextOp, this never risks editing a different span that
    happens to contain the same text elsewhere on the page. Both callers
    below need a fresh extraction anyway (span indices are only meaningful
    against the page's *current* state), and neither can afford to just let
    a bad index raise IndexError -- that would be a 500, not a clear
    "never guess silently" validation error."""
    spans = extract_page_spans(document.raw, page_index)
    if not 0 <= span_index < len(spans):
        raise OpValidationError(f"page {page_index} has {len(spans)} span(s); span_index {span_index} is out of range")
    return spans[span_index]


def find_span_index(document: Document, page_index: int, text: str) -> int:
    """The index of the first span on `page_index` whose text contains
    `text` literally -- how text-addressed callers (the CLI) get the
    positional index the index-precise Ops above take."""
    for span in extract_page_spans(document.raw, page_index):
        if text in span.style.text:
            return span.style.span_index
    raise OpValidationError(f"no span on page {page_index} contains {text!r}")


class PreviewResult(BaseModel):
    """What committing a text edit *would* do, without doing it -- the same
    font-resolution decision replace_span_text makes, reported for UI-02's
    live preview and UI-03's inspector "Match" field (SPEC.md section 8.1)
    as the user types, before they've committed to anything."""

    model_config = ConfigDict(frozen=True)

    tier: str
    """"exact" | "approximate" | "fallback" -- a plain str, matching FontResolution.tier
    and EditResult.tier's own type, since both are constructed from the same values."""
    confidence: float
    requires_approval: bool
    note: str


@register_op
class PreviewTextOp(Op):
    """A read-only preview of the font resolution a replacement would use --
    never draws, redacts or verifies anything, so (like PageSpansOp) it's
    never journaled; server/app.py calls it directly. `span_index` is
    positional in a fresh `extract_page_spans` call on `page_index`, not a
    text search (see _span_at)."""

    op: Literal["preview_text"] = "preview_text"
    page_index: int
    span_index: int
    needed_text: str

    def apply(self, document: Document) -> PreviewResult:
        span = _span_at(document, self.page_index, self.span_index)
        resolution = resolve_font_for_span(document, self.page_index, span, self.needed_text, font_index=_font_index())
        return PreviewResult(
            tier=resolution.tier,
            confidence=resolution.confidence,
            requires_approval=resolution.requires_approval,
            note=resolution.note,
        )


@register_op
class ReplaceSpanTextOp(Op):
    """EDT-02, index-precise: replace exactly the span at `span_index`
    (found the same way PreviewTextOp finds it), never any other span that
    happens to contain the same text elsewhere on the page -- what UI-02's
    click-to-edit commit step needs and ReplaceTextOp's text search can't
    guarantee. Journaled normally through the generic ops endpoint, unlike
    its read-only sibling above: this one really does edit the document."""

    op: Literal["replace_span_text"] = "replace_span_text"
    page_index: int
    span_index: int
    new_text: str
    require_tier: Literal["exact", "approximate", "fallback"] = "approximate"
    """Matches every other text-editing Op's default (SPEC.md section 5.3:
    only a fallback match is rejected outright). The click-to-edit overlay
    that drives this Op gets to see PreviewTextOp's result and ask the user
    to confirm *before* committing, so it passes "fallback" explicitly once
    that confirmation happens -- this default is what a caller with no such
    UI-side gate gets instead."""
    fit: bool = False
    verify: bool = True

    def apply(self, document: Document) -> EditResult:
        span = _span_at(document, self.page_index, self.span_index)
        result = replace_span_text(
            document, self.page_index, span, self.new_text, font_index=_font_index(), fit=self.fit, verify=self.verify
        )
        _check_tier(result, self.require_tier, where=type(self).__name__)
        return result


@register_op
class CopyStyleOp(Op):
    """EDT-07, format painter: give the span at (`target_page_index`,
    `target_span_index`) the font, size, color and spacing of the span at
    (`page_index`, `span_index`), keeping the target's own text. Both spans
    are addressed by index (see _span_at), like ReplaceSpanTextOp, because
    the UI picks them by clicking, never by searching."""

    op: Literal["copy_style"] = "copy_style"
    page_index: int
    span_index: int
    target_page_index: int
    target_span_index: int
    require_tier: Literal["exact", "approximate", "fallback"] = "approximate"
    verify: bool = True

    def apply(self, document: Document) -> EditResult:
        if (self.page_index, self.span_index) == (self.target_page_index, self.target_span_index):
            raise OpValidationError("copy_style: source and target are the same span")
        # Both looked up before anything is redrawn: indices describe the page as it is now.
        source = _span_at(document, self.page_index, self.span_index)
        target = _span_at(document, self.target_page_index, self.target_span_index)
        if not target.style.text.strip():
            raise OpValidationError("copy_style: the target span has no visible text to restyle")
        result = copy_span_style(
            document,
            self.page_index,
            source,
            self.target_page_index,
            target,
            font_index=_font_index(),
            verify=self.verify,
        )
        _check_tier(result, self.require_tier, where=type(self).__name__)
        return result


def _block_at(document: Document, page_index: int, span_index: int) -> TextBlock:
    """The block containing the span at `span_index`. detect_blocks needs its
    lines consecutive; content-stream order gives that for freshly authored
    pages but not after an edit (redrawn text is appended to the end of the
    stream), while top-to-bottom order gives it after an edit but can
    interleave side-by-side columns. Both are tried; the larger block wins."""
    spans = extract_page_spans(document.raw, page_index)
    _span_at(document, page_index, span_index)  # range check with the standard error message
    target = spans[span_index]  # the same object detect_blocks sees: find_block_containing matches by identity
    visual = sorted(
        spans, key=lambda s: (round(s.style.chars[0].origin[1], 1) if s.style.chars else 0.0, s.style.bbox[0])
    )
    candidates = [find_block_containing(detect_blocks(order), target) for order in (spans, visual)]
    blocks = [block for block in candidates if block is not None]
    if not blocks:
        raise OpValidationError("the span could not be placed in a text block")
    return max(blocks, key=lambda block: len(block.lines))


@register_op
class MoveTextBlockOp(Op):
    """EDT-05: move the text block containing the span at `span_index` by
    (`dx`, `dy`) points (MuPDF page space: +x right, +y down), and/or
    re-wrap it to `width` points. Its text and style are unchanged."""

    op: Literal["move_text_block"] = "move_text_block"
    page_index: int
    span_index: int
    dx: float = 0.0
    dy: float = 0.0
    width: float | None = None
    require_tier: Literal["exact", "approximate", "fallback"] = "approximate"
    verify: bool = True

    def apply(self, document: Document) -> list[EditResult]:
        if self.width is not None and self.width <= 0:
            raise OpValidationError("move_text_block: width must be positive")
        if self.dx == 0 and self.dy == 0 and self.width is None:
            raise OpValidationError("move_text_block: nothing to do (set dx/dy and/or width)")
        block = _block_at(document, self.page_index, self.span_index)
        results = move_resize_block(
            document,
            self.page_index,
            block,
            dx=self.dx,
            dy=self.dy,
            width=self.width,
            font_index=_font_index(),
            verify=self.verify,
        )
        for result in results:
            _check_tier(result, self.require_tier, where=type(self).__name__)
        return results


def _span_by_origin(document: Document, page_index: int, origin: tuple[float, float], text: str) -> SpanTrace:
    """Re-find a span listed before earlier redraws on the same page (which renumber spans)."""
    for span in extract_page_spans(document.raw, page_index):
        if (
            span.style.text == text
            and span.style.chars
            and math.dist(span.style.chars[0].origin, origin) < _SAME_ORIGIN_TOLERANCE
        ):
            return span
    raise OpValidationError(f"the text {text!r} changed while earlier matches on page {page_index} were replaced")


class _FindReplaceOp(Op):
    """Shared fields for ReplaceTextOp and DeleteTextOp."""

    match: str
    mode: Literal["literal", "regex"] = "literal"
    case_sensitive: bool = True
    page_index: int | None = None
    """None means every page."""
    require_tier: Literal["exact", "approximate", "fallback"] = "approximate"
    fit: bool = False
    """FNT-10: match the original span's width via Tc/Tz. Off by default -- see
    engine.edit.replace_span_text's docstring for the chainability trade-off."""
    verify: bool = True
    """FNT-12: render before/after and confirm the text landed (engine.edit)."""
    whole_word: bool = False
    """EDT-02: only match where the text isn't part of a longer word ("cat" but not "category")."""

    def _replacement_for(self, matched_text: str) -> str:
        raise NotImplementedError

    def apply(self, document: Document) -> list[EditResult]:
        font_index = _font_index()
        pattern = _compile_pattern(self.match, self.mode, self.case_sensitive, self.whole_word)
        results: list[EditResult] = []

        for page_index in _pages_to_search(document, self.page_index):
            if not _page_might_match(document, page_index, pattern):
                continue
            # Which spans to change is decided once, up front, and each is redrawn once with all of
            # its matches replaced. Searching again after every redraw looped forever whenever the
            # replacement itself contained the match ("Hello" -> "Hello there").
            targets = [
                (span.style.chars[0].origin, span.style.text)
                for span in extract_page_spans(document.raw, page_index)
                if span.style.chars and pattern.search(span.style.text)
            ]
            for origin, text in targets:
                span = _span_by_origin(document, page_index, origin, text)
                new_text = pattern.sub(lambda m: self._replacement_for(m.group()), text)
                if new_text == text:
                    continue
                result = replace_span_text(
                    document, page_index, span, new_text, font_index=font_index, fit=self.fit, verify=self.verify
                )
                _check_tier(result, self.require_tier, where=type(self).__name__)
                results.append(result)
        return results


@register_op
class ReplaceTextOp(_FindReplaceOp):
    """EDT-02: find text (literal or regex) and replace it, matching the original style."""

    op: Literal["replace_text"] = "replace_text"
    replacement: str = ""

    def _replacement_for(self, matched_text: str) -> str:
        return self.replacement


@register_op
class DeleteTextOp(_FindReplaceOp):
    """EDT-04: find and remove text, closing the gap in its style-matched span."""

    op: Literal["delete_text"] = "delete_text"

    def _replacement_for(self, matched_text: str) -> str:
        return ""


class _StyleChange(Op):
    """EDT-06: the style fields shared by RestyleTextOp and RestyleSpanOp. Any field left
    unset keeps the text's current value."""

    size: float | None = None
    color: tuple[float, float, float] | None = None
    font: str | None = None
    """A font family to switch to (see GET /fonts or `pdfworkerz fonts` for the choices)."""
    bold: bool | None = None
    italic: bool | None = None
    require_tier: Literal["exact", "approximate", "fallback"] = "approximate"
    verify: bool = True

    def _check_something_changes(self) -> None:
        if all(value is None for value in (self.size, self.color, self.font, self.bold, self.italic)):
            raise OpValidationError(f"{type(self).__name__}: set at least one of size, color, font, bold or italic")

    def _restyle(
        self, document: Document, page_index: int, span: SpanTrace, font_index: list[FontCandidate]
    ) -> EditResult:
        chosen = None
        if self.font is not None or self.bold is not None or self.italic is not None:
            current_bold, current_italic = is_bold_italic(span.style.font, font_index)
            chosen = resolve_chosen_font(
                self.font or family_of(span.style.font, font_index),
                bold=current_bold if self.bold is None else self.bold,
                italic=current_italic if self.italic is None else self.italic,
                text=span.style.text,
                font_index=font_index,
            )
        result = replace_span_text(
            document,
            page_index,
            span,
            span.style.text,
            font_index=font_index,
            override_size=self.size,
            override_color=self.color,
            override_font=chosen,
            verify=self.verify,
        )
        _check_tier(result, self.require_tier, where=type(self).__name__)
        return result


@register_op
class RestyleTextOp(_StyleChange):
    """EDT-06: change the font, weight, slant, size and/or color of every span that
    matches, leaving its wording unchanged."""

    op: Literal["restyle_text"] = "restyle_text"
    match: str
    mode: Literal["literal", "regex"] = "literal"
    case_sensitive: bool = True
    page_index: int | None = None

    def apply(self, document: Document) -> list[EditResult]:
        self._check_something_changes()
        font_index = _font_index()
        pattern = _compile_pattern(self.match, self.mode, self.case_sensitive)
        results: list[EditResult] = []

        for page_index in _pages_to_search(document, self.page_index):
            if not _page_might_match(document, page_index, pattern):
                continue
            targets = [
                (s.style.chars[0].origin, s.style.text)
                for s in extract_page_spans(document.raw, page_index)
                if s.style.chars and pattern.search(s.style.text)
            ]
            for origin, text in targets:
                span = _span_by_origin(document, page_index, origin, text)
                results.append(self._restyle(document, page_index, span, font_index))
        return results


@register_op
class RestyleSpanOp(_StyleChange):
    """EDT-06 for the UI: restyle exactly the span the user selected, by index."""

    op: Literal["restyle_span"] = "restyle_span"
    page_index: int
    span_index: int

    def apply(self, document: Document) -> EditResult:
        self._check_something_changes()
        span = _span_at(document, self.page_index, self.span_index)
        return self._restyle(document, self.page_index, span, _font_index())


@register_op
class InsertTextOp(Op):
    """EDT-03: add new text at `position` (a baseline point), either matching the style of
    an existing span (`reference_match`) or in an explicit style (`font` and `size`, plus
    optional `color`, `bold`, `italic`). With both, the explicit fields override the
    matched style. Nothing existing is touched."""

    op: Literal["insert_text"] = "insert_text"
    page_index: int
    text: str
    position: tuple[float, float]
    reference_match: str | None = None
    """A literal substring identifying the span whose style to copy."""
    reference_case_sensitive: bool = True
    font: str | None = None
    size: float | None = None
    color: tuple[float, float, float] | None = None
    bold: bool | None = None
    italic: bool | None = None
    require_tier: Literal["exact", "approximate", "fallback"] = "approximate"
    verify: bool = True

    def apply(self, document: Document) -> EditResult:
        font_index = _font_index()
        if self.size is not None and self.size <= 0:
            raise OpValidationError("insert_text: size must be positive")
        if self.reference_match is None:
            if self.font is None or self.size is None:
                raise OpValidationError("insert_text: give reference_match, or an explicit font and size")
            chosen = resolve_chosen_font(
                self.font, bold=bool(self.bold), italic=bool(self.italic), text=self.text, font_index=font_index
            )
            result = insert_styled_text(
                document,
                self.page_index,
                self.text,
                self.position,
                font=chosen,
                size=self.size,
                color=self.color or (0.0, 0.0, 0.0),
                verify=self.verify,
            )
            _check_tier(result, self.require_tier, where=self.op)
            return result

        pattern = _compile_pattern(self.reference_match, "literal", self.reference_case_sensitive)
        found = _find_first_match(document, self.page_index, pattern)
        if found is None:
            raise OpValidationError(f"insert_text: no span matches reference text {self.reference_match!r}")
        reference_span, _match_obj = found
        chosen_font = None
        if self.font is not None or self.bold is not None or self.italic is not None:
            current_bold, current_italic = is_bold_italic(reference_span.style.font, font_index)
            chosen_font = resolve_chosen_font(
                self.font or family_of(reference_span.style.font, font_index),
                bold=current_bold if self.bold is None else self.bold,
                italic=current_italic if self.italic is None else self.italic,
                text=self.text,
                font_index=font_index,
            )
        result = insert_text_near(
            document,
            self.page_index,
            reference_span,
            self.text,
            self.position,
            font_index=font_index,
            verify=self.verify,
            override_font=chosen_font,
            override_size=self.size,
            override_color=self.color,
        )
        _check_tier(result, self.require_tier, where=self.op)
        return result


@register_op
class ReflowTextOp(Op):
    """FNT-11: replace an entire paragraph's text, re-wrapping it across the
    paragraph's own existing lines (engine.fonts.blocks/engine.fonts.reflow).
    `match` identifies which paragraph to reflow -- a literal substring found
    within any one of its lines."""

    op: Literal["reflow_text"] = "reflow_text"
    match: str
    new_text: str
    page_index: int
    case_sensitive: bool = True
    require_tier: Literal["exact", "approximate", "fallback"] = "approximate"
    allow_overflow: bool = False
    """When False (default), raise instead of leaving text undrawn because
    `new_text` needed more lines than the paragraph has (engine.edit.reflow_block's
    "overflow" outcome) -- the same "never guess silently" principle require_tier
    implements for a weak font match, applied to an incomplete edit instead."""
    verify: bool = True

    def apply(self, document: Document) -> list[EditResult]:
        font_index = _font_index()
        pattern = _compile_pattern(self.match, "literal", self.case_sensitive)
        spans = extract_page_spans(document.raw, self.page_index)
        target_line = next((span for span in spans if pattern.search(span.style.text)), None)
        if target_line is None:
            raise OpValidationError(f"reflow_text: no line matches {self.match!r}")

        block = find_block_containing(detect_blocks(spans), target_line)
        if block is None:
            raise OpValidationError("reflow_text: matched line could not be placed in a block")

        results = reflow_block(
            document, self.page_index, block, self.new_text, font_index=font_index, verify=self.verify
        )
        # Report every problem at once: checking overflow first used to hide a weak font match behind it.
        problems = []
        if not self.allow_overflow and "overflow:" in results[-1].note:
            problems.append(results[-1].note)
        weak = next((p for p in (_tier_problem(r, self.require_tier) for r in results) if p), None)
        if weak:
            problems.append(weak)
        if problems:
            raise OpValidationError(f"{type(self).__name__}: " + "; ".join(problems))
        return results
