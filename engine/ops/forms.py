"""P6: typed Ops for AcroForm fields (FRM-01, FRM-02, FRM-05, FRM-06); see engine.forms."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from engine import forms
from engine.forms import FieldInfo, FillResult, FlattenResult
from engine.ops.base import Op, register_op

if TYPE_CHECKING:
    from engine.document import Document


@register_op
class PageFieldsOp(Op):
    """FRM-01: every AcroForm field on one page (a radio group counts as one
    logical field). Read-only, so -- like PageAnnotationsOp -- never journaled;
    server/app.py serves it from a GET route."""

    op: Literal["page_fields"] = "page_fields"
    page_index: int = 0

    def apply(self, document: Document) -> list[FieldInfo]:
        return forms.list_fields(document, self.page_index)


@register_op
class FillFieldsOp(Op):
    """FRM-02: set several named fields' values on one page in a single undo step."""

    op: Literal["fill_fields"] = "fill_fields"
    page_index: int
    values: dict[str, Any]

    def apply(self, document: Document) -> FillResult:
        return forms.fill_fields(document, self.page_index, self.values)


@register_op
class SetTabOrderOp(Op):
    """FRM-05: reorder a page's fields for tab navigation."""

    op: Literal["set_tab_order"] = "set_tab_order"
    page_index: int
    field_names: list[str]

    def apply(self, document: Document) -> None:
        forms.set_tab_order(document, self.page_index, self.field_names)


@register_op
class FlattenFormOp(Op):
    """FRM-06: draw every widget's appearance into page content and drop the
    AcroForm. ``page_index=None`` flattens the whole document."""

    op: Literal["flatten_form"] = "flatten_form"
    page_index: int | None = None

    def apply(self, document: Document) -> FlattenResult:
        return forms.flatten_form(document, self.page_index)
