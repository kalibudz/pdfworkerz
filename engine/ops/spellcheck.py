"""EDT-11: typed spell-check Ops (see engine.spellcheck)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from engine.document import Document
from engine.edit import EditResult, replace_span_text
from engine.errors import OpValidationError
from engine.ops.base import Op, register_op
from engine.ops.text import _check_tier, _font_index, _span_at
from engine.spellcheck import Misspelling, check_page


@register_op
class SpellCheckOp(Op):
    """Every misspelled word on one page, with suggestions. Read-only, so --
    like PageSpansOp -- never journaled; server/app.py serves it from a GET route."""

    op: Literal["spell_check"] = "spell_check"
    page_index: int = 0
    language: str = "en_US"
    ignore: list[str] = Field(default_factory=list)

    def apply(self, document: Document) -> list[Misspelling]:
        return check_page(document, self.page_index, language=self.language, ignore=frozenset(self.ignore))


@register_op
class CorrectWordOp(Op):
    """Replace one word -- characters `start`..`end` of the span at
    `span_index` -- with `replacement`, keeping the rest of the span and its
    style (the ReplaceSpanTextOp path). `word` must still be what's there:
    a stale correction (the page changed since it was checked) is refused
    rather than overwriting the wrong characters."""

    op: Literal["correct_word"] = "correct_word"
    page_index: int
    span_index: int
    start: int
    end: int
    word: str
    replacement: str
    require_tier: Literal["exact", "approximate", "fallback"] = "approximate"
    verify: bool = True

    def apply(self, document: Document) -> EditResult:
        span = _span_at(document, self.page_index, self.span_index)
        text = span.style.text
        if not (0 <= self.start < self.end <= len(text)) or text[self.start : self.end] != self.word:
            raise OpValidationError(
                f"correct_word: {self.word!r} is no longer at characters {self.start}-{self.end} of span "
                f"{self.span_index}; check the page again"
            )
        new_text = text[: self.start] + self.replacement + text[self.end :]
        result = replace_span_text(
            document, self.page_index, span, new_text, font_index=_font_index(), verify=self.verify
        )
        _check_tier(result, self.require_tier, where=type(self).__name__)
        return result
