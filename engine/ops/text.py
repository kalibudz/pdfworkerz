"""EDT-01, EDT-02, EDT-03, EDT-04, EDT-06, FNT-11: typed text-editing Ops.

Each Op finds its target span(s) with :func:`engine.fonts.style.extract_page_spans`
and draws through :mod:`engine.edit`. A match is only supported when it lies
entirely within one span (SPEC.md's Tier model operates per span); a search
string split across two spans is not found. `require_tier` is how these Ops
implement "never guess silently" ahead of a UI: a font resolution weaker
than the tier requested raises :class:`~engine.errors.OpValidationError`
instead of drawing with it.
"""

from __future__ import annotations

import re
from typing import Literal

from engine.document import Document
from engine.edit import EditResult, insert_text_near, reflow_block, replace_span_text
from engine.errors import OpValidationError
from engine.fonts.blocks import detect_blocks, find_block_containing
from engine.fonts.match import FontCandidate, build_font_index
from engine.fonts.style import SpanTrace, extract_page_spans
from engine.ops.base import Op, register_op

_TIER_ORDER = {"exact": 0, "approximate": 1, "fallback": 2}

_font_index_cache: list[FontCandidate] | None = None


def _font_index() -> list[FontCandidate]:
    """A process-wide cache: scanning system fonts is expensive and the result
    doesn't change within a session. A longer-lived cache belongs at the server
    layer once one exists; this is enough for the CLI and for tests."""
    global _font_index_cache
    if _font_index_cache is None:
        _font_index_cache = build_font_index()
    return _font_index_cache


def _check_tier(result: EditResult, require_tier: str, *, where: str) -> None:
    if _TIER_ORDER[result.tier] > _TIER_ORDER[require_tier]:
        raise OpValidationError(
            f"{where}: font resolution fell back to tier {result.tier!r}, weaker than the "
            f"required {require_tier!r} (confidence {result.confidence:.2f}: {result.note})"
        )


def _compile_pattern(match: str, mode: str, case_sensitive: bool) -> re.Pattern[str]:
    flags = 0 if case_sensitive else re.IGNORECASE
    pattern = match if mode == "regex" else re.escape(match)
    return re.compile(pattern, flags)


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

    def _replacement_for(self, matched_text: str) -> str:
        raise NotImplementedError

    def apply(self, document: Document) -> list[EditResult]:
        font_index = _font_index()
        pattern = _compile_pattern(self.match, self.mode, self.case_sensitive)
        results: list[EditResult] = []

        for page_index in _pages_to_search(document, self.page_index):
            while True:
                found = _find_first_match(document, page_index, pattern)
                if found is None:
                    break
                span, match_obj = found
                text = span.style.text
                replacement = self._replacement_for(match_obj.group())
                new_text = text[: match_obj.start()] + replacement + text[match_obj.end() :]
                if new_text == text:
                    break  # a zero-width regex match on an already-handled span: stop, don't loop forever
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


@register_op
class RestyleTextOp(Op):
    """EDT-06: change the size and/or color of text that already matches, leaving its
    wording unchanged."""

    op: Literal["restyle_text"] = "restyle_text"
    match: str
    mode: Literal["literal", "regex"] = "literal"
    case_sensitive: bool = True
    page_index: int | None = None
    size: float | None = None
    color: tuple[float, float, float] | None = None
    require_tier: Literal["exact", "approximate", "fallback"] = "approximate"
    verify: bool = True

    def apply(self, document: Document) -> list[EditResult]:
        if self.size is None and self.color is None:
            raise OpValidationError("restyle_text: at least one of size or color must be set")
        font_index = _font_index()
        pattern = _compile_pattern(self.match, self.mode, self.case_sensitive)
        results: list[EditResult] = []

        for page_index in _pages_to_search(document, self.page_index):
            spans = extract_page_spans(document.raw, page_index)
            targets = [span for span in spans if pattern.search(span.style.text)]
            for span in targets:
                result = replace_span_text(
                    document,
                    page_index,
                    span,
                    span.style.text,
                    font_index=font_index,
                    override_size=self.size,
                    override_color=self.color,
                    verify=self.verify,
                )
                _check_tier(result, self.require_tier, where=self.op)
                results.append(result)
        return results


@register_op
class InsertTextOp(Op):
    """EDT-03: add new text near an existing span, matching its style (or an
    explicit override) -- nothing existing is touched."""

    op: Literal["insert_text"] = "insert_text"
    page_index: int
    text: str
    position: tuple[float, float]
    reference_match: str
    """A literal substring identifying the span whose style to copy."""
    reference_case_sensitive: bool = True
    require_tier: Literal["exact", "approximate", "fallback"] = "approximate"
    verify: bool = True

    def apply(self, document: Document) -> EditResult:
        font_index = _font_index()
        pattern = _compile_pattern(self.reference_match, "literal", self.reference_case_sensitive)
        found = _find_first_match(document, self.page_index, pattern)
        if found is None:
            raise OpValidationError(f"insert_text: no span matches reference text {self.reference_match!r}")
        reference_span, _match_obj = found

        result = insert_text_near(
            document,
            self.page_index,
            reference_span,
            self.text,
            self.position,
            font_index=font_index,
            verify=self.verify,
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
        if not self.allow_overflow and "overflow:" in results[-1].note:
            raise OpValidationError(f"reflow_text: {results[-1].note}")
        for result in results:
            _check_tier(result, self.require_tier, where=type(self).__name__)
        return results
