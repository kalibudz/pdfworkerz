"""FRM-07: import/export form field data as FDF, XFDF, JSON or CSV.

Design notes
------------
- **JSON** is a flat ``{field_name: value}`` object -- exactly :class:`engine.forms.FieldInfo`'s
  own ``value`` typing (``bool`` for a checkbox, the selected option string for a
  radio/dropdown/listbox, text for a text field, ``null`` for an unset/signature field). No
  richer shape is needed to satisfy "checkbox/radio/dropdown values round-trip their selected
  option, not just a raw string": JSON already distinguishes a bare ``true``/``false`` from a
  quoted string, and the string *is* the selected option's own name (never a numeric index or
  other indirection), so a round trip through this module already carries that distinction.
- **CSV** has three columns, ``name,type,value``: ``type`` is FRM-01's own ``field_type``
  (informational -- re-reading a CSV never trusts it over the *target* document's real field
  type, see below), and ``value`` is the same Python value as JSON's, but run through
  ``json.dumps``/``json.loads`` so a CSV cell -- which is plain text -- still tells a checkbox's
  ``true``/``false`` apart from the literal strings ``"true"``/``"false"`` a text field might
  legitimately contain, and an unset field's empty selection (``null``) apart from an empty
  string value.
- **FDF** and **XFDF** are real formats with no native boolean type, so a checkbox's value is
  written as the PDF convention of a Name matching the on-state (``/Yes`` for true, ``/Off`` for
  false) and a radio/dropdown/listbox's selected option as a Name matching that option; a text
  field's value is written as a literal PDF string (FDF) or element text (XFDF). Importing
  either format therefore can't tell a bool from a string by the file alone -- the *target*
  document's own current field type (via :func:`engine.forms.list_fields`) decides how each
  raw value is interpreted (e.g. any FDF/XFDF value other than ``Off``/``false``/``0`` becomes
  ``True`` for a field the target document says is a checkbox); a radio/dropdown/listbox value
  is passed straight through as the option name, which :func:`engine.forms.fill_fields` already
  validates against that field's real choices.

Unknown field names on import (FRM-07 criterion 3: "reports it rather than failing the whole
import or silently dropping it") are filtered out *before* calling fill_fields (which would
otherwise refuse the whole call for even one unknown name, by FRM-02's own design) and listed
in the returned :class:`~engine.forms.FillResult`'s ``unknown``. A signature field's value is
always exported as ``null``/absent (fill_fields itself refuses to fill a signature field) and
is skipped on import for the same reason, without being reported as unknown (it *is* a known
field, just not one this path fills).

Known limitations (reviewed, deliberately not "fixed" -- see each note for why)
--------------------------------------------------------------------------------
- **CSV field *names* are not defused against spreadsheet formula injection.** A field named
  e.g. ``=cmd|'/c calc'!A1`` (legal as an AcroForm field name; only a maliciously-crafted PDF
  would use one) is written to the ``name`` column byte-for-byte, so opening the exported CSV
  directly in Excel/Sheets could trigger the classic leading-``=``/``+``/``-``/``@`` DDE/formula
  vector. Field *values* are safe only as a side effect of ``json.dumps`` always wrapping a
  string value in quotes first. The usual mitigation (prefixing a defusing character such as a
  single quote to any cell starting with a trigger character) was deliberately not applied here:
  it is not reliably invertible (a real field name that itself starts with ``'=...`` would be
  indistinguishable on import from a guarded ``=...``), and this module's own FRM-07 round-trip
  guarantee -- export then import must reproduce the original field name exactly -- takes
  priority over guarding a local, single-user file against a spreadsheet application the engine
  doesn't control. Treat an exported CSV as data to re-import here, not as a file to open
  directly in a spreadsheet app from an untrusted source PDF.
- **XFDF loses a ``\\r`` from a ``\\r\\n`` line ending in a multiline text value on round-trip.**
  Confirmed against the stdlib ``xml.etree.ElementTree``: every standards-compliant XML parser
  normalizes ``\\r\\n`` (and a bare ``\\r``) to ``\\n`` while parsing element text, per the XML 1.0
  spec's mandatory end-of-line handling (section 2.11) -- this is not a bug in this module's own
  (de)serialization, and avoiding it would mean no longer using a standards-compliant XML parser.
  FDF/JSON/CSV are unaffected (none of them is XML).
"""

from __future__ import annotations

import csv
import io
import json
import re
import xml.etree.ElementTree as ET  # nosec B405 -- parses a local XFDF file the user opened, not untrusted network XML
from typing import Literal

from engine.document import Document
from engine.errors import OpValidationError
from engine.forms import FieldInfo, FillResult, fill_fields, list_fields

FormDataFormat = Literal["fdf", "xfdf", "json", "csv"]

_XFDF_NS = "http://ns.adobe.com/xfdf/"
_CHECKBOX_OFF_SPELLINGS = ("off", "false", "0", "")


# -- export -----------------------------------------------------------------------------------


def _checkbox_token(value: bool) -> str:
    return "Yes" if value else "Off"


def _pdf_name_escape(value: str) -> str:
    # Same name-escaping convention engine.forms._pdf_name_token uses for radio options.
    return "".join(f"#{ord(ch):02x}" if ch in " \t\r\n()<>[]{}/%#" or ord(ch) < 33 else ch for ch in value)


def _pdf_string_escape(value: str) -> str:
    return value.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _export_token(field: FieldInfo) -> tuple[str, bool] | None:
    """The raw text to write for one field's value in FDF/XFDF, and whether it's a PDF
    Name (True) rather than a string/element-text (False). ``None`` means "write the
    field with no value at all" (an unset choice field, or a signature)."""
    if field.field_type == "signature" or field.value is None:
        return None
    if field.field_type == "checkbox":
        return _checkbox_token(bool(field.value)), True
    if field.field_type in ("radio", "dropdown", "listbox"):
        return str(field.value), True
    return str(field.value), False  # text, unknown


def _export_fdf(fields: list[FieldInfo]) -> bytes:
    entries = []
    for field in fields:
        token = _export_token(field)
        name = _pdf_string_escape(field.name)
        if token is None:
            entries.append(f"<< /T ({name}) >>")
            continue
        raw, is_name = token
        value = f"/{_pdf_name_escape(raw)}" if is_name else f"({_pdf_string_escape(raw)})"
        entries.append(f"<< /T ({name}) /V {value} >>")
    fields_text = "\n".join(entries)
    body = (
        f"%FDF-1.2\n1 0 obj\n<< /FDF << /Fields [\n{fields_text}\n] >> >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"
    )
    return body.encode("latin-1")


def _export_xfdf(fields: list[FieldInfo]) -> bytes:
    root = ET.Element("xfdf", xmlns=_XFDF_NS)
    fields_el = ET.SubElement(root, "fields")
    for field in fields:
        token = _export_token(field)
        field_el = ET.SubElement(fields_el, "field", name=field.name)
        if token is not None:
            raw, _is_name = token
            value_el = ET.SubElement(field_el, "value")
            value_el.text = raw
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8")


def _export_json(fields: list[FieldInfo]) -> bytes:
    return json.dumps({field.name: field.value for field in fields}, indent=2, ensure_ascii=False).encode("utf-8")


def _export_csv(fields: list[FieldInfo]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(["name", "type", "value"])
    for field in fields:
        writer.writerow([field.name, field.field_type, json.dumps(field.value)])
    return buffer.getvalue().encode("utf-8")


def export_form_data(document: Document, page_index: int, fmt: FormDataFormat) -> bytes:
    """FRM-07: every field on `page_index` -- name and current value -- as `fmt` bytes.
    A page with no fields still produces a validly-structured (empty) file in every format."""
    fields = list_fields(document, page_index)
    if fmt == "fdf":
        return _export_fdf(fields)
    if fmt == "xfdf":
        return _export_xfdf(fields)
    if fmt == "json":
        return _export_json(fields)
    if fmt == "csv":
        return _export_csv(fields)
    raise OpValidationError(f"unknown form data format {fmt!r}; choose one of fdf, xfdf, json, csv")


# -- import -----------------------------------------------------------------------------------

_FDF_ENTRY_RE = re.compile(rb"/T\s*\(((?:[^()\\]|\\.)*)\)\s*(?:/V\s*(?:\(((?:[^()\\]|\\.)*)\)|/([^\s/()<>\[\]]+)))?")


def _unescape_pdf_string(raw: bytes) -> str:
    out = bytearray()
    i = 0
    while i < len(raw):
        ch = raw[i]
        if ch == 0x5C and i + 1 < len(raw):  # backslash
            nxt = raw[i + 1 : i + 2]
            if nxt in (b"(", b")", b"\\"):
                out += nxt
                i += 2
                continue
            if nxt == b"n":
                out += b"\n"
                i += 2
                continue
            if nxt == b"r":
                out += b"\r"
                i += 2
                continue
            if nxt == b"t":
                out += b"\t"
                i += 2
                continue
        out.append(ch)
        i += 1
    return out.decode("latin-1")


def _unescape_pdf_name(raw: str) -> str:
    out = []
    i = 0
    while i < len(raw):
        if raw[i] == "#" and i + 2 < len(raw) + 1 and len(raw) - i > 2:
            try:
                out.append(chr(int(raw[i + 1 : i + 3], 16)))
                i += 3
                continue
            except ValueError:
                pass
        out.append(raw[i])
        i += 1
    return "".join(out)


def _parse_fdf(data: bytes) -> dict[str, str | None]:
    # Unlike XFDF/JSON/CSV -- each of which goes through a real parser that itself rejects
    # malformed input -- a bare regex scan for /T.../V... patterns has no notion of "this
    # isn't FDF at all": garbage bytes, or a file truncated mid-write, would otherwise just
    # match zero entries and silently come back as FillResult(filled=[], unknown=[]), not
    # an error. A real FDF file (including every one this module's own export_form_data
    # writes) has a recognizable %FDF-... header, an /FDF dict, and ends with a trailer and
    # %%EOF; a file missing any of those is rejected up front instead of being scanned.
    if not data.lstrip().startswith(b"%FDF-") or b"/FDF" not in data:
        raise OpValidationError("not a valid FDF file: missing the %FDF-... header or an /FDF dictionary")
    if b"trailer" not in data or b"%%EOF" not in data:
        raise OpValidationError("not a valid FDF file: missing its trailer/%%EOF -- it looks truncated")
    result: dict[str, str | None] = {}
    for match in _FDF_ENTRY_RE.finditer(data):
        name = _unescape_pdf_string(match.group(1))
        if match.group(2) is not None:
            result[name] = _unescape_pdf_string(match.group(2))
        elif match.group(3) is not None:
            result[name] = _unescape_pdf_name(match.group(3).decode("latin-1"))
        else:
            result[name] = None
    return result


def _parse_xfdf(data: bytes) -> dict[str, str | None]:
    try:
        root = ET.fromstring(data)  # nosec B314 -- stdlib XML parse of a local form-data file, not untrusted network XML
    except ET.ParseError as exc:
        raise OpValidationError(f"not a valid XFDF file: {exc}") from exc
    result: dict[str, str | None] = {}
    for field_el in root.iter():
        if field_el.tag.rsplit("}", 1)[-1] != "field":
            continue
        name = field_el.get("name")
        if not name:
            continue
        value_el = next((child for child in field_el if child.tag.rsplit("}", 1)[-1] == "value"), None)
        result[name] = value_el.text if value_el is not None else None
    return result


def _parse_json(data: bytes) -> dict[str, object]:
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError as exc:
        raise OpValidationError(f"not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise OpValidationError("form data JSON must be a flat {field_name: value} object")
    return parsed


def _parse_csv(data: bytes) -> dict[str, object]:
    text = data.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None or "name" not in reader.fieldnames or "value" not in reader.fieldnames:
        raise OpValidationError("form data CSV needs at least 'name' and 'value' columns")
    result: dict[str, object] = {}
    for row in reader:
        name = row["name"]
        if not name:
            continue
        try:
            result[name] = json.loads(row["value"])
        except json.JSONDecodeError as exc:
            raise OpValidationError(f"form data CSV: field {name!r}'s value column isn't valid JSON: {exc}") from exc
    return result


def _is_checkbox_false(raw: object) -> bool:
    return str(raw).strip().lower() in _CHECKBOX_OFF_SPELLINGS


def import_form_data(document: Document, page_index: int, fmt: FormDataFormat, data: bytes) -> FillResult:
    """FRM-07: apply previously-exported (or hand-written) field data to `page_index`.
    An unknown field name is reported in the result's ``unknown`` list rather than
    failing the whole import or being silently dropped; every recognized field's value
    is applied in one :func:`engine.forms.fill_fields` call (so still one undo step, and
    still refusing the whole call if a *known* field's value is invalid -- e.g. a
    dropdown option that doesn't exist -- consistent with fill_fields's own contract)."""
    if fmt == "fdf":
        raw: dict[str, object] = dict(_parse_fdf(data))
        string_typed = True
    elif fmt == "xfdf":
        raw = dict(_parse_xfdf(data))
        string_typed = True
    elif fmt == "json":
        raw = _parse_json(data)
        string_typed = False
    elif fmt == "csv":
        raw = _parse_csv(data)
        string_typed = False
    else:
        raise OpValidationError(f"unknown form data format {fmt!r}; choose one of fdf, xfdf, json, csv")

    current: dict[str, FieldInfo] = {field.name: field for field in list_fields(document, page_index)}
    unknown = sorted(name for name in raw if name not in current)

    values: dict[str, object] = {}
    for name, value in raw.items():
        field = current.get(name)
        if field is None or value is None or field.field_type == "signature":
            continue
        if string_typed and field.field_type == "checkbox":
            value = not _is_checkbox_false(value)
        values[name] = value

    applied = fill_fields(document, page_index, values)
    return FillResult(filled=applied.filled, unknown=unknown)


__all__ = [
    "FormDataFormat",
    "export_form_data",
    "import_form_data",
]
