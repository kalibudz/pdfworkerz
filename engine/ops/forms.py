"""P6: typed Ops for AcroForm fields (FRM-01, FRM-02, FRM-03, FRM-05, FRM-06, FRM-07, FRM-08);
see engine.forms and engine.form_data."""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Any, Literal

from engine import form_data, forms
from engine.form_detect import FieldProposal, detect_form_fields
from engine.forms import FieldInfo, FillResult, FlattenResult, XFAReport
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


Rect = tuple[float, float, float, float]


@register_op
class CreateFieldOp(Op):
    """FRM-03: add a new field to one page; see engine.forms.create_field for what each
    of these means per `field_type` (a radio group's own `rect` is ignored -- give
    `options`+`rects` instead)."""

    op: Literal["create_field"] = "create_field"
    page_index: int
    field_type: Literal["text", "checkbox", "radio", "dropdown", "listbox", "signature"]
    name: str
    rect: Rect | None = None
    options: list[str] | None = None
    rects: list[Rect] | None = None
    value: str | bool | None = None
    font: str = "Helv"
    size: float = 0.0
    multiline: bool = False

    def apply(self, document: Document) -> FieldInfo:
        return forms.create_field(
            document,
            self.page_index,
            field_type=self.field_type,
            name=self.name,
            rect=self.rect,
            options=self.options,
            rects=self.rects,
            value=self.value,
            font=self.font,
            size=self.size,
            multiline=self.multiline,
        )


@register_op
class EditFieldOp(Op):
    """FRM-03: change an existing field's type-specific properties; only the fields
    actually given are changed (see engine.forms.edit_field for which properties apply
    to which field type -- an inapplicable one is refused)."""

    op: Literal["edit_field"] = "edit_field"
    page_index: int
    name: str
    font: str | None = None
    size: float | None = None
    multiline: bool | None = None
    options: list[str] | None = None
    rect: Rect | None = None
    rects: list[Rect] | None = None

    def apply(self, document: Document) -> FieldInfo:
        changes: dict[str, Any] = {}
        if self.font is not None:
            changes["font"] = self.font
        if self.size is not None:
            changes["size"] = self.size
        if self.multiline is not None:
            changes["multiline"] = self.multiline
        if self.options is not None:
            changes["options"] = self.options
        if self.rect is not None:
            changes["rect"] = self.rect
        if self.rects is not None:
            changes["rects"] = self.rects
        return forms.edit_field(document, self.page_index, self.name, **changes)


@register_op
class DeleteFieldOp(Op):
    """FRM-03: remove a field from both the page's annotations and the AcroForm's field
    tree (a radio group's every button together)."""

    op: Literal["delete_field"] = "delete_field"
    page_index: int
    name: str

    def apply(self, document: Document) -> None:
        forms.delete_field(document, self.page_index, self.name)


@register_op
class ExportFormDataOp(Op):
    """FRM-07: every field on one page as FDF/XFDF/JSON/CSV bytes. Read-only, like
    PageFieldsOp; server/app.py gives it its own GET route (like render_page's) since
    its result is raw bytes of a caller-chosen format, not journaled JSON."""

    op: Literal["export_form_data"] = "export_form_data"
    page_index: int
    format: Literal["fdf", "xfdf", "json", "csv"]

    def apply(self, document: Document) -> bytes:
        return form_data.export_form_data(document, self.page_index, self.format)


@register_op
class ImportFormDataOp(Op):
    """FRM-07: apply previously-exported (or hand-written) field data to one page, in one
    undo step. `data_base64` is the file's bytes, base64-encoded for the JSON Op body --
    the same convention RenderPageOp's PNG result and SIG-01's image stamp input use."""

    op: Literal["import_form_data"] = "import_form_data"
    page_index: int
    format: Literal["fdf", "xfdf", "json", "csv"]
    data_base64: str

    def apply(self, document: Document) -> FillResult:
        data = base64.b64decode(self.data_base64)
        return form_data.import_form_data(document, self.page_index, self.format, data)


@register_op
class DetectFormFieldsOp(Op):
    """FRM-04: propose likely field locations on a flat (non-interactive) page
    from visual cues alone (a line/underscore -> text field, a small box ->
    checkbox). Read-only, like PageFieldsOp -- never journaled; server/app.py
    serves it from its own GET route. Nothing is created until the caller
    reviews the proposals and issues a separate CreateDetectedFieldsOp (or
    individual CreateFieldOp calls)."""

    op: Literal["detect_form_fields"] = "detect_form_fields"
    page_index: int
    dpi: int = 150

    def apply(self, document: Document) -> list[FieldProposal]:
        return detect_form_fields(document, self.page_index, dpi=self.dpi)


@register_op
class CreateDetectedFieldsOp(Op):
    """FRM-04's accept step: turn a batch of (presumably reviewed/filtered)
    FieldProposals into real AcroForm fields via FRM-03's own create_field, in
    one journal entry -- so accepting several proposals at once undoes as a
    single step, and a failure partway (e.g. a duplicate name) leaves nothing
    behind (UndoRedoJournal.record rolls the whole Op back on any exception,
    same as every other multi-step Op). `proposals` is normally exactly what
    a prior DetectFormFieldsOp returned, possibly with some entries dropped by
    the caller after review -- never applied automatically on its own."""

    op: Literal["create_detected_fields"] = "create_detected_fields"
    page_index: int
    proposals: list[FieldProposal]

    def apply(self, document: Document) -> list[FieldInfo]:
        created: list[FieldInfo] = []
        for proposal in self.proposals:
            created.append(
                forms.create_field(
                    document,
                    self.page_index,
                    field_type=proposal.field_type,
                    name=proposal.suggested_name,
                    rect=proposal.rect,
                    value=False if proposal.field_type == "checkbox" else None,
                )
            )
        return created


@register_op
class DetectXFAOp(Op):
    """FRM-08: whether this document's AcroForm carries an /XFA entry. Read-only and not
    page-scoped (it's a whole-document property); server/app.py gives it its own GET
    route, like PageFieldsOp's."""

    op: Literal["detect_xfa"] = "detect_xfa"

    def apply(self, document: Document) -> XFAReport:
        return forms.detect_xfa(document)
