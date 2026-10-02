"""OCR-04: turn photos of paper into a clean PDF.

For each photo: find the sheet of paper in it, flatten that quadrilateral into an upright
rectangle (undoing the perspective of a phone held at an angle), tidy it in the chosen mode,
and place it on a page. Several photos make one multi-page PDF; the result can be made
searchable with the OCR of OCR-01.

When no sheet outline can be found (paper that fills the frame, or on a desk the same colour)
the whole photo is used as it is, and the result says so: a photo that can't be flattened is
still worth keeping.
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import cv2
import numpy as np
import pymupdf
from PIL import Image

from engine.document import Document
from engine.errors import ConversionError
from engine.ocr import OcrReport, ocr_document
from engine.rasters import OUTPUT_DPI, NamedFile, Paper, open_picture, page_box, to_bgr

Mode = Literal["original", "color", "gray", "bw"]

MAX_SIDE = 3300
"""Longest side of a flattened page in pixels, about A4 at 300 dpi."""
_MIN_PAPER_SHARE = 0.15
_JPEG_QUALITY = 85


@dataclass(frozen=True)
class ScanPage:
    """What happened to one photo."""

    name: str
    outline_found: bool
    note: str = ""


@dataclass(frozen=True)
class ScanResult:
    pdf: bytes
    pages: list[ScanPage]
    ocr: OcrReport | None = None


# ---------- finding and flattening the sheet ----------


def _order_corners(points: np.ndarray) -> np.ndarray:
    """Top-left, top-right, bottom-right, bottom-left."""
    by_sum, by_diff = points.sum(axis=1), np.diff(points, axis=1).ravel()
    return np.array(
        [points[np.argmin(by_sum)], points[np.argmin(by_diff)], points[np.argmax(by_sum)], points[np.argmax(by_diff)]],
        dtype=np.float32,
    )


def _outline_of(mask: np.ndarray, area: float) -> np.ndarray | None:
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)  # joins the page's printed text into one shape
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:3]:
        if cv2.contourArea(contour) < _MIN_PAPER_SHARE * area:
            break
        hull = cv2.convexHull(contour)
        approx = cv2.approxPolyDP(hull, 0.02 * cv2.arcLength(hull, True), True)
        if len(approx) == 4:
            return approx.reshape(4, 2).astype(np.float32)
        box = cv2.boxPoints(cv2.minAreaRect(hull))  # a page with a bent corner or two: its tight box
        if cv2.contourArea(hull) > 0.85 * cv2.contourArea(box.astype(np.float32)):
            return box.astype(np.float32)
    return None


def find_page_outline(image: np.ndarray) -> np.ndarray | None:
    """The four corners (top-left first, clockwise) of the sheet of paper, or None."""
    scale = 800 / max(image.shape[:2])
    small = cv2.resize(image, None, fx=min(scale, 1.0), fy=min(scale, 1.0), interpolation=cv2.INTER_AREA)
    blurred = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (7, 7), 0)
    area = float(blurred.shape[0] * blurred.shape[1])
    bright = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1]
    edges = cv2.dilate(cv2.Canny(blurred, 40, 120), np.ones((5, 5), np.uint8))
    for mask in (bright, edges):
        found = _outline_of(mask, area)
        if found is not None:
            corners = found / min(scale, 1.0)
            return _order_corners(corners)
    return None


def flatten(image: np.ndarray, corners: np.ndarray) -> np.ndarray:
    """The sheet inside ``corners`` as an upright rectangle."""
    top_left, top_right, bottom_right, bottom_left = corners
    width = int(max(np.linalg.norm(top_right - top_left), np.linalg.norm(bottom_right - bottom_left)))
    height = int(max(np.linalg.norm(bottom_left - top_left), np.linalg.norm(bottom_right - top_right)))
    shrink = min(1.0, MAX_SIDE / max(width, height))
    width, height = max(int(width * shrink), 1), max(int(height * shrink), 1)
    target = np.array([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(corners, target)
    return cv2.warpPerspective(image, matrix, (width, height), flags=cv2.INTER_CUBIC)


# ---------- the look of the page ----------


def _flattened_light(image: np.ndarray) -> np.ndarray:
    """The picture with the uneven light of a photo evened out: each pixel is divided by the
    paper brightness around it, so shadows and a bright corner leave the page white."""
    size = max(image.shape[:2]) // 12 | 1
    paper = cv2.medianBlur(cv2.dilate(image, np.ones((7, 7), np.uint8)), min(size, 255) | 1)
    return cv2.divide(image, np.maximum(paper, 1), scale=255)


def apply_mode(image: np.ndarray, mode: Mode) -> np.ndarray:
    """``original`` keeps the photo's colours; ``color`` evens the light and keeps colour;
    ``gray`` is that in gray; ``bw`` is pure black on white, the smallest file."""
    if mode == "original":
        return image
    even = _flattened_light(image)
    if mode == "color":
        return even
    gray = cv2.cvtColor(even, cv2.COLOR_BGR2GRAY)
    if mode == "gray":
        return gray
    return cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1]


# ---------- the PDF ----------


def _encoded(image: np.ndarray) -> tuple[bytes, str]:
    """Pictures with one channel of pure black and white go in as a 1-bit PNG; the rest as JPEG."""
    if image.ndim == 2 and set(np.unique(image)) <= {0, 255}:
        buffer = io.BytesIO()
        Image.fromarray(image).convert("1").save(buffer, format="PNG", optimize=True)
        return buffer.getvalue(), "png"
    ok, data = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, _JPEG_QUALITY])
    if not ok:
        raise ConversionError("a page could not be encoded")
    return data.tobytes(), "jpg"


def scan_to_pdf(
    photos: Sequence[NamedFile],
    *,
    mode: Mode = "color",
    paper: Paper = "fit",
    margin: float = 0.0,
    ocr_language: str | None = None,
) -> ScanResult:
    """One PDF page per photo, flattened and tidied. ``ocr_language`` (such as ``"eng"``) makes
    the result searchable. Refuses an unreadable photo by name before making anything."""
    if not photos:
        raise ConversionError("there are no photos to scan")
    if margin < 0:
        raise ConversionError("the margin cannot be negative")
    pictures = [to_bgr(open_picture(photo)) for photo in photos]  # every file is checked before any work is done
    document = pymupdf.open()
    reports = []
    for photo, picture in zip(photos, pictures, strict=True):
        corners = find_page_outline(picture)
        flat = flatten(picture, corners) if corners is not None else picture
        reports.append(
            ScanPage(
                photo.name,
                corners is not None,
                "" if corners is not None else "no page outline was found, so the whole photo is used",
            )
        )
        data, _ = _encoded(apply_mode(flat, mode))
        height, width = flat.shape[:2]
        page_rect, picture_rect = page_box((width, height), paper, margin, dpi=OUTPUT_DPI)
        document.new_page(width=page_rect.width, height=page_rect.height).insert_image(picture_rect, stream=data)
    result = Document.from_bytes(document.tobytes())
    ocr = ocr_document(result, language=ocr_language) if ocr_language else None
    return ScanResult(result.to_bytes(), reports, ocr)
