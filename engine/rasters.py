"""Reading picture files (PNG, JPEG, BMP, GIF, TIFF, WebP) the way a person expects: upright.

Shared by Scan to PDF (OCR-04) and Images to PDF (CVT-04). A phone stores a portrait photo
sideways plus an EXIF "orientation" note; applying that note here means every caller sees the
picture as it looks, and a file that is not a picture is refused by name, up front.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Literal

import cv2
import numpy as np
import pymupdf
from PIL import Image, ImageOps, UnidentifiedImageError

from engine.errors import ConversionError

MAX_PIXELS = 120_000_000
"""Refuse pictures above this (an A0 poster at 300 dpi): decoding one could exhaust memory."""
Image.MAX_IMAGE_PIXELS = MAX_PIXELS

SUPPORTED = ("PNG", "JPEG", "BMP", "GIF", "TIFF", "WEBP")
Paper = Literal["fit", "a4", "letter", "legal"]
PAPER_POINTS: dict[str, tuple[float, float]] = {
    "a4": (595.276, 841.89),
    "letter": (612.0, 792.0),
    "legal": (612.0, 1008.0),
}
OUTPUT_DPI = 200
MAX_FIT_POINTS = 1190.0
"""A page made to "fit" its picture is never longer than A3, however many pixels the picture has."""


@dataclass(frozen=True)
class NamedFile:
    """A file's name (for messages) with its bytes."""

    name: str
    data: bytes


def open_picture(file: NamedFile) -> Image.Image:
    """The picture, upright, in RGB (or L for a gray one). Only the first frame of an animated
    GIF or multi-page TIFF is used."""
    try:
        picture = Image.open(io.BytesIO(file.data))
        if picture.format not in SUPPORTED:
            raise ConversionError(f"{file.name} is a {picture.format} picture; PDFWorkerz reads {', '.join(SUPPORTED)}")
        picture.seek(0)
        upright = ImageOps.exif_transpose(picture)
        upright.load()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ConversionError(f"{file.name} is not a picture that can be read ({exc})") from exc
    return _opaque(upright)


def _opaque(picture: Image.Image) -> Image.Image:
    """A transparent picture laid on white (as a printed page would show it), as L or RGB."""
    if picture.mode in ("RGBA", "LA", "PA") or "transparency" in picture.info:
        rgba = picture.convert("RGBA")
        paper = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        return Image.alpha_composite(paper, rgba).convert("RGB")
    return picture.convert("L" if picture.mode in ("1", "L") else "RGB")


def to_bgr(picture: Image.Image) -> np.ndarray:
    """The picture as the BGR array OpenCV works on."""
    rgb = np.asarray(picture.convert("RGB"))
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def page_box(
    pixels: tuple[int, int], paper: Paper, margin: float, *, dpi: float = OUTPUT_DPI
) -> tuple[pymupdf.Rect, pymupdf.Rect]:
    """The page and the rectangle on it that a picture ``pixels`` wide and high fills. "fit"
    makes the page the picture's own size at ``dpi`` (shrunk to A3 at most); a paper size keeps
    the picture's proportions, turned landscape if the picture is, centred inside the margin."""
    width, height = pixels
    if paper == "fit":
        shrink = min(1.0, MAX_FIT_POINTS / (max(width, height) * 72 / dpi))
        page = pymupdf.Rect(0, 0, width * 72 / dpi * shrink, height * 72 / dpi * shrink)
        return page, pymupdf.Rect(margin, margin, page.x1 - margin, page.y1 - margin)
    short, long = PAPER_POINTS[paper]
    page = pymupdf.Rect(0, 0, long, short) if width > height else pymupdf.Rect(0, 0, short, long)
    room = pymupdf.Rect(margin, margin, page.x1 - margin, page.y1 - margin)
    fit = min(room.width / width, room.height / height)
    shown_w, shown_h = width * fit, height * fit
    left, top = room.x0 + (room.width - shown_w) / 2, room.y0 + (room.height - shown_h) / 2
    return page, pymupdf.Rect(left, top, left + shown_w, top + shown_h)
