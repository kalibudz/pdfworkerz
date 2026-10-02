"""Synthetic scans for the P7 tests: a page of known text rendered to pixels, then made to look
like what a scanner or a phone produces (skewed, noisy, sideways, photographed at an angle).
Everything is generated here, so a test knows exactly what the text says and how it was damaged."""

from __future__ import annotations

import cv2
import numpy as np
import pymupdf

PAGE_W, PAGE_H = 612.0, 792.0  # US Letter, in points
SAMPLE_LINES = (
    "Invoice Number 20481",
    "Customer: Northwind Traders",
    "Payment is due within thirty days.",
    "Total amount payable 1,250.00",
)


def text_page_png(
    lines: tuple[str, ...] = SAMPLE_LINES,
    *,
    dpi: int = 200,
    fontname: str = "helv",
    fontsize: float = 16,
    color: tuple[float, float, float] = (0, 0, 0),
) -> bytes:
    """A letter page with ``lines`` of text, rendered to a PNG."""
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    for number, line in enumerate(lines):
        page.insert_text((72, 100 + number * 40), line, fontsize=fontsize, fontname=fontname, color=color)
    return page.get_pixmap(dpi=dpi).tobytes("png")


def decode(png: bytes) -> np.ndarray:
    array = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    assert array is not None
    return array


def encode(image: np.ndarray) -> bytes:
    ok, buffer = cv2.imencode(".png", image)
    assert ok
    return buffer.tobytes()


def rotate_image(image: np.ndarray, degrees: float) -> np.ndarray:
    """Rotate about the centre by ``degrees`` (positive = counter-clockwise), keeping the size
    and filling the corners white, as a scanner's slightly crooked paper would."""
    height, width = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), degrees, 1.0)
    return cv2.warpAffine(image, matrix, (width, height), borderValue=(255, 255, 255))


def add_speckle(image: np.ndarray, *, fraction: float = 0.012, seed: int = 7) -> np.ndarray:
    """Salt-and-pepper noise, the dust of a poor scan."""
    rng = np.random.default_rng(seed)
    noisy = image.copy()
    mask = rng.random(image.shape[:2])
    noisy[mask < fraction / 2] = 0
    noisy[mask > 1 - fraction / 2] = 255
    return noisy


def photo_of_page(png: bytes, *, canvas: tuple[int, int] = (1500, 1900)) -> np.ndarray:
    """The page as a phone would see it: tilted, in perspective, on a dark desk."""
    page = decode(png)
    height, width = page.shape[:2]
    out_w, out_h = canvas
    source = np.float32([[0, 0], [width, 0], [width, height], [0, height]])
    target = np.float32([[250, 180], [out_w - 120, 260], [out_w - 220, out_h - 150], [160, out_h - 260]])
    matrix = cv2.getPerspectiveTransform(source, target)
    desk = np.full((out_h, out_w, 3), (40, 55, 70), np.uint8)
    warped = cv2.warpPerspective(page, matrix, (out_w, out_h), desk, borderMode=cv2.BORDER_TRANSPARENT)
    return warped


def scanned_pdf_bytes(png: bytes, *, rotation: int = 0) -> bytes:
    """A one-page PDF holding only the picture -- no text -- which is what a scan is."""
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    page.insert_image(page.rect, stream=png)
    if rotation:
        page.set_rotation(rotation)
    return doc.tobytes()


def sideways_scan_pdf_bytes(png: bytes) -> bytes:
    """A scan whose picture is stored turned 90 degrees, with the page's own /Rotate turning it
    back upright -- how a scanner feeding paper sideways often writes it."""
    stored = cv2.rotate(decode(png), cv2.ROTATE_90_COUNTERCLOCKWISE)
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_H, height=PAGE_W)
    page.insert_image(page.rect, stream=encode(stored))
    page.set_rotation(90)
    return doc.tobytes()
