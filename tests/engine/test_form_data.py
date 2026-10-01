"""P6: form data import/export (FRM-07); see engine.form_data."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from engine.document import Document
from engine.errors import OpValidationError
from engine.ops.base import parse_op


def _apply(doc: Document, data: dict[str, object]) -> object:
    op = parse_op(data)
    op.check_pages(doc)
    return op.apply(doc)


def _build_form(tmp_path: Path, name: str = "form.pdf") -> Path:
    """One page with a text, checkbox, dropdown and listbox field created through this
    module's own FRM-03 Ops (not hand-built with pikepdf): exercising the same
    create_field path this test's round trip will later re-create from, so a round trip
    through export/import is tested against the exact same field-creation path FRM-03
    tests use, not a separately hand-built fixture that could silently diverge from it."""
    doc = pymupdf.open()
    doc.new_page()
    path = tmp_path / name
    doc.save(path)
    doc.close()
    with Document.open(path) as created:
        _apply(
            created,
            {
                "op": "create_field",
                "page_index": 0,
                "field_type": "text",
                "name": "full_name",
                "rect": [50, 50, 250, 70],
            },
        )
        _apply(
            created,
            {
                "op": "create_field",
                "page_index": 0,
                "field_type": "checkbox",
                "name": "agree",
                "rect": [50, 90, 70, 110],
            },
        )
        _apply(
            created,
            {
                "op": "create_field",
                "page_index": 0,
                "field_type": "dropdown",
                "name": "color",
                "rect": [50, 130, 250, 150],
                "options": ["Red", "Green", "Blue"],
            },
        )
        _apply(
            created,
            {
                "op": "create_field",
                "page_index": 0,
                "field_type": "listbox",
                "name": "fruit",
                "rect": [50, 170, 250, 220],
                "options": ["Apple", "Pear", "Fig"],
            },
        )
        _apply(
            created,
            {
                "op": "create_field",
                "page_index": 0,
                "field_type": "signature",
                "name": "sig1",
                "rect": [50, 300, 250, 340],
            },
        )
        _apply(
            created,
            {
                "op": "create_field",
                "page_index": 0,
                "field_type": "radio",
                "name": "plan",
                "options": ["Basic", "Pro"],
                "rects": [[50, 240, 65, 255], [90, 240, 105, 255]],
            },
        )
        created.save(path, overwrite=True)
    return path


_FILL_VALUES = {"full_name": "Hello World", "agree": True, "color": "Blue", "fruit": "Pear", "plan": "Pro"}


@pytest.mark.parametrize("fmt", ["fdf", "xfdf", "json", "csv"])
@pytest.mark.feature("FRM-07")
def test_export_contains_every_field_name_and_value(tmp_path: Path, fmt: str) -> None:
    path = _build_form(tmp_path)
    with Document.open(path) as doc:
        _apply(doc, {"op": "fill_fields", "page_index": 0, "values": _FILL_VALUES})
        data = _apply(doc, {"op": "export_form_data", "page_index": 0, "format": fmt})

    assert isinstance(data, bytes) and data
    text = data.decode("utf-8", errors="replace")
    for name in ("full_name", "agree", "color", "fruit", "plan", "sig1"):
        assert name in text


@pytest.mark.parametrize("fmt", ["fdf", "xfdf", "json", "csv"])
@pytest.mark.feature("FRM-07", criterion=2)
def test_round_trip_every_format_fills_a_fresh_copy_to_the_original_values(tmp_path: Path, fmt: str) -> None:
    filled_path = _build_form(tmp_path, "filled.pdf")
    with Document.open(filled_path) as filled:
        _apply(filled, {"op": "fill_fields", "page_index": 0, "values": _FILL_VALUES})
        exported = _apply(filled, {"op": "export_form_data", "page_index": 0, "format": fmt})

    fresh_path = _build_form(tmp_path, f"fresh_{fmt}.pdf")
    with Document.open(fresh_path) as fresh:
        import base64

        result = _apply(
            fresh,
            {
                "op": "import_form_data",
                "page_index": 0,
                "format": fmt,
                "data_base64": base64.b64encode(exported).decode("ascii"),  # type: ignore[arg-type]
            },
        )
        assert sorted(result.unknown) == []  # type: ignore[attr-defined]
        listed = _apply(fresh, {"op": "page_fields", "page_index": 0})

    by_name = {f.name: f.value for f in listed}  # type: ignore[attr-defined]
    assert by_name["full_name"] == "Hello World"
    assert by_name["agree"] is True
    assert by_name["color"] == "Blue"
    assert by_name["fruit"] == "Pear"
    assert by_name["plan"] == "Pro"


@pytest.mark.feature("FRM-07", criterion=3)
def test_unknown_field_name_on_import_is_reported_not_fatal_or_silently_dropped(tmp_path: Path) -> None:
    import base64

    path = _build_form(tmp_path)
    data = b'{"full_name": "Known Value", "totally_bogus_field": "ignored"}'
    with Document.open(path) as doc:
        result = _apply(
            doc,
            {
                "op": "import_form_data",
                "page_index": 0,
                "format": "json",
                "data_base64": base64.b64encode(data).decode("ascii"),
            },
        )
        listed = _apply(doc, {"op": "page_fields", "page_index": 0})

    assert result.unknown == ["totally_bogus_field"]  # type: ignore[attr-defined]
    assert result.filled == ["full_name"]  # type: ignore[attr-defined]
    by_name = {f.name: f.value for f in listed}  # type: ignore[attr-defined]
    assert by_name["full_name"] == "Known Value"


@pytest.mark.feature("FRM-07", criterion=4)
def test_checkbox_radio_dropdown_round_trip_their_selected_option_not_a_raw_string(tmp_path: Path) -> None:
    """engine.form_data's JSON export is a flat {name: value} object -- bool for a
    checkbox, the exact option string for radio/dropdown -- so this is really testing
    that fidelity survives the export/import boundary, not just that *some* string made
    it across (e.g. a checkbox "false" string must become Python False, not a truthy
    non-empty string, and a dropdown/radio's value must be the option name itself)."""
    import base64
    import json

    path = _build_form(tmp_path)
    with Document.open(path) as doc:
        _apply(
            doc, {"op": "fill_fields", "page_index": 0, "values": {"agree": False, "color": "Green", "plan": "Basic"}}
        )
        exported = _apply(doc, {"op": "export_form_data", "page_index": 0, "format": "json"})

    parsed = json.loads(exported)  # type: ignore[arg-type]
    assert parsed["agree"] is False  # a real JSON boolean, not the string "false"
    assert parsed["color"] == "Green"
    assert parsed["plan"] == "Basic"

    fresh_path = _build_form(tmp_path, "fresh_fidelity.pdf")
    with Document.open(fresh_path) as fresh:
        _apply(
            fresh,
            {
                "op": "import_form_data",
                "page_index": 0,
                "format": "json",
                "data_base64": base64.b64encode(exported).decode("ascii"),  # type: ignore[arg-type]
            },
        )
        listed = _apply(fresh, {"op": "page_fields", "page_index": 0})
    by_name = {f.name: f.value for f in listed}  # type: ignore[attr-defined]
    assert by_name["agree"] is False
    assert by_name["color"] == "Green"
    assert by_name["plan"] == "Basic"


@pytest.mark.feature("FRM-07")
def test_unknown_format_is_refused(tmp_path: Path) -> None:
    path = _build_form(tmp_path)
    with Document.open(path) as doc, pytest.raises(OpValidationError):
        _apply(doc, {"op": "export_form_data", "page_index": 0, "format": "docx"})


# Regression: _parse_fdf used to be a bare regex scan for /T.../V... patterns with no
# notion of "this isn't FDF at all" -- garbage bytes or a file truncated mid-write just
# matched zero entries and silently came back as "0 fields changed", unlike XFDF/JSON/CSV
# (which go through a real parser that itself rejects malformed input on the equivalent
# garbage). _parse_fdf now requires a %FDF-... header, an /FDF dict, and a trailer/%%EOF
# before scanning at all.
@pytest.mark.feature("FRM-07")
def test_garbage_fdf_bytes_raise_instead_of_importing_nothing_silently(tmp_path: Path) -> None:
    import base64

    path = _build_form(tmp_path)
    garbage = b"this is not a pdf or an fdf file, just plain garbage bytes"
    with Document.open(path) as doc, pytest.raises(OpValidationError):
        _apply(
            doc,
            {
                "op": "import_form_data",
                "page_index": 0,
                "format": "fdf",
                "data_base64": base64.b64encode(garbage).decode("ascii"),
            },
        )


@pytest.mark.feature("FRM-07")
def test_truncated_fdf_raises_instead_of_importing_nothing_silently(tmp_path: Path) -> None:
    import base64

    path = _build_form(tmp_path)
    with Document.open(path) as doc:
        _apply(doc, {"op": "fill_fields", "page_index": 0, "values": {"full_name": "Hello World"}})
        exported = _apply(doc, {"op": "export_form_data", "page_index": 0, "format": "fdf"})

    # cut it off mid-value, before the trailer/%%EOF this module's own writer always adds
    cut_point = exported.index(b"Hello")  # type: ignore[arg-type]
    truncated = exported[:cut_point] + b"Hel"  # type: ignore[index]

    with Document.open(path) as doc2, pytest.raises(OpValidationError):
        _apply(
            doc2,
            {
                "op": "import_form_data",
                "page_index": 0,
                "format": "fdf",
                "data_base64": base64.b64encode(truncated).decode("ascii"),
            },
        )
