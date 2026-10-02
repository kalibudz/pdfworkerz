"""CVF-04 / CVF-08: pages to images, and extracting the pictures inside a PDF."""

from __future__ import annotations

import io
import math
import zipfile

import pymupdf
import pytest
from PIL import Image

from engine.document import Document
from engine.errors import OpValidationError
from engine.export_images import extract_embedded_images, pages_to_images, zip_files
from tests import pdfs


def _jpeg(size: tuple[int, int] = (240, 160), colour: tuple[int, int, int] = (30, 90, 200)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, format="JPEG", quality=77)
    return buffer.getvalue()


def _three_pages() -> Document:
    raw = pymupdf.open()
    for number in range(3):
        raw.new_page(width=400, height=300).insert_text((50, 100), f"Page {number + 1}", fontsize=20)
    return Document.from_bytes(raw.tobytes())


@pytest.mark.feature("CVF-04", criterion=1)
@pytest.mark.parametrize(("fmt", "magic"), [("png", b"\x89PNG"), ("jpg", b"\xff\xd8\xff"), ("tiff", b"II*\x00")])
def test_pages_export_in_each_format_one_file_per_page(fmt: str, magic: bytes) -> None:
    files = pages_to_images(_three_pages(), fmt=fmt, dpi=72)  # type: ignore[arg-type]
    assert [f.name for f in files] == [f"page-{n}.{fmt}" for n in (1, 2, 3)]
    assert all(f.data.startswith(magic) for f in files)
    assert all(Image.open(io.BytesIO(f.data)).size == (400, 300) for f in files)


@pytest.mark.feature("CVF-04", criterion=2)
def test_a_page_range_limits_the_export_and_a_bad_page_is_refused() -> None:
    document = _three_pages()
    assert [f.name for f in pages_to_images(document, [2, 0], dpi=72)] == ["page-3.png", "page-1.png"]
    with pytest.raises(OpValidationError, match="page 4 is outside"):
        pages_to_images(document, [3])
    with pytest.raises(OpValidationError, match="dpi"):
        pages_to_images(document, dpi=5000)


@pytest.mark.feature("CVF-04", criterion=3)
def test_a_multi_page_tiff_is_one_file() -> None:
    [tiff] = pages_to_images(_three_pages(), fmt="tiff", dpi=72, single_tiff=True)
    picture = Image.open(io.BytesIO(tiff.data))
    assert picture.n_frames == 3
    with pytest.raises(OpValidationError, match="only possible as TIFF"):
        pages_to_images(_three_pages(), fmt="png", single_tiff=True)


@pytest.mark.feature("CVF-04", criterion=4)
@pytest.mark.parametrize("dpi", [72, 150, 300])
def test_the_pixel_size_follows_the_resolution(dpi: int) -> None:
    [file] = pages_to_images(_three_pages(), [0], dpi=dpi)
    assert Image.open(io.BytesIO(file.data)).size == (
        math.ceil(400 * dpi / 72),
        math.ceil(300 * dpi / 72),
    )  # a partly covered edge pixel counts


def _with_pictures() -> tuple[Document, bytes]:
    photo = _jpeg()
    raw = pymupdf.open()
    raw.new_page()
    raw.new_page()
    first, second = raw[0], raw[1]
    xref = first.insert_image(pymupdf.Rect(50, 50, 290, 210), stream=photo)
    second.insert_image(pymupdf.Rect(50, 50, 170, 130), xref=xref)  # the same picture again on another page
    first.insert_image(pymupdf.Rect(300, 50, 308, 58), stream=_jpeg((8, 8), (200, 0, 0)))  # a speck
    return Document.from_bytes(raw.tobytes()), photo


@pytest.mark.feature("CVF-08", criterion=1)
def test_a_jpeg_comes_out_as_the_same_jpeg_bytes() -> None:
    document, photo = _with_pictures()
    [image] = extract_embedded_images(document)
    assert image.file.name.endswith(".jpeg") or image.file.name.endswith(".jpg")
    assert image.file.data == photo
    assert (image.width, image.height) == (240, 160)


@pytest.mark.feature("CVF-08", criterion=2)
def test_a_picture_used_on_two_pages_comes_out_once_with_both_pages_listed() -> None:
    document, _ = _with_pictures()
    [image] = extract_embedded_images(document)
    assert image.pages == [0, 1]


@pytest.mark.feature("CVF-08", criterion=3)
def test_tiny_pictures_are_left_out_unless_the_threshold_is_lowered() -> None:
    document, _ = _with_pictures()
    assert len(extract_embedded_images(document)) == 1
    assert len(extract_embedded_images(document, min_size=4)) == 2


@pytest.mark.feature("CVF-08", criterion=4)
def test_a_page_range_limits_what_is_extracted() -> None:
    document, _ = _with_pictures()
    [image] = extract_embedded_images(document, [1])
    assert image.pages == [1]
    assert extract_embedded_images(Document.from_bytes(pdfs.table_pdf())) == []


def test_many_files_zip_up_under_their_own_names() -> None:
    document, _ = _with_pictures()
    archive = zip_files([image.file for image in extract_embedded_images(document, min_size=4)])
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        assert len(zipped.namelist()) == 2
