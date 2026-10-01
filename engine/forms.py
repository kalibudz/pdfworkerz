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

import re
from typing import Literal

import pymupdf
from pydantic import BaseModel, ConfigDict

from engine.document import Document
from engine.errors import OpValidationError

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


class FillResult(BaseModel):
    """FRM-02: what :func:`fill_fields` changed."""

    model_config = ConfigDict(frozen=True)

    filled: list[str]


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
