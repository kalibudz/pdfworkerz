"""P6: AcroForm fields (FRM-01, FRM-02, FRM-05, FRM-06).

PyMuPDF's own widget-creation API (`page.add_widget`) does not build a real
``/Parent``/``/Kids`` radio group -- two widgets created with the same
`field_name` come out as independent top-level fields that both default to
the same on-state (verified while building this module; see engine/forms.py's
module docstring). A fixture that needs a genuine radio group is therefore
built directly with pikepdf, injected onto a page a pymupdf pass already
populated with the other field types, matching how real-world tools (and
this engine's own `set_tab_order`) actually structure one.
"""

from __future__ import annotations

from pathlib import Path

import pikepdf
import pymupdf
import pytest
from pikepdf import Array, Dictionary, Name

from engine.document import Document
from engine.errors import OpValidationError
from engine.ops.base import parse_op
from engine.ops.journal import UndoRedoJournal


def _apply(doc: Document, data: dict[str, object]) -> object:
    op = parse_op(data)
    op.check_pages(doc)
    return op.apply(doc)


def _add_widget(
    page: pymupdf.Page,
    name: str,
    rect: tuple[float, float, float, float],
    field_type: int,
    **kw: object,
) -> None:
    widget = pymupdf.Widget()
    widget.field_name = name
    widget.rect = pymupdf.Rect(rect)
    widget.field_type = field_type
    for key, value in kw.items():
        setattr(widget, key, value)
    page.add_widget(widget)


def _radio_appearance(pdf: pikepdf.Pdf, bbox: list[float], mark: bool) -> pikepdf.Object:
    content = "q 0 0 0 rg 0 0 15 15 re f Q" if mark else ""
    return pdf.make_stream(
        content.encode(),
        BBox=Array(bbox),
        Matrix=Array([1, 0, 0, 1, 0, 0]),
        Subtype=Name("/Form"),
        Type=Name("/XObject"),
        Resources=Dictionary(ProcSet=Array([Name("/PDF")])),
    )


def _inject_radio_group(
    path: Path,
    *,
    page_index: int,
    name: str,
    options: list[str],
    rects: list[tuple[float, float, float, float]],
) -> None:
    """Add a true radio group (shared `/Parent`, per-kid on-states) to an
    already-saved PDF's page, the only structure PyMuPDF's own widget-creation
    API can't build (see this module's docstring)."""
    pdf = pikepdf.open(path, allow_overwriting_input=True)
    page = pdf.pages[page_index]
    parent = pdf.make_indirect(
        Dictionary(FT=Name("/Btn"), T=pikepdf.String(name), Ff=32768, V=Name("/Off"), Kids=Array([]))
    )
    kids = []
    for option, rect in zip(options, rects, strict=True):
        off = _radio_appearance(pdf, [0, 0, rect[2] - rect[0], rect[3] - rect[1]], mark=False)
        on = _radio_appearance(pdf, [0, 0, rect[2] - rect[0], rect[3] - rect[1]], mark=True)
        kid = pdf.make_indirect(
            Dictionary(
                Type=Name("/Annot"),
                Subtype=Name("/Widget"),
                Rect=Array(list(rect)),
                Parent=parent,
                AS=Name("/Off"),
                F=4,
                AP=Dictionary(N=Dictionary({f"/{option}": on, "/Off": off})),
            )
        )
        kids.append(kid)
    parent.Kids = Array(kids)
    existing_annots = list(page.obj.get("/Annots", Array([])))
    page.obj.Annots = Array([*existing_annots, *kids])
    acroform = pdf.Root.get("/AcroForm")
    existing_fields = list(acroform.Fields) if acroform is not None else []
    pdf.Root.AcroForm = Dictionary(Fields=Array([*existing_fields, parent]), NeedAppearances=True)
    pdf.save(path)


def _build_full_form(tmp_path: Path, name: str = "full.pdf") -> Path:
    """One page with a text, checkbox, dropdown and listbox field (pymupdf), plus
    a genuine two-option radio group ("plan": Basic/Pro) injected with pikepdf."""
    doc = pymupdf.open()
    page = doc.new_page()
    _add_widget(page, "name", (50, 50, 250, 70), pymupdf.PDF_WIDGET_TYPE_TEXT, field_value="")
    _add_widget(page, "agree", (50, 90, 70, 110), pymupdf.PDF_WIDGET_TYPE_CHECKBOX, field_value=False)
    _add_widget(
        page,
        "color",
        (50, 130, 300, 150),
        pymupdf.PDF_WIDGET_TYPE_COMBOBOX,
        choice_values=["Red", "Green", "Blue"],
        field_value="Red",
    )
    _add_widget(
        page,
        "fruit",
        (50, 170, 300, 220),
        pymupdf.PDF_WIDGET_TYPE_LISTBOX,
        choice_values=["Apple", "Pear", "Fig"],
        field_value="Apple",
    )
    path = tmp_path / name
    doc.save(path)
    doc.close()
    _inject_radio_group(
        path,
        page_index=0,
        name="plan",
        options=["Basic", "Pro"],
        rects=[(50, 240, 65, 255), (90, 240, 105, 255)],
    )
    return path


def _empty_doc(tmp_path: Path, name: str = "empty.pdf") -> Path:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "No form fields here.", fontsize=12)
    path = tmp_path / name
    doc.save(path)
    doc.close()
    return path


def _field(fields: list[object], name: str) -> dict[str, object]:
    for field in fields:
        if field.name == name:  # type: ignore[attr-defined]
            return field.model_dump()  # type: ignore[attr-defined]
    raise AssertionError(f"field {name!r} not found among {[f.name for f in fields]}")  # type: ignore[attr-defined]


# -- FRM-01 list fields --


@pytest.mark.feature("FRM-01")
def test_list_fields_reports_every_type_name_rect_value(tmp_path: Path) -> None:
    path = _build_full_form(tmp_path)
    with Document.open(path) as doc:
        fields = _apply(doc, {"op": "page_fields", "page_index": 0})

    text = _field(fields, "name")  # type: ignore[arg-type]
    assert text["field_type"] == "text"
    assert text["rect"] == pytest.approx((50, 50, 250, 70))
    assert text["value"] == ""

    checkbox = _field(fields, "agree")  # type: ignore[arg-type]
    assert checkbox["field_type"] == "checkbox"
    assert checkbox["value"] is False

    dropdown = _field(fields, "color")  # type: ignore[arg-type]
    assert dropdown["field_type"] == "dropdown"
    assert dropdown["value"] == "Red"
    assert dropdown["options"] == ["Red", "Green", "Blue"]

    listbox = _field(fields, "fruit")  # type: ignore[arg-type]
    assert listbox["field_type"] == "listbox"
    assert listbox["options"] == ["Apple", "Pear", "Fig"]


@pytest.mark.feature("FRM-01", criterion=2)
def test_radio_group_is_one_logical_field_with_options(tmp_path: Path) -> None:
    path = _build_full_form(tmp_path)
    with Document.open(path) as doc:
        fields = _apply(doc, {"op": "page_fields", "page_index": 0})

    radios = [f for f in fields if f.name == "plan"]  # type: ignore[attr-defined]
    assert len(radios) == 1, "a radio group must be reported as one logical field, not one per button"
    radio = radios[0].model_dump()  # type: ignore[attr-defined]
    assert radio["field_type"] == "radio"
    assert sorted(radio["options"]) == ["Basic", "Pro"]
    assert radio["value"] is None  # nothing selected yet (/V is /Off)


@pytest.mark.feature("FRM-01", criterion=3)
def test_no_acroform_reports_empty_list_not_error(tmp_path: Path) -> None:
    path = _empty_doc(tmp_path)
    with Document.open(path) as doc:
        fields = _apply(doc, {"op": "page_fields", "page_index": 0})
    assert fields == []


# -- FRM-02 fill fields --


@pytest.mark.feature("FRM-02")
def test_fill_text_field_regenerates_appearance(tmp_path: Path) -> None:
    path = _build_full_form(tmp_path)
    with Document.open(path) as doc:
        _apply(doc, {"op": "fill_fields", "page_index": 0, "values": {"name": "Hello World"}})
        out = tmp_path / "saved.pdf"
        doc.save(out, overwrite=False)

    reopened = pymupdf.open(out)
    assert "Hello World" in reopened[0].get_text()
    fields = _apply(Document.open(out), {"op": "page_fields", "page_index": 0})
    assert _field(fields, "name")["value"] == "Hello World"  # type: ignore[arg-type]


@pytest.mark.feature("FRM-02", criterion=2)
def test_fill_checkbox_radio_and_dropdown(tmp_path: Path) -> None:
    path = _build_full_form(tmp_path)
    with Document.open(path) as doc:
        _apply(
            doc,
            {
                "op": "fill_fields",
                "page_index": 0,
                "values": {"agree": True, "color": "Blue", "plan": "Pro"},
            },
        )
        out = tmp_path / "saved.pdf"
        doc.save(out, overwrite=False)

    fields = _apply(Document.open(out), {"op": "page_fields", "page_index": 0})
    assert _field(fields, "agree")["value"] is True  # type: ignore[arg-type]
    assert _field(fields, "color")["value"] == "Blue"  # type: ignore[arg-type]
    assert _field(fields, "plan")["value"] == "Pro"  # type: ignore[arg-type]


@pytest.mark.feature("FRM-02", criterion=3)
def test_unknown_field_name_refuses_whole_call(tmp_path: Path) -> None:
    path = _build_full_form(tmp_path)
    with Document.open(path) as doc:
        with pytest.raises(OpValidationError, match="bogus"):
            _apply(
                doc,
                {"op": "fill_fields", "page_index": 0, "values": {"name": "ok", "bogus": "x", "also_bogus": "y"}},
            )
        # nothing changed, not even the valid field:
        fields = _apply(doc, {"op": "page_fields", "page_index": 0})
        assert _field(fields, "name")["value"] == ""  # type: ignore[arg-type]

    # the error names every unknown field, not just the first
    with Document.open(path) as doc2:
        try:
            _apply(doc2, {"op": "fill_fields", "page_index": 0, "values": {"bogus": "x", "also_bogus": "y"}})
            raise AssertionError("expected OpValidationError")
        except OpValidationError as exc:
            assert "bogus" in str(exc)
            assert "also_bogus" in str(exc)


@pytest.mark.feature("FRM-02", criterion=2)
def test_checkbox_only_accepts_bool_or_explicit_spellings(tmp_path: Path) -> None:
    # bool(raw_value) would make the string "false" truthy and check the box; the engine
    # must refuse a string that isn't one of the explicit true/false spellings instead.
    path = _build_full_form(tmp_path)
    with Document.open(path) as doc:
        with pytest.raises(OpValidationError, match="checkbox"):
            _apply(doc, {"op": "fill_fields", "page_index": 0, "values": {"agree": "maybe"}})
        # refused before anything changed
        fields = _apply(doc, {"op": "page_fields", "page_index": 0})
        assert _field(fields, "agree")["value"] is False  # type: ignore[arg-type]

    with Document.open(path) as doc2:
        _apply(doc2, {"op": "fill_fields", "page_index": 0, "values": {"agree": "false"}})
        fields2 = _apply(doc2, {"op": "page_fields", "page_index": 0})
        assert _field(fields2, "agree")["value"] is False  # type: ignore[arg-type]

    with Document.open(path) as doc3:
        _apply(doc3, {"op": "fill_fields", "page_index": 0, "values": {"agree": "TRUE"}})
        fields3 = _apply(doc3, {"op": "page_fields", "page_index": 0})
        assert _field(fields3, "agree")["value"] is True  # type: ignore[arg-type]


@pytest.mark.feature("FRM-02", criterion=2)
def test_text_value_needing_an_unsupported_glyph_is_refused_not_invisible(tmp_path: Path) -> None:
    # PyMuPDF's widget appearance regeneration is limited to four built-in fonts
    # (Cour/TiRo/Helv/ZaDb) with no way to attach a Unicode-coverage font through this API; an
    # emoji silently produces a zero-size (invisible) appearance instead of an error, so the
    # engine must detect that and refuse rather than "succeed" with nothing drawn.
    path = _build_full_form(tmp_path)
    with Document.open(path) as doc, pytest.raises(OpValidationError, match="name"):
        _apply(doc, {"op": "fill_fields", "page_index": 0, "values": {"name": "Hi \U0001f600"}})

    # a currency symbol within WinAnsiEncoding (the Helv field's own encoding) still works
    with Document.open(path) as doc2:
        _apply(doc2, {"op": "fill_fields", "page_index": 0, "values": {"name": "Price: €100"}})
        pixels = doc2.raw[0].get_pixmap(dpi=150, clip=pymupdf.Rect(50, 50, 250, 70)).samples
        assert any(b != 255 for b in pixels)  # something was actually drawn


@pytest.mark.feature("FRM-02", criterion=4)
def test_filling_several_fields_is_one_undo_step(tmp_path: Path) -> None:
    path = _build_full_form(tmp_path)
    journal = UndoRedoJournal(Document.open(path))
    values = {"name": "X", "agree": True, "color": "Green"}
    journal.record(parse_op({"op": "fill_fields", "page_index": 0, "values": values}))
    assert len(journal.history) == 1

    fields = journal.document.inspect()  # sanity: document still opens fine
    assert fields is not None

    journal.undo()
    reverted = _apply(journal.document, {"op": "page_fields", "page_index": 0})
    assert _field(reverted, "name")["value"] == ""  # type: ignore[arg-type]
    assert _field(reverted, "agree")["value"] is False  # type: ignore[arg-type]


# -- FRM-05 tab order --


@pytest.mark.feature("FRM-05")
def test_explicit_tab_order_is_reflected_in_saved_annots_order(tmp_path: Path) -> None:
    path = _build_full_form(tmp_path)
    with Document.open(path) as doc:
        _apply(doc, {"op": "set_tab_order", "page_index": 0, "field_names": ["fruit", "name", "agree"]})
        out = tmp_path / "saved.pdf"
        doc.save(out, overwrite=False)

    reopened = pymupdf.open(out)
    page = reopened[0]
    names_in_order = []
    seen = set()
    for widget in page.widgets():
        if widget.field_name not in seen:
            names_in_order.append(widget.field_name)
            seen.add(widget.field_name)
    assert names_in_order[:3] == ["fruit", "name", "agree"]
    assert reopened.xref_get_key(page.xref, "Tabs") == ("name", "/S")


@pytest.mark.feature("FRM-05", criterion=2)
def test_unlisted_field_is_placed_at_the_end(tmp_path: Path) -> None:
    path = _build_full_form(tmp_path)
    with Document.open(path) as doc:
        # "color" and "plan" are deliberately left out
        _apply(doc, {"op": "set_tab_order", "page_index": 0, "field_names": ["fruit", "name", "agree"]})
        out = tmp_path / "saved.pdf"
        doc.save(out, overwrite=False)

    reopened = pymupdf.open(out)
    names_in_order = []
    seen = set()
    for widget in reopened[0].widgets():
        if widget.field_name not in seen:
            names_in_order.append(widget.field_name)
            seen.add(widget.field_name)
    assert names_in_order.index("color") > names_in_order.index("agree")
    assert names_in_order.index("plan") > names_in_order.index("agree")


@pytest.mark.feature("FRM-05", criterion=3)
def test_order_round_trips_unchanged_when_nothing_reordered(tmp_path: Path) -> None:
    path = _build_full_form(tmp_path)
    with Document.open(path) as doc:
        before = _apply(doc, {"op": "page_fields", "page_index": 0})
        current_order = []
        seen = set()
        for field in before:  # type: ignore[attr-defined]
            if field.name not in seen:
                current_order.append(field.name)
                seen.add(field.name)
        _apply(doc, {"op": "set_tab_order", "page_index": 0, "field_names": current_order})
        after = _apply(doc, {"op": "page_fields", "page_index": 0})

    before_names = [f.name for f in before]  # type: ignore[attr-defined]
    after_names = [f.name for f in after]  # type: ignore[attr-defined]
    assert before_names == after_names


# -- FRM-06 flatten --


@pytest.mark.feature("FRM-06")
def test_flatten_whole_document_removes_acroform(tmp_path: Path) -> None:
    path = _build_full_form(tmp_path)
    with Document.open(path) as doc:
        _apply(doc, {"op": "fill_fields", "page_index": 0, "values": {"name": "Signed off", "agree": True}})
        result = _apply(doc, {"op": "flatten_form", "page_index": None})
        assert result.fields_flattened > 0  # type: ignore[attr-defined]
        out = tmp_path / "flat.pdf"
        doc.save(out, overwrite=False)

    reopened = pymupdf.open(out)
    assert list(reopened[0].widgets()) == []
    assert reopened.xref_get_key(reopened.pdf_catalog(), "AcroForm") == ("null", "null")
    assert "Signed off" in reopened[0].get_text()


@pytest.mark.feature("FRM-06", criterion=2)
def test_flatten_is_pixel_faithful_to_the_filled_field(tmp_path: Path) -> None:
    # Render at the same DPI engine.forms captures the widget at (_FLATTEN_CAPTURE_DPI), so
    # pasting the capture back introduces no resampling of its own: any remaining difference is
    # purely the antialiasing seam where the pasted image's edge meets the page (a handful of
    # edge pixels, not missing content).
    capture_dpi = 300
    path = _build_full_form(tmp_path)
    with Document.open(path) as doc:
        _apply(doc, {"op": "fill_fields", "page_index": 0, "values": {"agree": True}})
        before_pixels = doc.raw[0].get_pixmap(dpi=capture_dpi, clip=pymupdf.Rect(50, 90, 70, 110)).samples
        _apply(doc, {"op": "flatten_form", "page_index": 0})
        after_pixels = doc.raw[0].get_pixmap(dpi=capture_dpi, clip=pymupdf.Rect(50, 90, 70, 110)).samples

    # a checked box must still look checked after flattening (not blank/erased)
    assert before_pixels != b"\xff" * len(before_pixels)  # the checkmark drew *something* before
    diffs = sum(1 for a, b in zip(before_pixels, after_pixels, strict=True) if abs(a - b) > 10)
    assert diffs / len(before_pixels) < 0.02


@pytest.mark.feature("FRM-06", criterion=3)
def test_flatten_is_a_no_op_without_an_acroform(tmp_path: Path) -> None:
    # Document.snapshot() isn't byte-stable across two calls even with zero edits (each pymupdf
    # save writes a fresh random second trailer /ID half, see engine/document.py's
    # _pin_second_file_id), so compare the page's rendered content and widget/AcroForm state
    # instead of raw bytes.
    path = _empty_doc(tmp_path)
    with Document.open(path) as doc:
        before_text = doc.raw[0].get_text()
        before_pixels = doc.raw[0].get_pixmap(dpi=100).samples
        result = _apply(doc, {"op": "flatten_form", "page_index": None})
        after_text = doc.raw[0].get_text()
        after_pixels = doc.raw[0].get_pixmap(dpi=100).samples
    assert result.fields_flattened == 0  # type: ignore[attr-defined]
    assert before_text == after_text
    assert before_pixels == after_pixels


@pytest.mark.feature("FRM-06", criterion=4)
def test_flatten_is_undoable(tmp_path: Path) -> None:
    path = _build_full_form(tmp_path)
    journal = UndoRedoJournal(Document.open(path))
    journal.record(parse_op({"op": "flatten_form", "page_index": None}))
    assert list(journal.document.raw[0].widgets()) == []

    journal.undo()
    restored_fields = _apply(journal.document, {"op": "page_fields", "page_index": 0})
    names = {f.name for f in restored_fields}  # type: ignore[attr-defined]
    assert {"name", "agree", "color", "fruit", "plan"} <= names


# -- FRM-03 create / edit / delete fields --


@pytest.mark.feature("FRM-03")
def test_create_each_field_type_at_a_given_position_and_name(tmp_path: Path) -> None:
    path = _empty_doc(tmp_path)
    with Document.open(path) as doc:
        text = _apply(
            doc,
            {
                "op": "create_field",
                "page_index": 0,
                "field_type": "text",
                "name": "full_name",
                "rect": [50, 50, 250, 70],
            },
        )
        checkbox = _apply(
            doc,
            {
                "op": "create_field",
                "page_index": 0,
                "field_type": "checkbox",
                "name": "agree",
                "rect": [50, 90, 70, 110],
            },
        )
        dropdown = _apply(
            doc,
            {
                "op": "create_field",
                "page_index": 0,
                "field_type": "dropdown",
                "name": "color",
                "rect": [50, 130, 250, 150],
                "options": ["Red", "Green", "Blue"],
            },
        )
        radio = _apply(
            doc,
            {
                "op": "create_field",
                "page_index": 0,
                "field_type": "radio",
                "name": "plan",
                "options": ["Basic", "Pro"],
                "rects": [[50, 240, 65, 255], [90, 240, 105, 255]],
            },
        )
        signature = _apply(
            doc,
            {
                "op": "create_field",
                "page_index": 0,
                "field_type": "signature",
                "name": "sig1",
                "rect": [50, 300, 250, 340],
            },
        )

    assert text.field_type == "text" and text.rect == pytest.approx((50, 50, 250, 70))  # type: ignore[attr-defined]
    assert checkbox.field_type == "checkbox" and checkbox.value is False  # type: ignore[attr-defined]
    assert dropdown.field_type == "dropdown" and dropdown.options == ["Red", "Green", "Blue"]  # type: ignore[attr-defined]
    assert radio.field_type == "radio" and sorted(radio.options) == ["Basic", "Pro"]  # type: ignore[attr-defined]
    assert signature.field_type == "signature" and signature.value is None  # type: ignore[attr-defined]


@pytest.mark.feature("FRM-03", criterion=3)
def test_created_field_is_listed_and_fillable(tmp_path: Path) -> None:
    path = _empty_doc(tmp_path)
    with Document.open(path) as doc:
        _apply(
            doc,
            {
                "op": "create_field",
                "page_index": 0,
                "field_type": "text",
                "name": "full_name",
                "rect": [50, 50, 250, 70],
            },
        )
        _apply(
            doc,
            {
                "op": "create_field",
                "page_index": 0,
                "field_type": "radio",
                "name": "plan",
                "options": ["Basic", "Pro"],
                "rects": [[50, 240, 65, 255], [90, 240, 105, 255]],
            },
        )

        listed = _apply(doc, {"op": "page_fields", "page_index": 0})
        assert {f.name for f in listed} == {"full_name", "plan"}  # type: ignore[attr-defined]

        fill_result = _apply(
            doc, {"op": "fill_fields", "page_index": 0, "values": {"full_name": "Hello", "plan": "Pro"}}
        )
        assert fill_result.filled == ["full_name", "plan"]  # type: ignore[attr-defined]

        out = tmp_path / "created_and_filled.pdf"
        doc.save(out, overwrite=False)

    reopened = pymupdf.open(out)
    assert "Hello" in reopened[0].get_text()
    refilled = _apply(Document.open(out), {"op": "page_fields", "page_index": 0})
    assert _field(refilled, "plan")["value"] == "Pro"  # type: ignore[arg-type]


@pytest.mark.feature("FRM-03")
def test_duplicate_field_name_is_refused(tmp_path: Path) -> None:
    path = _build_full_form(tmp_path)
    with Document.open(path) as doc, pytest.raises(OpValidationError, match="name"):
        _apply(
            doc,
            {"op": "create_field", "page_index": 0, "field_type": "text", "name": "name", "rect": [10, 10, 100, 30]},
        )


# Regression: page.add_widget raises a bare ValueError("bad rect") for a zero-area or
# inverted rect -- not an OpValidationError, so server/app.py's error map (which only
# knows PdfWorkerzError subclasses) would have let it through as an uncaught 500. An
# out-of-page-bounds rect raised nothing at all, silently creating an unreachable field.
@pytest.mark.parametrize(
    "bad_rect",
    [
        pytest.param([100, 100, 100, 100], id="zero-area"),
        pytest.param([100, 100, 50, 50], id="inverted"),
        pytest.param([-50, -50, -10, -10], id="out-of-bounds-negative"),
        pytest.param([500, 800, 700, 900], id="out-of-bounds-beyond-page"),
    ],
)
@pytest.mark.feature("FRM-03")
def test_create_field_refuses_a_bad_rect_as_opvalidationerror(tmp_path: Path, bad_rect: list[float]) -> None:
    path = _empty_doc(tmp_path)
    with Document.open(path) as doc, pytest.raises(OpValidationError):
        _apply(doc, {"op": "create_field", "page_index": 0, "field_type": "text", "name": "f", "rect": bad_rect})


@pytest.mark.parametrize(
    "bad_rects",
    [
        pytest.param([[100, 100, 100, 100], [10, 10, 20, 20]], id="zero-area-first-option"),
        pytest.param([[10, 10, 20, 20], [100, 100, 50, 50]], id="inverted-second-option"),
        pytest.param([[10, 10, 20, 20], [-50, -50, -10, -10]], id="out-of-bounds-second-option"),
    ],
)
@pytest.mark.feature("FRM-03")
def test_create_radio_group_refuses_a_bad_rect_on_any_option(tmp_path: Path, bad_rects: list[list[float]]) -> None:
    path = _empty_doc(tmp_path)
    with Document.open(path) as doc:
        with pytest.raises(OpValidationError):
            _apply(
                doc,
                {
                    "op": "create_field",
                    "page_index": 0,
                    "field_type": "radio",
                    "name": "plan",
                    "options": ["A", "B"],
                    "rects": bad_rects,
                },
            )
        # every option's rect is validated before anything is written: nothing partial
        listed = _apply(doc, {"op": "page_fields", "page_index": 0})
        assert listed == []


@pytest.mark.feature("FRM-03")
def test_edit_field_rect_change_also_refuses_a_bad_rect(tmp_path: Path) -> None:
    path = _build_full_form(tmp_path)
    with Document.open(path) as doc, pytest.raises(OpValidationError):
        _apply(doc, {"op": "edit_field", "page_index": 0, "name": "name", "rect": [100, 100, 100, 100]})


@pytest.mark.feature("FRM-03", criterion=2)
def test_edit_dropdown_options(tmp_path: Path) -> None:
    path = _build_full_form(tmp_path)
    with Document.open(path) as doc:
        edited = _apply(doc, {"op": "edit_field", "page_index": 0, "name": "color", "options": ["X", "Y", "Z"]})
        assert edited.options == ["X", "Y", "Z"]  # type: ignore[attr-defined]
        assert (
            edited.value is None
        )  # the old selection ("Red") isn't one of the new options  # type: ignore[attr-defined]

        _apply(doc, {"op": "fill_fields", "page_index": 0, "values": {"color": "Y"}})
        listed = _apply(doc, {"op": "page_fields", "page_index": 0})
        assert _field(listed, "color")["value"] == "Y"  # type: ignore[arg-type]


@pytest.mark.feature("FRM-03", criterion=2)
def test_edit_dropdown_options_clears_a_dangling_value_on_disk_not_just_in_memory(tmp_path: Path) -> None:
    # Regression: widget.update() silently refuses to rewrite /V to empty for a choice
    # widget -- the in-memory FieldInfo read back correctly (None), which is all the
    # sibling test above checks, but a plain save/reopen used to still show the OLD /V
    # with no matching /Opt entry: a corrupted field that would have survived into
    # FRM-07 export and FRM-06 flatten. edit_field now clears /V with a raw xref write.
    path = _build_full_form(tmp_path)
    with Document.open(path) as doc:
        _apply(doc, {"op": "edit_field", "page_index": 0, "name": "color", "options": ["X", "Y", "Z"]})
        out = tmp_path / "dangling_value.pdf"
        doc.save(out, overwrite=False)

    pdf = pikepdf.open(out)
    field = next(f for f in pdf.Root.AcroForm.Fields if str(f.get("/T")) == "color")
    assert "/V" not in field, f"stale /V survived a save/reopen: {dict(field)!r}"
    assert [str(o) for o in field.Opt] == ["X", "Y", "Z"]

    # and the field is still cleanly fillable afterwards, from a fresh reopen
    with Document.open(out) as reopened:
        _apply(reopened, {"op": "fill_fields", "page_index": 0, "values": {"color": "Z"}})
        listed = _apply(reopened, {"op": "page_fields", "page_index": 0})
        assert _field(listed, "color")["value"] == "Z"  # type: ignore[arg-type]


@pytest.mark.feature("FRM-03", criterion=2)
def test_edit_text_font_size_and_multiline(tmp_path: Path) -> None:
    path = _build_full_form(tmp_path)
    with Document.open(path) as doc:
        _apply(
            doc, {"op": "edit_field", "page_index": 0, "name": "name", "font": "Helv", "size": 14, "multiline": True}
        )
        out = tmp_path / "edited.pdf"
        doc.save(out, overwrite=False)

    reopened = pymupdf.open(out)
    for widget in reopened[0].widgets():
        if widget.field_name == "name":
            assert widget.text_fontsize == pytest.approx(14)
            assert widget.field_flags & pymupdf.PDF_TX_FIELD_IS_MULTILINE


@pytest.mark.feature("FRM-03", criterion=2)
def test_edit_rejects_a_property_the_field_type_does_not_have(tmp_path: Path) -> None:
    path = _build_full_form(tmp_path)
    with Document.open(path) as doc, pytest.raises(OpValidationError, match="options"):
        _apply(doc, {"op": "edit_field", "page_index": 0, "name": "name", "options": ["a", "b"]})


@pytest.mark.feature("FRM-03", criterion=2)
def test_edit_radio_group_options_rebuilds_the_group(tmp_path: Path) -> None:
    path = _build_full_form(tmp_path)
    with Document.open(path) as doc:
        _apply(doc, {"op": "fill_fields", "page_index": 0, "values": {"plan": "Pro"}})
        edited = _apply(
            doc,
            {
                "op": "edit_field",
                "page_index": 0,
                "name": "plan",
                "options": ["Basic", "Pro", "Enterprise"],
                "rects": [[50, 240, 65, 255], [90, 240, 105, 255], [130, 240, 145, 255]],
            },
        )
        assert sorted(edited.options) == ["Basic", "Enterprise", "Pro"]  # type: ignore[attr-defined]
        assert (
            edited.value == "Pro"
        )  # the old selection is kept when it's still a valid option  # type: ignore[attr-defined]

        _apply(doc, {"op": "fill_fields", "page_index": 0, "values": {"plan": "Enterprise"}})
        listed = _apply(doc, {"op": "page_fields", "page_index": 0})
        assert _field(listed, "plan")["value"] == "Enterprise"  # type: ignore[arg-type]


@pytest.mark.feature("FRM-03", criterion=4)
def test_delete_field_removes_it_from_annots_and_acroform(tmp_path: Path) -> None:
    path = _build_full_form(tmp_path)
    with Document.open(path) as doc:
        _apply(doc, {"op": "delete_field", "page_index": 0, "name": "name"})
        listed = _apply(doc, {"op": "page_fields", "page_index": 0})
        assert "name" not in {f.name for f in listed}  # type: ignore[attr-defined]
        out = tmp_path / "deleted.pdf"
        doc.save(out, overwrite=False)

    reopened = pymupdf.open(out)
    assert "name" not in {w.field_name for w in reopened[0].widgets()}
    pdf = pikepdf.open(out)
    field_names = {str(f.get("/T")) for f in pdf.Root.AcroForm.Fields}
    assert "name" not in field_names


@pytest.mark.feature("FRM-03", criterion=4)
def test_delete_radio_group_removes_every_button_and_the_parent_field(tmp_path: Path) -> None:
    path = _build_full_form(tmp_path)
    with Document.open(path) as doc:
        _apply(doc, {"op": "delete_field", "page_index": 0, "name": "plan"})
        listed = _apply(doc, {"op": "page_fields", "page_index": 0})
        assert "plan" not in {f.name for f in listed}  # type: ignore[attr-defined]
        out = tmp_path / "deleted_radio.pdf"
        doc.save(out, overwrite=False)

    reopened = pymupdf.open(out)
    assert "plan" not in {w.field_name for w in reopened[0].widgets()}
    pdf = pikepdf.open(out)
    field_names = {str(f.get("/T")) for f in pdf.Root.AcroForm.Fields}
    assert "plan" not in field_names


@pytest.mark.feature("FRM-03")
def test_delete_unknown_field_name_is_refused(tmp_path: Path) -> None:
    path = _build_full_form(tmp_path)
    with Document.open(path) as doc, pytest.raises(OpValidationError, match="bogus"):
        _apply(doc, {"op": "delete_field", "page_index": 0, "name": "bogus"})


# -- FRM-08 XFA detection --


def _inject_xfa(path: Path) -> None:
    """Add an /XFA entry to an already-saved PDF's /AcroForm (pikepdf, since pymupdf
    has no API for this -- engine.forms.detect_xfa reads it back with pikepdf too)."""
    pdf = pikepdf.open(path, allow_overwriting_input=True)
    xfa_stream = pdf.make_stream(b"<xdp:xdp xmlns:xdp='http://ns.adobe.com/xdp/'></xdp:xdp>")
    pdf.Root.AcroForm.XFA = xfa_stream
    pdf.save(path)


@pytest.mark.feature("FRM-08")
def test_xfa_is_detected_distinctly_from_an_ordinary_acroform(tmp_path: Path) -> None:
    path = _build_full_form(tmp_path)
    _inject_xfa(path)
    with Document.open(path) as doc:
        report = _apply(doc, {"op": "detect_xfa"})
    assert report.has_xfa is True  # type: ignore[attr-defined]
    assert report.warning is not None  # type: ignore[attr-defined]
    assert "XFA" in report.warning  # type: ignore[attr-defined]


@pytest.mark.feature("FRM-08", criterion=2)
def test_static_fields_alongside_xfa_are_still_listed_and_fillable(tmp_path: Path) -> None:
    path = _build_full_form(tmp_path)
    _inject_xfa(path)
    with Document.open(path) as doc:
        report = _apply(doc, {"op": "detect_xfa"})
        assert report.has_static_fields is True  # type: ignore[attr-defined]

        listed = _apply(doc, {"op": "page_fields", "page_index": 0})
        assert {"name", "agree", "color", "fruit", "plan"} <= {f.name for f in listed}  # type: ignore[attr-defined]

        _apply(doc, {"op": "fill_fields", "page_index": 0, "values": {"name": "Still works"}})
        out = tmp_path / "xfa_filled.pdf"
        doc.save(out, overwrite=False)

    reopened = pymupdf.open(out)
    assert "Still works" in reopened[0].get_text()


@pytest.mark.feature("FRM-08", criterion=3)
def test_opening_an_xfa_document_surfaces_a_clear_warning(tmp_path: Path) -> None:
    path = _build_full_form(tmp_path)
    _inject_xfa(path)
    with Document.open(path) as doc:
        report = _apply(doc, {"op": "detect_xfa"})
    assert report.warning is not None and "xfa" in report.warning.lower()  # type: ignore[attr-defined]


@pytest.mark.feature("FRM-08", criterion=4)
def test_a_document_with_no_xfa_is_never_falsely_flagged(tmp_path: Path) -> None:
    path = _build_full_form(tmp_path)
    with Document.open(path) as doc:
        report = _apply(doc, {"op": "detect_xfa"})
    assert report.has_xfa is False  # type: ignore[attr-defined]
    assert report.warning is None  # type: ignore[attr-defined]

    empty_path = _empty_doc(tmp_path)
    with Document.open(empty_path) as doc2:
        report2 = _apply(doc2, {"op": "detect_xfa"})
    assert report2.has_xfa is False  # type: ignore[attr-defined]
    assert report2.has_static_fields is False  # type: ignore[attr-defined]
