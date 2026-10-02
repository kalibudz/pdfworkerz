"""OCR-04: scan to PDF -- photos of paper to clean, flattened pages."""

from __future__ import annotations

import io

import cv2
import numpy as np
import pymupdf
import pytest
from PIL import Image

from engine.document import Document
from engine.errors import ConversionError
from engine.external import find_tool
from engine.rasters import NamedFile
from engine.scan import apply_mode, find_page_outline, flatten, page_box, scan_to_pdf
from tests import scans

needs_tesseract = pytest.mark.skipif(find_tool("tesseract") is None, reason="Tesseract is not installed")


@pytest.fixture(scope="module")
def photo() -> np.ndarray:
    return scans.photo_of_page(scans.text_page_png(dpi=200), canvas=(2000, 2500))


def _file(image: np.ndarray, name: str = "photo.png") -> NamedFile:
    return NamedFile(name, scans.encode(image))


def _page_image(pdf: bytes, index: int = 0) -> np.ndarray:
    doc = pymupdf.open("pdf", pdf)
    info = doc[index].get_images()[0]
    return cv2.imdecode(np.frombuffer(doc.extract_image(info[0])["image"], np.uint8), cv2.IMREAD_UNCHANGED)


@pytest.mark.feature("OCR-04", criterion=1)
def test_the_page_is_found_and_flattened_into_an_upright_rectangle(photo: np.ndarray) -> None:
    corners = find_page_outline(photo)
    assert corners is not None
    assert corners.shape == (4, 2)
    flat = flatten(photo, corners)
    height, width = flat.shape[:2]
    assert abs(width / height - 612 / 792) < 0.06, "the page's own proportions come back"
    # nothing but paper and print at the edges: the dark desk is gone
    assert flat[5:-5, 5:-5].mean() > 200
    assert np.mean(flat[: height // 40, :]) > 180


@needs_tesseract
@pytest.mark.feature("OCR-04", criterion=1)
def test_the_flattened_page_can_be_read() -> None:
    result = scan_to_pdf(
        [_file(scans.photo_of_page(scans.text_page_png(dpi=200), canvas=(2000, 2500)))], ocr_language="eng"
    )
    text = " ".join(pymupdf.open("pdf", result.pdf)[0].get_text("text").split())
    assert "Northwind" in text and "20481" in text


@pytest.mark.feature("OCR-04", criterion=2)
def test_each_cleanup_mode_gives_the_output_it_names(photo: np.ndarray) -> None:
    flat = flatten(photo, find_page_outline(photo))  # type: ignore[arg-type]
    assert apply_mode(flat, "original") is flat
    colour, gray, bw = apply_mode(flat, "color"), apply_mode(flat, "gray"), apply_mode(flat, "bw")
    assert colour.ndim == 3 and gray.ndim == 2 and bw.ndim == 2
    assert set(np.unique(bw)) <= {0, 255}
    assert np.median(gray) > np.median(cv2.cvtColor(flat, cv2.COLOR_BGR2GRAY)) - 1, "the paper comes out white"
    pdf = scan_to_pdf([_file(photo)], mode="bw").pdf
    stored = pymupdf.open("pdf", pdf)
    assert stored.extract_image(stored[0].get_images()[0][0])["ext"] == "png"  # 1-bit: the small lossless form


@pytest.mark.feature("OCR-04", criterion=3)
def test_several_photos_make_one_pdf_sized_as_asked(photo: np.ndarray) -> None:
    files = [_file(photo, "one.png"), _file(photo, "two.png"), _file(photo, "three.png")]
    fitted = pymupdf.open("pdf", scan_to_pdf(files).pdf)
    assert fitted.page_count == 3
    a4 = pymupdf.open("pdf", scan_to_pdf(files, paper="a4", margin=18).pdf)
    assert a4.page_count == 3
    page = a4[0]
    assert (round(page.rect.width), round(page.rect.height)) == (595, 842)
    shown = pymupdf.Rect(page.get_image_info()[0]["bbox"])
    assert shown.x0 >= 18 - 0.5 and shown.y0 >= 18 - 0.5 and shown.x1 <= 595.3 - 18 + 0.5
    assert abs(shown.width / shown.height - 612 / 792) < 0.06, "the picture is not stretched to the paper"
    assert abs((shown.x0 + shown.x1) / 2 - 297.6) < 1, "and sits in the middle"


@pytest.mark.feature("OCR-04", criterion=3)
def test_a_landscape_picture_gets_a_landscape_page() -> None:
    page, shown = page_box((2000, 1000), "letter", 10)
    assert (page.width, page.height) == (792, 612)
    assert shown.width / shown.height == pytest.approx(2.0)


@needs_tesseract
@pytest.mark.feature("OCR-04", criterion=4)
def test_the_result_can_be_made_searchable(photo: np.ndarray) -> None:
    plain = scan_to_pdf([_file(photo)])
    assert plain.ocr is None
    assert not pymupdf.open("pdf", plain.pdf)[0].get_text("text").strip()
    searchable = scan_to_pdf([_file(photo)], ocr_language="eng")
    assert searchable.ocr is not None and searchable.ocr.words > 5
    assert pymupdf.open("pdf", searchable.pdf)[0].get_text("text").strip()


@pytest.mark.feature("OCR-04", criterion=5)
def test_a_photo_with_no_outline_is_used_whole_and_says_so() -> None:
    desk = np.full((900, 1200, 3), (40, 55, 70), np.uint8)
    cv2.circle(desk, (600, 450), 330, (235, 235, 235), -1)  # a round white thing: no sheet of paper to find
    result = scan_to_pdf([_file(desk, "round.png")])
    assert result.pages[0].outline_found is False
    assert "whole photo" in result.pages[0].note
    assert Document.from_bytes(result.pdf).page_count == 1


@pytest.mark.feature("OCR-04", criterion=5)
def test_an_unreadable_photo_is_refused_by_name_before_anything_is_made(photo: np.ndarray) -> None:
    with pytest.raises(ConversionError, match=r"notes\.txt is not a picture"):
        scan_to_pdf([_file(photo), NamedFile("notes.txt", b"just words")])
    with pytest.raises(ConversionError, match="no photos"):
        scan_to_pdf([])


def test_a_sideways_phone_photo_is_stood_upright_by_its_exif_note() -> None:
    from engine.rasters import open_picture

    upright = Image.open(io.BytesIO(scans.text_page_png(dpi=60)))
    sideways = upright.transpose(Image.Transpose.ROTATE_90)  # stored turned; EXIF says "rotate 270 to view"
    exif = Image.Exif()
    exif[0x0112] = 8
    buffer = io.BytesIO()
    sideways.convert("RGB").save(buffer, format="JPEG", exif=exif)
    picture = open_picture(NamedFile("phone.jpg", buffer.getvalue()))
    assert picture.size == upright.size
