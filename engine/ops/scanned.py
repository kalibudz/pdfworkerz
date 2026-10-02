"""FNT-16 / EDT-12: the typed Ops for scanned pages -- finding their lines, measuring a line's
style, and replacing a line's text. Reading and measuring never change the document; the edit does,
so it is journaled (one undo step)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from engine.document import Document
from engine.ops.base import Op, register_op
from engine.scanned_edit import (
    DEFAULT_DPI,
    ScannedEdit,
    ScannedLine,
    StyleChoice,
    edit_scanned_text,
    estimate_line_style,
    find_scanned_lines,
)
from engine.scanned_style import FontClass, ScannedStyle

Rect = tuple[float, float, float, float]


@register_op
class ScannedLinesOp(Op):
    """The lines of text OCR finds on a scanned page (EDT-12): what a person picks to edit."""

    op: Literal["scanned_lines"] = "scanned_lines"
    page_index: int = 0
    language: str = "eng"
    dpi: int = Field(default=DEFAULT_DPI, ge=72, le=600)

    def apply(self, document: Document) -> list[ScannedLine]:
        return find_scanned_lines(document, self.page_index, language=self.language, dpi=self.dpi)


@register_op
class EstimateScannedStyleOp(Op):
    """How the text inside ``rect`` on a scanned page was typeset (FNT-16)."""

    op: Literal["estimate_scanned_style"] = "estimate_scanned_style"
    page_index: int = 0
    rect: Rect
    text: str | None = None
    """What the text says; read by OCR when omitted."""

    def apply(self, document: Document) -> ScannedStyle | None:
        return estimate_line_style(document, self.page_index, self.rect, self.text)


@register_op
class EditScannedTextOp(Op):
    """Replace the text inside ``rect`` on a scanned page with ``new_text``, drawn in the style
    estimated from the old text. Any style field given overrides the estimate."""

    op: Literal["edit_scanned_text"] = "edit_scanned_text"
    page_index: int = 0
    rect: Rect
    new_text: str
    old_text: str | None = None
    font_class: FontClass | None = None
    bold: bool | None = None
    italic: bool | None = None
    size_pt: float | None = Field(default=None, gt=0, le=400)
    color: tuple[int, int, int] | None = None

    def apply(self, document: Document) -> ScannedEdit:
        choice = StyleChoice(self.font_class, self.bold, self.italic, self.size_pt, self.color)
        return edit_scanned_text(
            document, self.page_index, self.rect, self.new_text, old_text=self.old_text, style=choice
        )
