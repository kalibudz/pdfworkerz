"""EDT-16: the page's text units for Block / Line / Word selection mode (SPEC.md 8.2
item 6). Read-only, never journaled; served from a GET route like PageBlocksOp."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from engine.fonts.style import extract_page_spans
from engine.fonts.units import Granularity, TextUnit, text_units
from engine.ops.base import Op, register_op

if TYPE_CHECKING:
    from engine.document import Document


@register_op
class PageTextUnitsOp(Op):
    """Every block, line or word on one page (engine.fonts.units), in that
    granularity's order -- what one click selects in the matching selection mode."""

    op: Literal["page_text_units"] = "page_text_units"
    page_index: int = 0
    granularity: Granularity = "line"

    def apply(self, document: Document) -> list[TextUnit]:
        return text_units(extract_page_spans(document.raw, self.page_index), self.granularity)
