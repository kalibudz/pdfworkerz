"""P6 forms (FRM-01, FRM-02, FRM-05, FRM-06): detect/list AcroForm fields, fill
them, set tab order, and flatten forms to static page content.

``FieldInfo`` is the field model every later form feature builds on (FRM-03's
create/edit, FRM-04's auto-detect, FRM-07's import/export, FRM-08's XFA
detection): it is deliberately a plain, serializable snapshot of a field --
not a live handle -- so it's safe to hand across the Op/JSON boundary. Code
that needs to *act* on a field re-resolves it by name through the helpers
here (:func:`_widgets_by_field`), the same way engine.annotations re-resolves
annotations by (page_index, xref) rather than holding onto PyMuPDF objects.

PyMuPDF 1.28.2 specifics this module relies on (verified against the
installed version, not assumed from memory):

- ``page.widgets()`` yields one :class:`pymupdf.Widget` per *kid* annotation;
  a radio group (several widgets sharing one ``field_name``) is **not**
  automatically collapsed -- this module groups by ``field_name`` itself.
- A kid widget built properly (via a real ``/Parent``+``/Kids`` structure,
  the only way Acrobat and other real-world tools build radio groups) reports
  its own ``on_state()`` and a shared ``rb_parent`` (the parent field's xref).
  PyMuPDF's own ``Widget``-based *creation* API (``page.add_widget``), by
  contrast, does **not** build that ``/Parent``/``/Kids`` structure for
  radio buttons -- two widgets created with the same ``field_name`` come out
  as two independent top-level fields that happen to share a name, each
  defaulting to the same "Yes" on-state. Test fixtures that need a *real*
  radio group are therefore built directly with pikepdf (see
  ``tests/engine/test_forms.py``), not via ``page.add_widget``.
- ``widget.button_states()`` and ``widget.on_state()`` are *methods* on a
  widget read back from a saved/reopened document (bound methods), not the
  plain attributes of a freshly constructed, not-yet-added ``Widget()``.
- Setting ``widget.field_value`` and calling ``widget.update()`` regenerates
  the field's appearance stream (verified: a filled text field's new value
  is visible in ``page.get_text()`` immediately after, with no external PDF
  viewer involved). Assigning ``True``/``False`` to a checkbox's
  ``field_value`` maps onto whatever its real on-state is (even a custom
  one), so filling never needs to invent the on-state name itself.
- Setting a radio kid's ``field_value = kid.on_state()`` turns its siblings
  off automatically (``Widget._checker`` does this, confirmed by reading the
  installed source and by a round-trip test) -- the caller never needs to
  manually clear the other kids in the group.
- ``document.raw.bake(annots=False, widgets=True)`` flattens *every* widget
  in the whole document to static content and drops ``/AcroForm`` entirely
  (confirmed: reopening afterwards reports no ``/AcroForm`` key at all).
  There is no page-scoped equivalent in 1.28.2 (``bake()`` takes no page
  argument), so a page-scoped flatten here instead captures each widget's
  on-page pixels (``page.get_pixmap(clip=rect, annots=True)``) at a DPI well
  above the page's own resolution, deletes the widget
  (``page.delete_widget``), and pastes the capture back as a static image at
  the same rect (``page.insert_image``) -- pixel-faithful by construction,
  since it is literally the same pixels, and measured within ~0.2% of
  pixels differing from a full-document ``bake()`` of the same content (the
  residual is antialiasing at the raster/vector boundary, not missing
  content). ``page.delete_widget`` already removes the field from
  ``/AcroForm``'s ``/Fields`` array as a side effect (confirmed); this module
  only needs to clear the now-empty ``/AcroForm`` key itself once no widget
  remains anywhere in the document.
- Tab order: the PDF spec drives tab order from a page's ``/Annots`` array
  order whenever that page's ``/Tabs`` is ``/S`` (structure order). PyMuPDF
  exposes no higher-level reordering call, but its low-level
  ``xref_get_key``/``xref_set_key`` can read and rewrite a page's raw
  ``/Annots`` array (confirmed round-trip: writing a reordered array changes
  the order ``page.widgets()`` yields on reopen). This module reorders only
  the *widget* entries within that array, leaving every non-widget
  annotation's position untouched, and sets ``/Tabs`` to ``/S``.
"""

from __future__ import annotations

import io
import re
from typing import Literal

import pikepdf
import pymupdf
from pydantic import BaseModel, ConfigDict, Field

from engine.document import Document
from engine.errors import OpValidationError
from engine.pdfbytes import plain_bytes

Rect = tuple[float, float, float, float]

FieldType = Literal["text", "checkbox", "radio", "dropdown", "listbox", "signature", "unknown"]

_TYPE_BY_STRING: dict[str, FieldType] = {
    "Text": "text",
    "CheckBox": "checkbox",
    "RadioButton": "radio",
    "ComboBox": "dropdown",
    "ListBox": "listbox",
    "Signature": "signature",
}

_ANNOT_REF_RE = re.compile(rb"\d+\s+\d+\s+R")


class FieldInfo(BaseModel):
    """One logical AcroForm field, as FRM-01's listing (and every later form
    feature) sees it. A radio group -- several widgets sharing one
    ``field_name`` -- is reported as a single ``FieldInfo`` with
    ``field_type="radio"`` and one option per radio button.

    Deliberately minimal and serializable (not a live PyMuPDF handle): later
    waves add to this model for FRM-03 (create/edit) rather than replacing
    it, so keep new fields optional with safe defaults.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    field_type: FieldType
    rect: Rect
    """The field's bounding box; for a radio group, the union of every option's rect."""
    value: str | bool | None
    """The current value: ``bool`` for a checkbox, the selected option string for
    radio/dropdown/listbox (``None`` if nothing is selected), the text for a text
    field, ``None`` for a signature (unset) or an unknown field type."""
    options: list[str] | None = None
    """Choice values for dropdown/listbox, or export values for a radio group's
    buttons; ``None`` for every other field type."""
    page_index: int = 0


def _field_type(widget: pymupdf.Widget) -> FieldType:
    return _TYPE_BY_STRING.get(widget.field_type_string or "", "unknown")


def _union_rect(rects: list[pymupdf.Rect | None]) -> Rect:
    result = pymupdf.Rect(rects[0])
    for rect in rects[1:]:
        result |= pymupdf.Rect(rect)
    return (result.x0, result.y0, result.x1, result.y1)


def _as_rect(rect: pymupdf.Rect | None) -> Rect:
    box = pymupdf.Rect(rect)
    return (box.x0, box.y0, box.x1, box.y1)


def _validate_rect(page: pymupdf.Page, rect: Rect, *, label: str = "field") -> pymupdf.Rect:
    """A field's rect must have positive area, normalized corners (x0<x1, y0<y1), and lie
    within the page -- otherwise `page.add_widget` raises a bare ``ValueError("bad rect")``
    for a zero-area/inverted rect (which server/app.py's error map doesn't know how to turn
    into a clean 400, since it isn't a PdfWorkerzError) and raises nothing at all for an
    out-of-bounds one (silently creating an unreachable/invisible field). Both FRM-03's
    simple-field path and its raw-xref radio-group path (`_build_radio_group`) call this
    before writing anything, so every field creation/edit gets the same check."""
    box = pymupdf.Rect(rect)
    if box.is_empty or not box.is_valid:
        raise OpValidationError(
            f"{label} rect {tuple(rect)!r} is empty or inverted; need x0 < x1 and y0 < y1 with positive area"
        )
    if not page.rect.contains(box):
        raise OpValidationError(f"{label} rect {tuple(rect)!r} is outside the page ({_as_rect(page.rect)!r})")
    return box


def _group_widgets(page: pymupdf.Page) -> dict[str, list[pymupdf.Widget]]:
    """Every widget on `page`, grouped by field name, in first-seen order."""
    groups: dict[str, list[pymupdf.Widget]] = {}
    for widget in page.widgets():
        groups.setdefault(widget.field_name or "", []).append(widget)
    return groups


def _field_info(name: str, widgets: list[pymupdf.Widget], page_index: int) -> FieldInfo:
    if len(widgets) > 1:
        # Several widgets sharing one field name: a radio group (the only case PDF
        # allows this). `on_state()` is each kid's own export value when "on".
        radio_options = [widget.on_state() or "" for widget in widgets]
        selected = next(
            (
                state
                for widget, state in zip(widgets, radio_options, strict=True)
                if widget.field_value not in (None, "Off")
            ),
            None,
        )
        return FieldInfo(
            name=name,
            field_type="radio",
            rect=_union_rect([w.rect for w in widgets]),
            value=selected,
            options=radio_options,
            page_index=page_index,
        )

    widget = widgets[0]
    field_type = _field_type(widget)
    value: str | bool | None
    choice_options: list[str] | None = None
    if field_type == "checkbox":
        value = widget.field_value not in (None, "Off", False)
    elif field_type in ("dropdown", "listbox"):
        choice_options = list(widget.choice_values or [])
        value = widget.field_value or None
    elif field_type == "signature":
        value = None
    else:  # text, unknown
        value = widget.field_value or ""
    return FieldInfo(
        name=name,
        field_type=field_type,
        rect=_as_rect(widget.rect),
        value=value,
        options=choice_options,
        page_index=page_index,
    )


def list_fields(document: Document, page_index: int) -> list[FieldInfo]:
    """FRM-01: every AcroForm field on `page_index`. A radio group is one logical
    field. A page (or document) with no AcroForm fields returns ``[]``, never raises."""
    page = document.raw[page_index]
    groups = _group_widgets(page)
    return [_field_info(name, widgets, page_index) for name, widgets in groups.items() if name]


def _widgets_by_field(page: pymupdf.Page) -> dict[str, list[pymupdf.Widget]]:
    return {name: widgets for name, widgets in _group_widgets(page).items() if name}


# -- FRM-03: create, edit, delete fields --------------------------------------------------
#
# Simple (single-widget) field types -- text, checkbox, dropdown, listbox, signature -- are
# created with page.add_widget(), confirmed (see this module's own docstring, and a REPL
# check before writing this code) to build a correct, immediately-visible widget: the new
# field shows up in page.widgets() right away, no save/reopen needed, so create_field can
# hand back a FieldInfo straight away and a later list_fields()/fill_fields() call in the
# same session sees it too.
#
# A radio *group* is the one shape page.add_widget() can't build (no real /Parent+/Kids --
# see the module docstring). This module instead writes the raw PDF structure directly with
# pymupdf's own low-level xref calls (get_new_xref/update_object/update_stream/xref_set_key
# -- the same primitives set_tab_order already uses for /Annots, so no new library is
# introduced for this). Confirmed by a REPL check: unlike add_widget, a freshly-built raw
# structure is *not* picked up by an already-open Page's cached widget list on its own --
# pymupdf.Document.reload_page(page) is required afterwards, and must be called with no
# other live reference to that Page object around (reload_page's own internal refcount
# assertion fails if the caller still holds one), so every helper below re-fetches the page
# via `document.raw[page_index]` after a reload rather than keeping an old Page variable.

_CREATABLE_TYPES: frozenset[str] = frozenset({"text", "checkbox", "radio", "dropdown", "listbox", "signature"})

# Missing from pymupdf's stub (like PDF_ENCRYPT_KEEP in engine.pdfbytes), confirmed present
# on the installed 1.28.2 at runtime.
_FIELD_TYPE_CONST: dict[str, int] = {
    "text": pymupdf.PDF_WIDGET_TYPE_TEXT,  # type: ignore[attr-defined]
    "checkbox": pymupdf.PDF_WIDGET_TYPE_CHECKBOX,  # type: ignore[attr-defined]
    "dropdown": pymupdf.PDF_WIDGET_TYPE_COMBOBOX,  # type: ignore[attr-defined]
    "listbox": pymupdf.PDF_WIDGET_TYPE_LISTBOX,  # type: ignore[attr-defined]
    "signature": pymupdf.PDF_WIDGET_TYPE_SIGNATURE,  # type: ignore[attr-defined]
}
_MULTILINE_FLAG: int = pymupdf.PDF_TX_FIELD_IS_MULTILINE  # type: ignore[attr-defined]

# Type-specific properties FRM-03's edit_field accepts per field type, beyond the generic
# "rect" (reposition/resize) every simple type also accepts. Radio groups are edited through
# a separate path (_edit_radio_group) since "options" there means something structurally
# different (new /Kids, not a /Opt array).
_EDITABLE_PROPS: dict[FieldType, frozenset[str]] = {
    "text": frozenset({"font", "size", "multiline", "rect"}),
    "checkbox": frozenset({"rect"}),
    "dropdown": frozenset({"options", "rect"}),
    "listbox": frozenset({"options", "rect"}),
    "signature": frozenset({"rect"}),
}


def _pdf_name_token(value: str) -> str:
    """A PDF `/Name` token for `value`. Field option names in this engine are plain
    identifiers (radio/dropdown/listbox options, as validated by fill_fields already);
    the only characters routinely seen that aren't legal bare in a PDF name are escaped
    as `#xx`, the PDF 1.2+ name-escaping convention, so an option like "50% Off" still
    round-trips instead of corrupting the object syntax."""
    escaped = "".join(f"#{ord(ch):02x}" if ch in " \t\r\n()<>[]{}/%#" or ord(ch) < 33 else ch for ch in value)
    return f"/{escaped}"


def _pdf_string_literal(value: str) -> str:
    """A PDF `(...)` string literal for `value`, with the three characters that would
    otherwise corrupt the literal (backslash and the two parens) backslash-escaped."""
    escaped = value.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
    return f"({escaped})"


def _require_known_field(groups: dict[str, list[pymupdf.Widget]], name: str, page_index: int) -> list[pymupdf.Widget]:
    widgets = groups.get(name)
    if widgets is None:
        raise OpValidationError(f"unknown field name {name!r} on page {page_index}")
    return widgets


def _make_appearance_xobject(document: Document, size: tuple[float, float], content: str) -> int:
    doc = document.raw
    width, height = size
    xref = doc.get_new_xref()
    doc.update_object(
        xref,
        f"<< /Type /XObject /Subtype /Form /BBox [0 0 {width} {height}] "
        f"/Matrix [1 0 0 1 0 0] /Resources << /ProcSet [/PDF] >> >>",
    )
    doc.update_stream(xref, content.encode("latin-1"))
    return xref


def _append_page_annots(document: Document, page: pymupdf.Page, xrefs: list[int]) -> None:
    tokens = _page_annots_tokens(document, page.xref)
    tokens += [f"{xref} 0 R".encode("ascii") for xref in xrefs]
    document.raw.xref_set_key(page.xref, "Annots", (b"[" + b" ".join(tokens) + b"]").decode("ascii"))


def _remove_page_annots(document: Document, page: pymupdf.Page, xrefs: set[int]) -> None:
    tokens = _page_annots_tokens(document, page.xref)
    kept = [token for token in tokens if int(token.split()[0]) not in xrefs]
    document.raw.xref_set_key(page.xref, "Annots", (b"[" + b" ".join(kept) + b"]").decode("ascii"))


_ACROFORM_FIELDS_RE = re.compile(r"/Fields\s*\[([^\]]*)\]")


def _acroform_text(document: Document) -> tuple[str, int | None]:
    """The AcroForm dict's own text, and -- if it's its own indirect object rather than
    inline in the catalog -- the xref to write it back to. `document.raw.xref_get_key`
    reports an inline dict's full text directly (e.g. after page.add_widget or this
    module's own writes, both confirmed in a REPL check to leave it inline); some
    externally-produced files instead point `/AcroForm` at a separate indirect object,
    which is handled by resolving to that object's own text instead."""
    doc = document.raw
    catalog = doc.pdf_catalog()
    kind, raw = doc.xref_get_key(catalog, "AcroForm")
    if kind == "null":
        return "<< /Fields [] >>", None
    if kind == "xref":
        target = int(raw.split()[0])
        return doc.xref_object(target), target
    if kind == "dict":
        return raw, None
    raise OpValidationError(f"unexpected /AcroForm value type {kind!r} on this document")


def _write_acroform_text(document: Document, text: str, indirect_xref: int | None) -> None:
    doc = document.raw
    if indirect_xref is None:
        doc.xref_set_key(doc.pdf_catalog(), "AcroForm", text)
    else:
        doc.update_object(indirect_xref, text)


def _append_acroform_fields(document: Document, xrefs: list[int]) -> None:
    text, indirect = _acroform_text(document)
    additions = " ".join(f"{xref} 0 R" for xref in xrefs)
    match = _ACROFORM_FIELDS_RE.search(text)
    if match:
        existing = match.group(1).strip()
        merged = f"{existing} {additions}".strip() if existing else additions
        new_text = text[: match.start()] + f"/Fields [{merged}]" + text[match.end() :]
    else:
        insert_at = text.index("<<") + 2
        new_text = text[:insert_at] + f" /Fields [{additions}] " + text[insert_at:]
    _write_acroform_text(document, new_text, indirect)


def _remove_acroform_fields(document: Document, xrefs: set[int]) -> None:
    text, indirect = _acroform_text(document)
    match = _ACROFORM_FIELDS_RE.search(text)
    if not match:
        return
    tokens = _ANNOT_REF_RE.findall(match.group(1).encode("latin-1"))
    kept = [token for token in tokens if int(token.split()[0]) not in xrefs]
    new_text = text[: match.start()] + f"/Fields [{b' '.join(kept).decode('ascii')}]" + text[match.end() :]
    _write_acroform_text(document, new_text, indirect)


def _radio_parent_xref(document: Document, widget: pymupdf.Widget) -> int:
    kind, raw = document.raw.xref_get_key(widget.xref, "Parent")
    if kind != "xref":
        raise OpValidationError(f"field {widget.field_name!r} has no /Parent -- not a real radio group")
    return int(raw.split()[0])


def _build_radio_group(
    document: Document,
    page: pymupdf.Page,
    name: str,
    options: list[str],
    rects: list[Rect],
    selected: str | None,
) -> int:
    """Write a real /Parent+/Kids radio group directly (see this section's own docstring
    for why) and wire it into the page's /Annots and the document's /AcroForm /Fields.
    Returns the parent field's xref; the caller must reload the page afterwards."""
    if len(options) < 2:
        raise OpValidationError(f"a radio group needs at least two options, got {options!r}")
    if len(options) != len(rects):
        raise OpValidationError(f"radio 'options' and 'rects' must be the same length ({len(options)} vs {len(rects)})")
    if len(set(options)) != len(options):
        raise OpValidationError(f"duplicate radio option name(s): {options}")
    if selected is not None and selected not in options:
        raise OpValidationError(f"radio value {selected!r} is not one of {options}")
    # Validate every option's rect before writing anything (same "nothing applied until
    # everything validates" discipline fill_fields already uses): page.add_widget raises a
    # bare ValueError for a zero-area/inverted rect in the simple-field path, and this raw
    # xref path wouldn't raise anything at all for a bad one, so every rect is normalized
    # here explicitly instead of trusting the PDF writer to reject it.
    for rect in rects:
        _validate_rect(page, rect, label="radio option")

    doc = document.raw
    parent_xref = doc.get_new_xref()
    kid_xrefs: list[int] = []
    for option, rect in zip(options, rects, strict=True):
        box = pymupdf.Rect(rect)
        width, height = box.width, box.height
        on_xref = _make_appearance_xobject(document, (width, height), f"q 0 0 0 rg 0 0 {width} {height} re f Q")
        off_xref = _make_appearance_xobject(document, (width, height), "")
        as_state = _pdf_name_token(option) if option == selected else "/Off"
        option_name = _pdf_name_token(option)
        kid_xref = doc.get_new_xref()
        doc.update_object(
            kid_xref,
            f"<< /Type /Annot /Subtype /Widget /Rect [{box.x0} {box.y0} {box.x1} {box.y1}] "
            f"/Parent {parent_xref} 0 R /AS {as_state} /F 4 "
            f"/AP << /N << {option_name} {on_xref} 0 R /Off {off_xref} 0 R >> >> >>",
        )
        kid_xrefs.append(kid_xref)

    kids_text = " ".join(f"{xref} 0 R" for xref in kid_xrefs)
    value_token = _pdf_name_token(selected) if selected is not None else "/Off"
    doc.update_object(
        parent_xref,
        f"<< /FT /Btn /T {_pdf_string_literal(name)} /Ff 32768 /V {value_token} /Kids [{kids_text}] >>",
    )
    _append_page_annots(document, page, kid_xrefs)
    _append_acroform_fields(document, [parent_xref])
    return parent_xref


def create_field(
    document: Document,
    page_index: int,
    *,
    field_type: str,
    name: str,
    rect: Rect | None = None,
    options: list[str] | None = None,
    rects: list[Rect] | None = None,
    value: str | bool | None = None,
    font: str = "Helv",
    size: float = 0.0,
    multiline: bool = False,
) -> FieldInfo:
    """FRM-03: add a new AcroForm field to `page_index` and return it as FRM-01 would
    report it. `field_type` is one of "text", "checkbox", "radio", "dropdown", "listbox"
    or "signature".

    - text: `value` (str, default ""), `font` (one of Cour/TiRo/Helv/ZaDb -- see FRM-02's
      own docstring for why those four), `size` (0 means "auto-size", pymupdf's own
      default), `multiline`.
    - checkbox: `value` (bool, default False).
    - dropdown / listbox: `options` (required, at least one), `value` (one of `options`
      or None for "nothing selected").
    - radio: a GROUP, not a single widget -- give `options` (at least two, the export
      value of each button) and `rects` (one rect per option, same length/order as
      `options`); the top-level `rect` argument is ignored for this type. `value` is
      one of `options` or None.
    - signature: an interactive /Sig placeholder field (not SIG-01's drawn/typed/image
      stamp, which is ordinary page content, not an AcroForm field) -- a later PAdES
      signer (SIG-02) targets this field by name. Carries no `value` through this API.

    Refuses a name already used by another field on this page (AcroForm field names
    must be unique on a page for FRM-01/FRM-02 to address them unambiguously)."""
    if field_type not in _CREATABLE_TYPES:
        raise OpValidationError(f"unknown field_type {field_type!r}; choose one of {sorted(_CREATABLE_TYPES)}")
    if not name:
        raise OpValidationError("a field needs a non-empty name")
    page = document.raw[page_index]
    if name in _widgets_by_field(page):
        raise OpValidationError(f"a field named {name!r} already exists on page {page_index}")

    if field_type == "radio":
        if options is None or rects is None:
            raise OpValidationError("a radio field needs 'options' and 'rects' (one rect per option)")
        if value is not None and not isinstance(value, str):
            raise OpValidationError(f"a radio field's value must be a string or None, not {value!r}")
        _build_radio_group(document, page, name, options, rects, value)
        document.raw.reload_page(page)
        page = document.raw[page_index]
        return _field_info(name, _widgets_by_field(page)[name], page_index)

    if rect is None:
        raise OpValidationError(f"a {field_type} field needs a 'rect'")
    box = _validate_rect(page, rect)

    widget = pymupdf.Widget()
    widget.field_name = name
    widget.rect = box
    widget.field_type = _FIELD_TYPE_CONST[field_type]

    if field_type == "text":
        if value is not None and not isinstance(value, str):
            raise OpValidationError(f"a text field's value must be a string, not {value!r}")
        widget.field_value = value or ""
        widget.text_font = font
        # pymupdf's stub narrows text_fontsize to int; it accepts (and this module needs) a float.
        widget.text_fontsize = size  # type: ignore[assignment]
        if multiline:
            widget.field_flags = _MULTILINE_FLAG
    elif field_type == "checkbox":
        if value is not None and not isinstance(value, bool):
            raise OpValidationError(f"a checkbox field's value must be a bool, not {value!r}")
        widget.field_value = bool(value)
    elif field_type in ("dropdown", "listbox"):
        if not options:
            raise OpValidationError(f"a {field_type} field needs at least one option")
        if value is not None and value not in options:
            raise OpValidationError(f"value {value!r} is not one of {options}")
        widget.choice_values = list(options)
        widget.field_value = value or ""
    # signature: no further fields to set.

    page.add_widget(widget)
    return _field_info(name, _widgets_by_field(page)[name], page_index)


def edit_field(document: Document, page_index: int, name: str, **changes: object) -> FieldInfo:
    """FRM-03: change an existing field's type-specific properties (e.g. a dropdown's
    `options`, a text field's `font`/`size`/`multiline`, or any simple field's `rect`)
    after creation. Refuses a change not applicable to the field's own type (e.g. giving
    `options` to a text field), and refuses unknown field/page the same way fill_fields
    does. A radio group's `options` (with matching `rects`) rebuilds the whole group
    (see `_edit_radio_group`); this never changes which radio button -- if any -- is
    currently selected unless that option was itself removed, in which case the
    selection is cleared rather than left pointing at a dropped option."""
    page = document.raw[page_index]
    groups = _widgets_by_field(page)
    is_radio = len(_require_known_field(groups, name, page_index)) > 1
    # Drop every live reference to `page` (including the ones `groups` holds, one per
    # widget on it) before a possible radio rebuild: pymupdf.Document.reload_page refuses
    # to run while any other Python reference to the same Page is still alive (confirmed
    # in a REPL check; engine.images._remove_placement hits the identical constraint, see
    # its own "del source_page" comment), and this function's own `page`/`groups` would
    # otherwise still be alive in this frame while `_edit_radio_group` is running.
    del page, groups
    if is_radio:
        return _edit_radio_group(document, page_index, name, changes)

    page = document.raw[page_index]
    widgets = _widgets_by_field(page)[name]
    widget = widgets[0]
    field_type = _field_type(widget)
    allowed = _EDITABLE_PROPS.get(field_type, frozenset())
    unknown = set(changes) - allowed
    if unknown:
        raise OpValidationError(
            f"field {name!r} ({field_type}) doesn't support editing {sorted(unknown)}; editable: {sorted(allowed)}"
        )
    if not changes:
        raise OpValidationError("no changes given")

    if field_type == "text":
        if "font" in changes:
            widget.text_font = str(changes["font"])
        if "size" in changes:
            widget.text_fontsize = float(changes["size"])  # type: ignore[arg-type,assignment]
        if "multiline" in changes:
            flags = widget.field_flags or 0
            flags = flags | _MULTILINE_FLAG if changes["multiline"] else flags & ~_MULTILINE_FLAG
            widget.field_flags = flags
    elif field_type in ("dropdown", "listbox"):
        if "options" in changes:
            new_options = list(changes["options"])  # type: ignore[call-overload]
            if not new_options:
                raise OpValidationError(f"field {name!r} needs at least one option")
            widget.choice_values = new_options
            clear_value = widget.field_value not in new_options
            if clear_value:
                widget.field_value = ""
    if "rect" in changes:
        widget.rect = _validate_rect(page, changes["rect"])  # type: ignore[arg-type]

    widget.update()
    if field_type in ("dropdown", "listbox") and "options" in changes and clear_value:
        # widget.update() silently refuses to rewrite /V to empty for a choice widget
        # (confirmed: xref_get_key(widget.xref, "V") still showed the OLD value, both
        # right after update() and after a save/reopen, with no matching /Opt entry --
        # a corrupted field, not merely a stale in-memory read) -- clear /V directly.
        document.raw.xref_set_key(widget.xref, "V", "null")
    return _field_info(name, [widget], page_index)


def _edit_radio_group(document: Document, page_index: int, name: str, changes: dict[str, object]) -> FieldInfo:
    """Rebuild radio group `name` to a new set of options/rects. Takes only `page_index`
    and `name` -- never a Page/Widget object the caller already fetched -- precisely so
    the caller holds no competing live reference by the time this reloads the page; see
    edit_field's own comment for why that matters."""
    allowed = frozenset({"options", "rects"})
    unknown = set(changes) - allowed
    if unknown:
        raise OpValidationError(
            f"radio field {name!r} doesn't support editing {sorted(unknown)}; editable: {sorted(allowed)}"
        )
    if not changes:
        raise OpValidationError("no changes given")
    if ("options" in changes) != ("rects" in changes):
        raise OpValidationError("editing a radio group's options requires both 'options' and 'rects' together")

    page = document.raw[page_index]
    widgets = _widgets_by_field(page)[name]
    current_options = [widget.on_state() or "" for widget in widgets]
    selected = next(
        (
            state
            for widget, state in zip(widgets, current_options, strict=True)
            if widget.field_value not in (None, "Off")
        ),
        None,
    )
    new_options: list[str] = list(changes.get("options", current_options))  # type: ignore[call-overload]
    new_rects: list[Rect] = list(
        changes.get("rects", [_as_rect(widget.rect) for widget in widgets])  # type: ignore[call-overload]
    )
    if selected not in new_options:
        selected = None
    parent_xref = _radio_parent_xref(document, widgets[0])
    kid_xrefs = {widget.xref for widget in widgets}
    del widgets  # see this function's own note on live references, below

    # The old kids are removed with a raw /Annots rewrite (_remove_page_annots), *not*
    # page.delete_widget: confirmed in a REPL check that calling page.delete_widget and
    # then pymupdf.Document.reload_page on the same Page object -- even with zero other
    # live references to it by then -- hits the same internal assertion this whole
    # function is already careful to avoid (reload_page gets back the same internal
    # pointer it started with and refuses to proceed, as if nothing had reloaded).
    # Going fully through this module's own raw-xref helpers for *both* the removal and
    # the rebuild, with exactly one reload_page at the very end, sidesteps it.
    _remove_page_annots(document, page, kid_xrefs)
    _remove_acroform_fields(document, {parent_xref})
    _build_radio_group(document, page, name, new_options, new_rects, selected)
    document.raw.reload_page(page)
    del page
    page = document.raw[page_index]
    return _field_info(name, _widgets_by_field(page)[name], page_index)


def delete_field(document: Document, page_index: int, name: str) -> None:
    """FRM-03: remove a field from both the page's annotations and the AcroForm's
    field tree. A radio group's every button is removed together. Refuses an
    unknown field/page name the same way fill_fields does. No page reload is needed
    here (unlike field creation/editing): `page.delete_widget` -- pymupdf's own API,
    unlike this module's raw-xref radio *construction* -- already live-updates the
    page's widget list immediately, confirmed in a REPL check."""
    page = document.raw[page_index]
    groups = _widgets_by_field(page)
    widgets = _require_known_field(groups, name, page_index)

    if len(widgets) > 1:
        parent_xref = _radio_parent_xref(document, widgets[0])
        for widget in widgets:
            page.delete_widget(widget)
        _remove_acroform_fields(document, {parent_xref})
    else:
        page.delete_widget(widgets[0])


class FillResult(BaseModel):
    """FRM-02: what :func:`fill_fields` changed. ``unknown`` is always empty when this
    comes straight from :func:`fill_fields` itself (an unknown name there refuses the
    whole call instead); FRM-07's :func:`engine.form_data.import_form_data` is the one
    caller that fills it in, since an unrecognized field name on import is reported
    rather than treated as fatal (see that module)."""

    model_config = ConfigDict(frozen=True)

    filled: list[str]
    unknown: list[str] = Field(default_factory=list)


_CHECKBOX_TRUE_SPELLINGS = ("true", "1")
_CHECKBOX_FALSE_SPELLINGS = ("false", "0")

# A degenerate appearance stream PyMuPDF's widget.update() can produce when the value needs a
# glyph the field's font (always one of Cour/TiRo/Helv/ZaDb -- Widget._adjust_font forces this
# on every update(), confirmed by reading the installed source; there is no way through this API
# to give a text field a Unicode-coverage font instead) can't supply: the text operator is still
# written, but with a zero font size (`/F0 0 Tf`) and a zeroed-out text matrix, so nothing is
# drawn -- "filled" silently produces no visible ink. Verified with an emoji value (0 nonwhite
# pixels in the field rect afterwards; plain ASCII/Latin-1/Cyrillic/CJK all render fine).
_ZERO_FONT_SIZE_RE = re.compile(rb"(?<![\w.])0+(?:\.0+)?\s+Tf\b")


def _coerce_checkbox(name: str, raw_value: object) -> bool:
    if isinstance(raw_value, bool):
        return raw_value
    if isinstance(raw_value, str):
        low = raw_value.strip().lower()
        if low in _CHECKBOX_TRUE_SPELLINGS:
            return True
        if low in _CHECKBOX_FALSE_SPELLINGS:
            return False
    raise OpValidationError(
        f"field {name!r} is a checkbox; give a bool, or one of "
        f"{_CHECKBOX_TRUE_SPELLINGS + _CHECKBOX_FALSE_SPELLINGS!r} (case-insensitive), not {raw_value!r}"
    )


def _appearance_is_degenerate(document: Document, widget: pymupdf.Widget) -> bool:
    kind, raw = document.raw.xref_get_key(widget.xref, "AP/N")
    if kind != "xref":
        return False
    try:
        ap_xref = int(raw.split()[0])
        content = document.raw.xref_stream(ap_xref)
    except (ValueError, IndexError, RuntimeError):  # pragma: no cover -- defensive, not expected
        return False
    return bool(_ZERO_FONT_SIZE_RE.search(content))


def fill_fields(document: Document, page_index: int, values: dict[str, object]) -> FillResult:
    """FRM-02: set each named field's value on `page_index` and regenerate its
    appearance. Every value is validated against every field *before* anything is
    changed: an unknown field name, a checkbox value that isn't a bool or one of a
    small set of explicit spellings, or a value that isn't one of a dropdown's /
    listbox's / radio's options, refuses the whole call rather than partially
    applying it (SPEC.md section 8.3: never guess, never half-do). A text value
    that needs a glyph the field's font can't supply (PyMuPDF's widget appearance
    regeneration is limited to four built-in fonts -- Cour/TiRo/Helv/ZaDb -- with
    no way to attach a Unicode-coverage font to an AcroForm text field through this
    API) is refused the same way, *after* the attempt, rather than silently
    succeeding with an invisible field: PyMuPDF can produce a zero-size appearance
    (0 Tf) with no visible ink instead of raising anything itself."""
    page = document.raw[page_index]
    groups = _widgets_by_field(page)

    unknown = [name for name in values if name not in groups]
    if unknown:
        raise OpValidationError(f"unknown field name(s) on page {page_index}: {', '.join(sorted(unknown))}")

    # Validate every value up front (still nothing applied yet).
    plans: list[tuple[str, pymupdf.Widget, object]] = []
    for name, raw_value in values.items():
        widgets = groups[name]
        if len(widgets) > 1:  # radio group
            options = {widget.on_state(): widget for widget in widgets}
            target = options.get(str(raw_value))
            if target is None:
                raise OpValidationError(f"field {name!r} has no option {raw_value!r}; choices are {sorted(options)}")
            plans.append((name, target, target.on_state()))
            continue

        widget = widgets[0]
        field_type = _field_type(widget)
        if field_type == "signature":
            raise OpValidationError(f"field {name!r} is a signature field; it is not filled through fill_fields")
        if field_type in ("dropdown", "listbox"):
            choices = list(widget.choice_values or [])
            if raw_value not in choices:
                raise OpValidationError(f"field {name!r} has no option {raw_value!r}; choices are {choices}")
            plans.append((name, widget, raw_value))
        elif field_type == "checkbox":
            plans.append((name, widget, _coerce_checkbox(name, raw_value)))
        else:  # text, unknown: accept anything stringifiable
            plans.append((name, widget, "" if raw_value is None else str(raw_value)))

    unrenderable: list[str] = []
    for name, widget, value in plans:
        widget.field_value = value
        widget.update()
        if value not in ("", False) and _appearance_is_degenerate(document, widget):
            unrenderable.append(name)
    if unrenderable:
        raise OpValidationError(
            "field(s) "
            + ", ".join(sorted(unrenderable))
            + ": the given value needs a character this field's font can't display (e.g. an emoji or"
            " another glyph outside Cour/TiRo/Helv/ZaDb's coverage), so nothing would actually be"
            " visible; use a value within the field's font, or edit the field's own font first"
        )

    return FillResult(filled=sorted(values))


def _page_annots_tokens(document: Document, page_xref: int) -> list[bytes]:
    kind, raw = document.raw.xref_get_key(page_xref, "Annots")
    if kind != "array":
        return []
    return _ANNOT_REF_RE.findall(raw.encode("latin-1"))


def set_tab_order(document: Document, page_index: int, field_names: list[str]) -> None:
    """FRM-05: reorder `page_index`'s widget annotations to match `field_names`
    (a field's radio-group kids move together, in their existing relative order).
    A field left out of `field_names` is placed after every named field, in its
    current relative order -- never spliced into the middle. Sets the page's
    ``/Tabs`` to ``/S`` (structure order, i.e. "follow /Annots") so a viewer
    actually honours the order written here."""
    page = document.raw[page_index]
    groups = _widgets_by_field(page)

    if len(set(field_names)) != len(field_names):
        raise OpValidationError(f"duplicate field name(s) in tab order: {field_names}")
    unknown = [name for name in field_names if name not in groups]
    if unknown:
        raise OpValidationError(f"unknown field name(s) on page {page_index}: {', '.join(unknown)}")

    ordered_xrefs: list[int] = []
    remaining = dict(groups)
    for name in field_names:
        ordered_xrefs.extend(widget.xref for widget in remaining.pop(name))
    for widgets in remaining.values():
        ordered_xrefs.extend(widget.xref for widget in widgets)

    widget_xrefs = {widget.xref for widgets in groups.values() for widget in widgets}
    tokens = _page_annots_tokens(document, page.xref)
    new_tokens: list[bytes] = []
    queue = iter(ordered_xrefs)
    for token in tokens:
        xref = int(token.split()[0])
        if xref in widget_xrefs:
            new_tokens.append(f"{next(queue)} 0 R".encode("ascii"))
        else:
            new_tokens.append(token)

    new_array = b"[" + b" ".join(new_tokens) + b"]"
    document.raw.xref_set_key(page.xref, "Annots", new_array.decode("ascii"))
    document.raw.xref_set_key(page.xref, "Tabs", "/S")


class FlattenResult(BaseModel):
    """FRM-06: what :func:`flatten_form` changed."""

    model_config = ConfigDict(frozen=True)

    fields_flattened: int
    pages_affected: list[int]


_FLATTEN_CAPTURE_DPI = 300


def _any_widgets_remain(document: Document) -> bool:
    return any(next(document.raw[i].widgets(), None) is not None for i in range(document.page_count))


def _clear_acroform(document: Document) -> None:
    catalog = document.raw.pdf_catalog()
    document.raw.xref_set_key(catalog, "AcroForm", "null")


def flatten_form(document: Document, page_index: int | None = None) -> FlattenResult:
    """FRM-06: draw every widget's current appearance into static page content and
    remove the interactive AcroForm. ``page_index=None`` flattens the whole
    document (uses PyMuPDF's own ``bake()``, which already drops ``/AcroForm``
    entirely); an explicit page flattens only that page's widgets, by pasting
    each widget's own rendered pixels back as a static image (no page-scoped
    ``bake()`` exists in pymupdf 1.28.2), then clears ``/AcroForm`` only once no
    widget remains anywhere in the document. A document with no AcroForm
    fields is left unchanged -- not an error."""
    if page_index is None:
        count = sum(len(list(document.raw[i].widgets())) for i in range(document.page_count))
        if count:
            document.raw.bake(annots=False, widgets=True)
        return FlattenResult(fields_flattened=count, pages_affected=list(range(document.page_count)) if count else [])

    page = document.raw[page_index]
    widgets = list(page.widgets())
    if not widgets:
        return FlattenResult(fields_flattened=0, pages_affected=[])

    for widget in widgets:
        rect = widget.rect
        capture = page.get_pixmap(clip=rect, dpi=_FLATTEN_CAPTURE_DPI, annots=True)
        page.delete_widget(widget)
        page.insert_image(rect, pixmap=capture)

    if not _any_widgets_remain(document):
        _clear_acroform(document)

    return FlattenResult(fields_flattened=len(widgets), pages_affected=[page_index])


# -- FRM-08: XFA detection ------------------------------------------------------------------


class XFAReport(BaseModel):
    """FRM-08: what :func:`detect_xfa` found."""

    model_config = ConfigDict(frozen=True)

    has_xfa: bool
    has_static_fields: bool
    """Whether this document also has ordinary (non-XFA) AcroForm fields on any page --
    FRM-01/FRM-02 keep listing and filling those regardless of `has_xfa`."""
    warning: str | None
    """Set whenever `has_xfa` is true: only the static AcroForm fields are read/filled
    here, never the XFA layer itself. ``None`` when this document has no XFA."""


_XFA_WARNING = (
    "this document uses XFA (XML Forms Architecture) form fields; only its conventional "
    "AcroForm fields, if any, are listed or filled here -- the XFA layer itself is not "
    "read, rendered or written"
)


def detect_xfa(document: Document) -> XFAReport:
    """FRM-08: whether `document`'s AcroForm carries an ``/XFA`` entry (Adobe's dynamic/
    static XML form layer, which this engine does not process), reported distinctly from
    an ordinary AcroForm so a caller can warn before opening/filling such a document.
    Checked with a read-only pikepdf parse of a plain (decrypted) copy of the document's
    current bytes (the same technique engine.structure uses for metadata) rather than
    engine.forms's own pymupdf-level xref helpers, since pikepdf's dict ``in`` operator
    is a direct, unambiguous way to ask "does this dict have this key" -- confirmed
    against the installed pikepdf 10.14.0 in a REPL check, including that a document
    with no /AcroForm at all (so no possible /XFA) reports `has_xfa=False`, never raises.
    A document with both XFA and ordinary fields still lists/fills the latter normally
    (confirmed: list_fields reads straight from pymupdf's own widget list, which is
    populated independently of /XFA)."""
    with pikepdf.open(io.BytesIO(plain_bytes(document.raw))) as pdf:
        acroform = pdf.Root.get("/AcroForm")
        has_xfa = acroform is not None and "/XFA" in acroform

    has_static_fields = any(list_fields(document, i) for i in range(document.page_count))
    return XFAReport(
        has_xfa=has_xfa,
        has_static_fields=has_static_fields,
        warning=_XFA_WARNING if has_xfa else None,
    )
