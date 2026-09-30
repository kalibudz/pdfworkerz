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

import dataclasses
import difflib
import math
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from engine.document import Document
from engine.edit import (
    EditResult,
    LineRestyle,
    anchored_position,
    copy_span_style,
    insert_styled_text,
    insert_text_near,
    move_resize_block,
    recolor_line_range,
    reflow_block,
    replace_span_text,
    resolve_font_for_span,
    rewrite_line_hunks,
)
from engine.errors import OpValidationError
from engine.fonts.blocks import TextBlock, detect_blocks, find_block_containing
from engine.fonts.choose import family_is_inferred, family_of, is_bold_italic, resolve_chosen_font
from engine.fonts.match import FontCandidate, build_font_index, scan_font_directory, user_fonts_dir
from engine.fonts.resolve import FontResolution
from engine.fonts.style import SpanTrace, dedupe_texttrace, extract_page_spans
from engine.fonts.units import TextLine, group_lines, split_words, text_units
from engine.ops.base import Op, register_op

_TIER_ORDER = {"exact": 0, "approximate": 1, "fallback": 2}
_SAME_ORIGIN_TOLERANCE = 0.05  # pt

_font_index_cache: list[FontCandidate] | None = None
_user_index_cache: tuple[tuple[str, int | None], list[FontCandidate]] | None = None


def _font_index() -> list[FontCandidate]:
    """The font index, cached per process: scanning the system's fonts is expensive and
    doesn't change within a session. The user's own library (engine.fonts.library) is
    small and does change -- a font added from the Fonts dialog must be used by the very
    next edit -- so it is rescanned whenever its folder (or that folder's contents,
    by modification time) changes."""
    global _font_index_cache, _user_index_cache
    if _font_index_cache is None:
        _font_index_cache = build_font_index(include_user=False)
    folder = user_fonts_dir()
    key = (str(folder), folder.stat().st_mtime_ns if folder.is_dir() else None)
    if _user_index_cache is None or _user_index_cache[0] != key:
        _user_index_cache = (key, scan_font_directory(folder, "user"))
    return _user_index_cache[1] + _font_index_cache


def reset_font_index() -> None:
    """Forget the user library's cached scan: a font was added to or removed from it
    (engine.fonts.library), and the next edit must see that even when the folder's
    modification time is too coarse to show the change."""
    global _user_index_cache
    _user_index_cache = None


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


def _check_search(match: str, mode: str) -> None:
    """Refuse a search that can't mean what it says: an invalid regex, or a pattern that
    matches empty text -- which `re.sub` would apply between every character."""
    if not match:
        raise OpValidationError("the text to find is empty")
    try:
        compiled = re.compile(match if mode == "regex" else re.escape(match))
    except re.error as exc:
        raise OpValidationError(f"the regular expression {match!r} is not valid: {exc}") from exc
    if compiled.fullmatch(""):
        raise OpValidationError(f"the pattern {match!r} matches empty text, so it would match everywhere")


def refuse_empty_matches(pattern: re.Pattern[str], text: str) -> None:
    """A zero-width pattern that needs context (a word boundary, a lookahead) passes _check_search (it can't
    match empty text on its own) but matches between characters of real text, where a
    substitution would insert the replacement everywhere. Refuse it on the text itself."""
    if any(found.start() == found.end() for found in pattern.finditer(text)):
        raise OpValidationError(
            f"the pattern {pattern.pattern!r} matches an empty stretch of text, so it would apply between characters"
        )


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
        resolution = resolve_font_for_span(
            document, self.page_index, span, self.needed_text, font_index=_font_index(), flag_for_research=False
        )
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
    """The block containing the span at `span_index` (EDT-19: the paragraph its whole
    line belongs to, whatever style runs the line is in). detect_blocks needs its
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
    return max(blocks, key=lambda block: (len(block.rows), len(block.lines)))


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

    @model_validator(mode="after")
    def _valid_search(self) -> _FindReplaceOp:
        _check_search(self.match, self.mode)
        return self

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
                refuse_empty_matches(pattern, text)
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

    def _restyles(self) -> bool:
        return any(value is not None for value in (self.size, self.color, self.font, self.bold, self.italic))

    def _chosen_font(self, span: SpanTrace, text: str, font_index: list[FontCandidate]) -> FontResolution | None:
        """The font to draw `text` in when the family, weight or slant is being changed:
        `span`'s own family, weight and slant with the requested ones put in their place.
        None when none of the three is set -- the text then keeps its font."""
        if self.font is None and self.bold is None and self.italic is None:
            return None
        current_bold, current_italic = is_bold_italic(span.style.font, font_index)
        family = self.font or family_of(span.style.font, font_index)
        chosen = resolve_chosen_font(
            family,
            bold=current_bold if self.bold is None else self.bold,
            italic=current_italic if self.italic is None else self.italic,
            text=text,
            font_index=font_index,
        )
        if self.font is None and family_is_inferred(span.style.font, font_index):
            # A weight or slant change on a font whose family is unknown draws it in a guessed
            # family: a different typeface, so it is approximate and needs the user's approval.
            chosen = dataclasses.replace(
                chosen,
                tier="approximate" if chosen.tier == "exact" else chosen.tier,
                requires_approval=True,
                note=f"{chosen.note}; the text's own font ({span.style.font}) is not a known family, "
                f"so {family} was used in its place",
            )
        return chosen

    def _restyle(
        self,
        document: Document,
        page_index: int,
        span: SpanTrace,
        font_index: list[FontCandidate],
        text: str | None = None,
    ) -> EditResult:
        """Redraw `span` in the changed style, with `text` in place of its own wording
        when given (EditSpanOp: new words and a new style in one draw)."""
        text = span.style.text if text is None else text
        chosen = self._chosen_font(span, text, font_index)
        result = replace_span_text(
            document,
            page_index,
            span,
            text,
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
    whole_word: bool = False
    page_index: int | None = None

    @model_validator(mode="after")
    def _valid_search(self) -> RestyleTextOp:
        _check_search(self.match, self.mode)
        return self

    def apply(self, document: Document) -> list[EditResult]:
        self._check_something_changes()
        font_index = _font_index()
        pattern = _compile_pattern(self.match, self.mode, self.case_sensitive, self.whole_word)
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
                refuse_empty_matches(pattern, text)
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
class EditSpanOp(_StyleChange):
    """UI-03's inspector editor: new wording and/or a new style for exactly the span the
    user selected, drawn and verified once -- one history entry, one undo -- where a
    replace_span_text followed by a restyle_span would be two, and the second would have
    to find its span again after the first had redrawn the line. Any field left unset
    keeps the span's current value."""

    op: Literal["edit_span"] = "edit_span"
    page_index: int
    span_index: int
    new_text: str | None = None

    def apply(self, document: Document) -> EditResult:
        span = _span_at(document, self.page_index, self.span_index)
        restyles = any(value is not None for value in (self.size, self.color, self.font, self.bold, self.italic))
        if not restyles and (self.new_text is None or self.new_text == span.style.text):
            raise OpValidationError("edit_span: change the text or at least one of size, color, font, bold or italic")
        if not restyles:
            result = replace_span_text(
                document, self.page_index, span, self.new_text or "", font_index=_font_index(), verify=self.verify
            )
            _check_tier(result, self.require_tier, where=type(self).__name__)
            return result
        return self._restyle(document, self.page_index, span, _font_index(), self.new_text)


@register_op
class InsertTextOp(Op):
    """EDT-03: add new text at `position` (a baseline point), either matching the style of
    an existing span (`reference_match`) or in an explicit style (`font` and `size`, plus
    optional `color`, `bold`, `italic`). With both, the explicit fields override the
    matched style. Nothing existing is touched."""

    op: Literal["insert_text"] = "insert_text"
    page_index: int
    text: str
    position: tuple[float, float] | None = None
    """The baseline point to start at. Leave unset to place the text by `anchor` instead."""
    reference_match: str | None = None
    """A literal substring identifying the span whose style to copy."""
    anchor: Literal["below", "above", "after", "before"] | None = None
    """Place the text one line below/above, or one space after/before, the reference span."""
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
        if (self.position is None) == (self.anchor is None):
            raise OpValidationError("insert_text: give either a position or an anchor (not both)")
        if self.reference_match is None:
            if self.anchor is not None:
                raise OpValidationError("insert_text: an anchor needs reference_match (the text to place it by)")
            if self.font is None or self.size is None:
                raise OpValidationError("insert_text: give reference_match, or an explicit font and size")
            if self.position is None:  # unreachable after the checks above; narrows the type
                raise OpValidationError("insert_text: give a position")
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
        position = self.position
        if position is None:
            if self.anchor is None:  # unreachable after the checks above; narrows the type
                raise OpValidationError("insert_text: give a position or an anchor")
            resolution = chosen_font or resolve_font_for_span(
                document, self.page_index, reference_span, self.text, font_index=font_index
            )
            chosen_font = resolution
            size = self.size or reference_span.style.size
            position = anchored_position(reference_span, self.anchor, self.text, resolution, size)
        result = insert_text_near(
            document,
            self.page_index,
            reference_span,
            self.text,
            position,
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
    align: Literal["left", "justify"] = "left"
    """FNT-17: "justify" spreads every line but the last to the paragraph's full width."""
    grow: bool = False
    """FNT-17: let the paragraph take more lines than it has, moving the text below it in the
    same column down; refused if that text would leave the page or pass images/drawings/links."""

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
            document,
            self.page_index,
            block,
            self.new_text,
            font_index=font_index,
            verify=self.verify,
            align=self.align,
            grow=self.grow,
        )
        _check_reflow(results, self.require_tier, self.allow_overflow, where=type(self).__name__)
        return results


def _check_reflow(results: list[EditResult], require_tier: str, allow_overflow: bool, *, where: str) -> None:
    """Refuse a reflow that left text undrawn (unless overflow is allowed) or fell to a weaker
    font tier than required. Every problem is reported at once: checking overflow first used
    to hide a weak font match behind it."""
    problems = []
    if results and not allow_overflow and "overflow:" in results[-1].note:
        problems.append(results[-1].note)
    weak = next((p for p in (_tier_problem(r, require_tier) for r in results) if p), None)
    if weak:
        problems.append(weak)
    if problems:
        raise OpValidationError(f"{where}: " + "; ".join(problems))


_MIN_KEPT_MATCH = 3
"""Unchanged stretches shorter than this between two changes, within one style run, are
redrawn with them: one hunk reads better than a word rebuilt from single-letter pieces."""

_StyleKey = tuple[str, float, tuple[float, float, float]] | None


def _style_of(span: SpanTrace) -> _StyleKey:
    return span.style.font, round(span.style.size, 2), span.style.color


def _unit_hunks(
    line: TextLine, styles: list[_StyleKey], start: int, end: int, new_text: str, *, restyle: bool
) -> list[tuple[int, int, str]]:
    """The hunks (start, end, text) -- offsets into the line's text -- that turn
    ``line.text[start:end]`` into `new_text`.

    Each separate change is its own hunk (difflib), so an edit in two places redraws two
    places, each in its own run's style, and the text between them keeps its glyphs. A
    same-length replacement across runs of different styles is split at the run boundary,
    so every letter keeps its run's style. With `restyle`, the unchanged text becomes
    hunks too -- one per style run, and split at gaps the PDF drew no space for, so each
    run is restyled from its own style and the gaps between words keep their width."""
    old = line.text[start:end]
    matcher = difflib.SequenceMatcher(None, old, new_text, autojunk=False)

    def style(i: int) -> _StyleKey:
        return styles[start + i]

    def one_style(i1: int, i2: int) -> bool:
        return len({style(i) for i in range(i1, i2) if style(i) is not None}) <= 1

    changes: list[tuple[int, int, str]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        replacement = new_text[j1:j2]
        if changes:
            p1, p2, previous = changes[-1]
            if i1 - p2 < _MIN_KEPT_MATCH and one_style(p1, i2):
                changes[-1] = (p1, i2, previous + old[p2:i1] + replacement)
                continue
        changes.append((i1, i2, replacement))

    hunks: list[tuple[int, int, str]] = []
    for i1, i2, replacement in changes:
        if i2 - i1 == len(replacement) and not one_style(i1, i2):
            cut = i1
            for i in range(i1 + 1, i2 + 1):
                if i == i2 or (style(i) is not None and style(i) != style(i - 1)):
                    hunks.append((cut, i, replacement[cut - i1 : i - i1]))
                    cut = i
        else:
            hunks.append((i1, i2, replacement))

    if restyle:
        covered = {i for i1, i2, _r in hunks for i in range(i1, i2)}
        inserted_at = {i1 for i1, i2, _r in hunks if i1 == i2}
        run: list[int] = []
        for i in range(len(old) + 1):
            breaks = (
                i == len(old)
                or i in covered
                or i in inserted_at
                or style(i) is None
                or bool(run and style(i) != style(run[-1]))
            )
            if breaks and run:
                hunks.append((run[0], run[-1] + 1, old[run[0] : run[-1] + 1]))
                run = []
            if i < len(old) and i not in covered and style(i) is not None:
                run.append(i)
        # An insertion between two restyled runs, or at an end, is its own hunk already.
    return sorted((start + i1, start + i2, text) for i1, i2, text in hunks)


@register_op
class EditTextUnitOp(_StyleChange):
    """EDT-17: new wording and/or a new style for one text unit -- the block, line or word
    the user selected (SPEC.md 8.2 item 6) -- as one Op, so one history entry and one undo.

    `index` is a span index for a block and the unit's index among the page's lines or
    words otherwise (GET .../text_units). `expect_text` is the unit's text as the UI last
    saw it: if the unit at `index` no longer has that text, the Op is refused rather than
    editing whatever is there now. Any field left unset keeps the unit's current value.

    A **word** or **line** is edited in place: each separate change is redrawn in the
    style of its own run (an insertion in the style of the character it follows), and
    everything else keeps its glyphs and styles (_unit_hunks). The rest of that line shifts
    only when a width changed, following the line's alignment; no other line is touched. A
    color-only restyle redraws each glyph where it is. A font, weight, slant or size change
    restyles every run from its own style: `size` is the size the unit's largest run takes,
    and every other run (a superscript, say) scales in proportion; a weight or slant change
    is made within each run's own family. `expect_text` is required for a word or line.
    Emptying a word or line is refused: delete it (delete_objects).

    A **block** is re-wrapped across its own lines (reflow_block), with `align`, `grow` and
    `allow_overflow` as in reflow_text; these three are for blocks only."""

    op: Literal["edit_text_unit"] = "edit_text_unit"
    page_index: int
    unit: Literal["block", "line", "word"]
    index: int
    expect_text: str | None = None
    new_text: str | None = None
    align: Literal["left", "justify"] = "left"
    grow: bool = False
    allow_overflow: bool = False

    @model_validator(mode="after")
    def _guarded(self) -> EditTextUnitOp:
        if self.unit != "block" and self.expect_text is None:
            raise ValueError(f"expect_text is required for a {self.unit}: the unit's text as last seen")
        return self

    def _check_expected(self, text: str) -> None:
        if self.expect_text is not None and self.expect_text != text:
            raise OpValidationError(
                f"edit_text_unit: the {self.unit} at index {self.index} is now {text!r}, not "
                f"{self.expect_text!r}; check the page again"
            )

    def _checked_text(self, current: str) -> str:
        """The text to draw: `new_text`, or the unit's own for a restyle."""
        text = current if self.new_text is None else self.new_text
        if not text.strip():
            raise OpValidationError(
                f"edit_text_unit: the new text is empty; to remove the {self.unit}, delete it (delete_objects)"
            )
        return text

    def apply(self, document: Document) -> EditResult | list[EditResult]:
        if self.size is not None and self.size <= 0:
            raise OpValidationError("edit_text_unit: size must be positive")
        if self.unit == "block":
            return self._edit_block(document)
        if self.align != "left" or self.grow or self.allow_overflow:
            raise OpValidationError('edit_text_unit: align, grow and allow_overflow are only for unit "block"')

        spans = extract_page_spans(document.raw, self.page_index)
        lines = group_lines(spans)
        if self.unit == "line":
            if not 0 <= self.index < len(lines):
                raise OpValidationError(
                    f"page {self.page_index} has {len(lines)} line(s); index {self.index} is out of range"
                )
            line = lines[self.index]
            start, end = 0, len(line.text)
        else:
            words = split_words(lines)
            if not 0 <= self.index < len(words):
                raise OpValidationError(
                    f"page {self.page_index} has {len(words)} word(s); index {self.index} is out of range"
                )
            word = words[self.index]
            line = lines[word.line_index]
            start, end = word.line_start, word.line_end
        current = line.text[start:end]
        self._check_expected(current)
        unit_spans = [spans[entry[0]] for entry in line.glyph_map[start:end] if entry is not None]
        if (without_no_ops := self._without_no_ops(unit_spans, current)) is not None:
            return without_no_ops.apply(document)
        new_text = self._checked_text(current)
        if "\n" in new_text or "\r" in new_text:
            raise OpValidationError(f"edit_text_unit: a {self.unit} is one line; the new text has a line break")
        restyles = self._restyles()
        if new_text == current and not restyles:
            raise OpValidationError(
                "edit_text_unit: change the text or at least one of size, color, font, bold or italic"
            )

        font_index = _font_index()
        only_color = all(value is None for value in (self.size, self.font, self.bold, self.italic))
        if new_text == current and only_color:
            # Same glyphs, same places, new color: nothing on the line can move.
            assert self.color is not None  # nosec B101 -- type narrowing: only_color with no text change implies a color
            result = recolor_line_range(
                document, self.page_index, line, start, end, self.color, font_index=font_index, verify=self.verify
            )
        else:
            glyphs = [None if entry is None else spans[entry[0]] for entry in line.glyph_map]
            styles = [None if span is None else _style_of(span) for span in glyphs]
            hunks = _unit_hunks(line, styles, start, end, new_text, restyle=restyles)
            scale = None
            if self.size is not None:
                # The unit's largest run takes the new size; the others keep their proportion.
                largest = max(span.style.size for span in glyphs[start:end] if span is not None)
                scale = self.size / largest
            changes_font = self.font is not None or self.bold is not None or self.italic is not None
            restyle = LineRestyle(
                size_scale=scale,
                color=self.color,
                font_for=(lambda span, text: self._chosen_font(span, text, font_index)) if changes_font else None,
            )
            result = rewrite_line_hunks(
                document, self.page_index, line, hunks, restyle=restyle, font_index=font_index, verify=self.verify
            )
        _check_tier(result, self.require_tier, where=type(self).__name__)
        return result

    def _without_no_ops(self, unit_spans: list[SpanTrace], current: str) -> EditTextUnitOp | None:
        """This Op without a bold or italic request the unit already meets (bold on text that
        is all bold): None when there is none. Such a request alone is refused -- it would
        redraw the text unchanged and report that nothing visible happened."""
        font_index = _font_index()
        styles = [is_bold_italic(span.style.font, font_index) for span in unit_spans]
        dropped: dict[str, None] = {}
        met: list[str] = []
        for field, position, word in (("bold", 0, "bold"), ("italic", 1, "italic")):
            wanted = getattr(self, field)
            if wanted is not None and styles and all(style[position] == wanted for style in styles):
                dropped[field] = None
                met.append(word if wanted else f"not {word}")
        if not dropped:
            return None
        remaining = self.model_copy(update=dropped)
        text_changes = self.new_text is not None and self.new_text != current
        if not remaining._restyles() and not text_changes and (self.unit != "block" or self.align == "left"):
            raise OpValidationError(f"edit_text_unit: the {self.unit} is already {' and '.join(met)}")
        return remaining

    def _recolor_block(self, document: Document, block: TextBlock) -> list[EditResult]:
        """C4: a color-only change to a block recolors each of its lines in place, so every
        run keeps its own font and size -- re-wrapping would redraw it in one style."""
        assert self.color is not None  # nosec B101 -- type narrowing: only called for a color-only change
        members = {span.style.span_index for span in block.lines}
        spans = extract_page_spans(document.raw, self.page_index)
        keys = [
            (line.origin, line.text)
            for line in group_lines(spans)
            if {segment.span_index for segment in line.segments} <= members
        ]
        results: list[EditResult] = []
        for origin, text in keys:
            lines = group_lines(extract_page_spans(document.raw, self.page_index))
            line = next(
                (c for c in lines if c.text == text and math.dist(c.origin, origin) < _SAME_ORIGIN_TOLERANCE), None
            )
            if line is None:
                raise OpValidationError(f"edit_text_unit: the line {text!r} changed while the block was recolored")
            results.append(
                recolor_line_range(
                    document,
                    self.page_index,
                    line,
                    0,
                    len(line.text),
                    self.color,
                    font_index=_font_index(),
                    verify=self.verify,
                )
            )
        for result in results:
            _check_tier(result, self.require_tier, where=type(self).__name__)
        return results

    def _edit_block(self, document: Document) -> list[EditResult]:
        block = _block_at(document, self.page_index, self.index)
        spans = extract_page_spans(document.raw, self.page_index)
        # The same text GET .../text_units reports for the block this span is in.
        unit = next((u for u in text_units(spans, "block") if self.index in u.span_indices), None)
        current = unit.text if unit is not None else block.text
        self._check_expected(current)
        if (without_no_ops := self._without_no_ops(list(block.lines), current)) is not None:
            return without_no_ops._edit_block(document)
        new_text = self._checked_text(block.text)
        only_color = self.color is not None and all(v is None for v in (self.size, self.font, self.bold, self.italic))
        if only_color and new_text in (current, block.text) and self.align == "left" and not self.grow:
            return self._recolor_block(document, block)
        if new_text in (current, block.text) and not self._restyles() and self.align == "left" and not self.grow:
            raise OpValidationError(
                "edit_text_unit: change the text, the alignment or at least one of size, color, font, bold or italic"
            )
        font_index = _font_index()
        results = reflow_block(
            document,
            self.page_index,
            block,
            new_text,
            font_index=font_index,
            verify=self.verify,
            align=self.align,
            grow=self.grow,
            override_font=self._chosen_font(block.dominant, new_text, font_index),
            override_size=self.size,
            override_color=self.color,
        )
        _check_reflow(results, self.require_tier, self.allow_overflow, where=type(self).__name__)
        return results
