"""EDT-08: insert, replace, move/resize, crop and delete images."""

from __future__ import annotations

import base64
import io
from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from engine.document import Document
from engine.errors import OpValidationError
from engine.fonts.style import extract_page_spans
from engine.ops.base import parse_op
from engine.ops.images import (
    CropImageOp,
    DeleteImageOp,
    InsertImageOp,
    MoveImageOp,
    PageImagesOp,
    ReplaceImageOp,
)


def _png(width: int, height: int, color: tuple[int, int, int], *, fmt: str = "PNG") -> bytes:
    """A solid image; `left_right` halves get distinguishable colors in _two_tone."""
    out = io.BytesIO()
    Image.new("RGB", (width, height), color).save(out, format=fmt)
    return out.getvalue()


def _two_tone(width: int, height: int) -> bytes:
    """Left half red, right half blue -- a crop's result is checkable by color."""
    image = Image.new("RGB", (width, height), (255, 0, 0))
    image.paste((0, 0, 255), (width // 2, 0, width, height))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


_TINY = _b64(_png(2, 2, (0, 0, 0)))


@pytest.fixture
def doc(work_dir: Path) -> Document:
    """One page: the same image shown twice (two placements, one xref), a
    second, different image, and a line of text that must survive."""
    raw = pymupdf.open()
    page = raw.new_page()
    xref = page.insert_image(pymupdf.Rect(50, 50, 250, 200), stream=_png(40, 30, (0, 160, 0)))
    page.insert_image(pymupdf.Rect(300, 50, 500, 200), xref=xref)
    page.insert_image(pymupdf.Rect(50, 400, 150, 500), stream=_two_tone(100, 100))
    page.insert_text((60, 300), "Caption stays put", fontsize=12)
    path = work_dir / "images.pdf"
    raw.save(path)
    raw.close()
    return Document.open(path)


def _rects(doc: Document) -> list[tuple[float, ...]]:
    return [tuple(round(v) for v in info.rect) for info in PageImagesOp(page_index=0).apply(doc)]


def _text(doc: Document) -> str:
    return "".join(span.style.text for span in extract_page_spans(doc.raw, 0))


def _pixel(doc: Document, x: float, y: float) -> tuple[int, ...]:
    pix = doc.raw[0].get_pixmap(dpi=72)
    return tuple(pix.pixel(int(x), int(y)))


@pytest.mark.feature("EDT-08")
def test_list_reports_every_placement_including_a_shared_image(doc: Document) -> None:
    infos = PageImagesOp(page_index=0).apply(doc)
    assert len(infos) == 3
    assert infos[0].xref == infos[1].xref  # one image object, drawn twice
    assert (infos[0].pixel_width, infos[0].pixel_height) == (40, 30)
    assert all(info.axis_aligned for info in infos)


@pytest.mark.feature("EDT-08")
def test_insert_places_a_new_image_and_it_survives_a_save(doc: Document) -> None:
    added = InsertImageOp(page_index=0, rect=(300, 400, 400, 500), image_base64=_b64(_png(10, 10, (0, 0, 0)))).apply(
        doc
    )
    assert tuple(round(v) for v in added.rect) == (300, 400, 400, 500)
    reopened = Document.from_bytes(doc.to_bytes())
    assert len(PageImagesOp(page_index=0).apply(reopened)) == 4


@pytest.mark.feature("EDT-08")
def test_insert_keeps_proportion_by_default(doc: Document) -> None:
    added = InsertImageOp(page_index=0, rect=(300, 400, 500, 500), image_base64=_b64(_png(10, 10, (0, 0, 0)))).apply(
        doc
    )
    width, height = added.rect[2] - added.rect[0], added.rect[3] - added.rect[1]
    assert width == pytest.approx(height)  # square image, fitted inside a 200x100 area


@pytest.mark.feature("EDT-08")
def test_non_png_input_is_reencoded(doc: Document) -> None:
    InsertImageOp(page_index=0, rect=(300, 400, 400, 500), image_base64=_b64(_png(10, 10, (9, 9, 9), fmt="BMP"))).apply(
        doc
    )
    assert len(_rects(doc)) == 4


@pytest.mark.feature("EDT-08")
def test_delete_removes_only_that_placement_of_a_shared_image(doc: Document) -> None:
    DeleteImageOp(page_index=0, index=0).apply(doc)
    assert _rects(doc) == [(300, 50, 500, 200), (50, 400, 150, 500)]
    assert "Caption stays put" in _text(doc)


@pytest.mark.feature("EDT-08")
def test_delete_of_an_overlapped_image_leaves_its_neighbour(work_dir: Path) -> None:
    raw = pymupdf.open()
    page = raw.new_page()
    page.insert_image(pymupdf.Rect(50, 50, 250, 250), stream=_png(20, 20, (255, 0, 0)))
    page.insert_image(pymupdf.Rect(150, 150, 350, 350), stream=_png(20, 20, (0, 0, 255)))
    path = work_dir / "overlap.pdf"
    raw.save(path)
    doc = Document.open(path)

    DeleteImageOp(page_index=0, index=1).apply(doc)
    assert _rects(doc) == [(50, 50, 250, 250)]


@pytest.mark.feature("EDT-08")
def test_an_image_entirely_covered_by_another_is_refused(work_dir: Path) -> None:
    raw = pymupdf.open()
    page = raw.new_page()
    page.insert_image(pymupdf.Rect(100, 100, 150, 150), stream=_png(20, 20, (255, 0, 0)))
    page.insert_image(pymupdf.Rect(50, 50, 250, 250), stream=_png(20, 20, (0, 0, 255)))
    path = work_dir / "covered.pdf"
    raw.save(path)
    doc = Document.open(path)
    with pytest.raises(OpValidationError, match="entirely covered"):
        DeleteImageOp(page_index=0, index=0).apply(doc)
    assert len(_rects(doc)) == 2


@pytest.mark.feature("EDT-08")
def test_move_and_resize_redraws_the_same_image_object(doc: Document) -> None:
    xref = PageImagesOp(page_index=0).apply(doc)[0].xref
    moved = MoveImageOp(page_index=0, index=0, rect=(300, 600, 400, 700)).apply(doc)
    assert moved.xref == xref
    assert sorted(_rects(doc)) == sorted([(300, 50, 500, 200), (50, 400, 150, 500), (300, 600, 400, 700)])
    assert _pixel(doc, 350, 650) == (0, 160, 0)


@pytest.mark.feature("EDT-08")
def test_replace_changes_only_that_placement(doc: Document) -> None:
    ReplaceImageOp(page_index=0, index=0, image_base64=_b64(_png(40, 30, (255, 255, 0)))).apply(doc)
    assert _pixel(doc, 150, 125) == (255, 255, 0)
    assert _pixel(doc, 400, 125) == (0, 160, 0)  # the other placement of the old image


@pytest.mark.feature("EDT-08")
def test_crop_keeps_only_the_selected_part_in_place(doc: Document) -> None:
    # The two-tone image spans x 50-150: red on the left half, blue on the right.
    cropped = CropImageOp(page_index=0, index=2, rect=(100, 400, 150, 500)).apply(doc)
    assert tuple(round(v) for v in cropped.rect) == (100, 400, 150, 500)
    assert cropped.pixel_width == 50  # really cut, not just clipped
    assert _pixel(doc, 125, 450)[2] > 200  # blue half kept
    assert _pixel(doc, 75, 450) == (255, 255, 255)  # red half gone


@pytest.mark.feature("EDT-08")
@pytest.mark.parametrize(
    ("op", "message"),
    [
        (InsertImageOp(page_index=0, rect=(0, 0, 10, 10), image_base64="not base64!"), "base64"),
        (InsertImageOp(page_index=0, rect=(0, 0, 10, 10), image_base64=_b64(b"plain text")), "could not be read"),
        (InsertImageOp(page_index=0, rect=(10, 10, 0, 0), image_base64=_TINY), "empty"),
        (
            InsertImageOp(page_index=0, rect=(5000, 5000, 5100, 5100), image_base64=_b64(_png(2, 2, (0, 0, 0)))),
            "outside",
        ),
        (DeleteImageOp(page_index=0, index=9), "out of range"),
        (MoveImageOp(page_index=3, index=0, rect=(0, 0, 10, 10)), "out of range"),
        (CropImageOp(page_index=0, index=0, rect=(600, 600, 700, 700)), "doesn't overlap"),
    ],
)
def test_invalid_image_ops_are_rejected(doc: Document, op: InsertImageOp, message: str) -> None:
    before = _rects(doc)
    with pytest.raises(OpValidationError, match=message):
        op.apply(doc)
    assert _rects(doc) == before


@pytest.mark.feature("EDT-08")
def test_image_ops_round_trip_through_json() -> None:
    for op in (
        InsertImageOp(page_index=0, rect=(1, 2, 3, 4), image_base64="QUJD"),
        ReplaceImageOp(page_index=0, index=1, image_base64="QUJD"),
        MoveImageOp(page_index=0, index=1, rect=(1, 2, 3, 4)),
        CropImageOp(page_index=0, index=1, rect=(1, 2, 3, 4)),
        DeleteImageOp(page_index=0, index=1),
        PageImagesOp(page_index=2),
    ):
        assert parse_op(op.model_dump()) == op
