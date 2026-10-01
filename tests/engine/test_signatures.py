"""SIG-01: visual signatures (drawn, typed, image), placed as ordinary page content."""

from __future__ import annotations

import base64
import io
from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from engine.document import Document
from engine.errors import OpValidationError
from engine.ops.signatures import PlaceSignatureOp
from engine.signatures import place_signature

RECT = (72.0, 72.0, 300.0, 180.0)


@pytest.fixture
def doc(work_dir: Path) -> Document:
    raw = pymupdf.open()
    raw.new_page()
    raw.new_page()
    path = work_dir / "sig.pdf"
    raw.save(path)
    raw.close()
    return Document.open(path)


def _png_bytes(width: int, height: int, color: tuple[int, int, int] = (20, 30, 200)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), color).save(out, format="PNG")
    return out.getvalue()


def _has_non_white_pixel(document: Document, page_index: int, rect: tuple[float, float, float, float]) -> bool:
    pix = document.raw[page_index].get_pixmap(dpi=72)
    x0, y0, x1, y1 = (int(v) for v in rect)
    for x in range(max(x0, 0), min(x1, pix.width), 2):
        for y in range(max(y0, 0), min(y1, pix.height), 2):
            if tuple(pix.pixel(x, y)) != (255, 255, 255):
                return True
    return False


@pytest.mark.feature("SIG-01", criterion=1)
def test_drawn_signature_renders_visibly_inside_rect(doc: Document) -> None:
    strokes = [[(0.0, 0.0), (10.0, 20.0), (40.0, 5.0), (60.0, 25.0)], [(5.0, 30.0), (55.0, 30.0)]]
    result = place_signature(doc, 0, RECT, kind="drawn", strokes=strokes)
    assert result.kind == "drawn"
    assert result.page_index == 0
    assert _has_non_white_pixel(doc, 0, result.rect)


@pytest.mark.feature("SIG-01", criterion=1)
def test_typed_signature_renders_visibly_inside_rect(doc: Document) -> None:
    result = place_signature(doc, 0, RECT, kind="typed", text="Jane Doe")
    assert result.kind == "typed"
    assert _has_non_white_pixel(doc, 0, result.rect)


@pytest.mark.feature("SIG-01", criterion=1)
def test_image_signature_renders_visibly_inside_rect(doc: Document) -> None:
    result = place_signature(doc, 0, RECT, kind="image", image_bytes=_png_bytes(80, 40))
    assert result.kind == "image"
    assert _has_non_white_pixel(doc, 0, result.rect)


@pytest.mark.feature("SIG-01", criterion=1)
def test_image_signature_keeps_aspect_ratio_centered(doc: Document) -> None:
    # A 2:1 image in a square rect should not stretch to fill the whole square --
    # it's centered and letterboxed, like engine.images.insert_image's own _fit.
    wide_rect = (72.0, 72.0, 272.0, 272.0)  # 200x200 square
    result = place_signature(doc, 0, wide_rect, kind="image", image_bytes=_png_bytes(200, 100))
    placed = pymupdf.Rect(result.rect)
    assert placed.width == pytest.approx(200.0)
    assert placed.height == pytest.approx(100.0)  # half the square's height: aspect kept, not stretched


@pytest.mark.feature("SIG-01", criterion=1)
def test_unknown_kind_is_refused(doc: Document) -> None:
    with pytest.raises(OpValidationError):
        place_signature(doc, 0, RECT, kind="scribble")  # type: ignore[arg-type]


@pytest.mark.feature("SIG-01", criterion=1)
def test_mismatched_payload_is_refused(doc: Document) -> None:
    with pytest.raises(OpValidationError):
        place_signature(doc, 0, RECT, kind="typed", strokes=[[(0.0, 0.0), (1.0, 1.0)]])
    with pytest.raises(OpValidationError):
        place_signature(doc, 0, RECT, kind="drawn")  # nothing given at all
    with pytest.raises(OpValidationError):
        # both text and image_bytes given alongside kind="typed" -- ambiguous, refused
        place_signature(doc, 0, RECT, kind="typed", text="Jane", image_bytes=_png_bytes(10, 10))


@pytest.mark.feature("SIG-01", criterion=2)
def test_placed_signature_is_normal_content_not_a_form_field(doc: Document) -> None:
    place_signature(doc, 0, RECT, kind="typed", text="Jane Doe")
    place_signature(doc, 0, (72.0, 200.0, 300.0, 260.0), kind="drawn", strokes=[[(0.0, 0.0), (20.0, 20.0)]])
    assert list(doc.raw[0].widgets()) == []  # no AcroForm field was created
    assert doc.raw.is_form_pdf is False


@pytest.mark.feature("SIG-01", criterion=2)
def test_placed_signature_survives_a_save(work_dir: Path, doc: Document) -> None:
    place_signature(doc, 0, RECT, kind="typed", text="Jane Doe")
    out = work_dir / "saved.pdf"
    doc.save(out, overwrite=True)
    reopened = Document.open(out)
    try:
        assert _has_non_white_pixel(reopened, 0, RECT)
        assert list(reopened.raw[0].widgets()) == []
    finally:
        reopened.close()


@pytest.mark.feature("SIG-01", criterion=3)
def test_same_signature_definition_is_reused_on_another_page(doc: Document) -> None:
    """The caller places the exact same `kind` + payload again, on a different
    page, without the person re-drawing/re-typing/re-uploading it -- see
    engine.signatures' module docstring for which interpretation of "reused
    without redrawing it from scratch" this satisfies."""
    strokes = [[(0.0, 0.0), (15.0, 10.0), (30.0, 0.0)]]
    first = place_signature(doc, 0, RECT, kind="drawn", strokes=strokes)
    second = place_signature(doc, 1, RECT, kind="drawn", strokes=strokes)
    assert first.kind == second.kind == "drawn"
    assert _has_non_white_pixel(doc, 0, first.rect)
    assert _has_non_white_pixel(doc, 1, second.rect)


@pytest.mark.feature("SIG-01", criterion=3)
def test_place_signature_op_round_trips_through_parse_op(doc: Document) -> None:
    """The Op (what the UI/CLI/recipes actually send) carries the same
    strokes/text/image_base64 payload a caller would keep client-side to
    place the signature again elsewhere."""
    from engine.ops.base import parse_op

    payload = {
        "op": "place_signature",
        "page_index": 0,
        "rect": list(RECT),
        "kind": "image",
        "image_base64": base64.b64encode(_png_bytes(20, 20)).decode("ascii"),
    }
    op = parse_op(payload)
    assert isinstance(op, PlaceSignatureOp)
    result = op.apply(doc)
    assert result.kind == "image"
    assert _has_non_white_pixel(doc, 0, result.rect)

    # Reused on another page: same op fields, different page_index/rect.
    payload["page_index"] = 1
    payload["rect"] = [72.0, 72.0, 172.0, 172.0]
    op2 = parse_op(payload)
    result2 = op2.apply(doc)
    assert _has_non_white_pixel(doc, 1, result2.rect)


@pytest.mark.feature("SIG-01", criterion=1)
def test_rect_entirely_outside_page_is_refused(doc: Document) -> None:
    with pytest.raises(OpValidationError):
        place_signature(doc, 0, (-5000.0, -5000.0, -4900.0, -4900.0), kind="typed", text="x")


@pytest.mark.feature("SIG-01", criterion=1)
def test_place_signature_op_validates_page_index(doc: Document) -> None:
    op = PlaceSignatureOp(page_index=99, rect=RECT, kind="typed", text="Jane Doe")
    with pytest.raises(OpValidationError):
        op.check_pages(doc)
