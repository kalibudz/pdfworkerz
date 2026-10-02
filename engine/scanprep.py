"""OCR-03: straighten, clean and turn upright the pages of a scanned PDF.

Three independent steps, each optional, each reporting what it did:

* **Rotate** -- Tesseract's orientation detection says whether the page is sideways or upside
  down; the fix is the page's own ``/Rotate``, so no pixel is touched.
* **Deskew** -- the angle the text lines tilt by is found by projecting the page's ink onto
  the vertical axis at many angles; the straightest projection (sharpest rows) wins. The
  scan's picture is then turned by that angle.
* **Denoise** -- dust specks (tiny isolated dark dots) are replaced by their surroundings.

Only a page that is *one picture and no text* is touched: a page with real text has nothing
to straighten, and a page built from several pictures would have them straightened apart.
"""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import pymupdf

from engine.document import Document
from engine.errors import OcrError
from engine.external import run_tool
from engine.ocr import page_has_text
from engine.ocr_langs import resolve_languages
from engine.scanpage import has_hidden_text, scan_picture

ANALYSIS_DPI = 200
MIN_SKEW = 0.3
"""Degrees: tilts smaller than this are scanner jitter, and rotating would only soften the text."""
MAX_SKEW = 15.0
MIN_ORIENTATION_CONFIDENCE = 1.5
"""Tesseract's own scale; below this its guess about sideways/upside down is not trusted."""
MIN_SPECKS = 40
"""A page with fewer isolated specks than this counts as clean and is left alone."""
_ROTATE = re.compile(r"Rotate:\s*(\d+)")
_CONFIDENCE = re.compile(r"Orientation confidence:\s*([\d.]+)")
_JPEG_QUALITY = 92


@dataclass(frozen=True)
class PageCleanup:
    """What the cleanup did to one page."""

    page_index: int
    status: str
    """"cleaned", "unchanged" (nothing needed doing) or "skipped"."""
    rotated_by: int = 0
    """Degrees clockwise the page was turned to stand upright (0, 90, 180 or 270)."""
    skew_corrected: float = 0.0
    """Degrees the tilted text was turned back by (0 when it was straight enough)."""
    specks_removed: int = 0
    note: str = ""


# ---------- pixels ----------


def _to_gray(image: np.ndarray) -> np.ndarray:
    return image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def _ink(gray: np.ndarray) -> np.ndarray:
    """The dark marks on the page as 1s."""
    _, binary = cv2.threshold(gray, 0, 1, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    return binary


def _rotated(image: np.ndarray, degrees: float, fill: tuple[int, ...] | int) -> np.ndarray:
    """Turn the picture counter-clockwise by ``degrees`` about its centre, same size."""
    height, width = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), degrees, 1.0)
    return cv2.warpAffine(image, matrix, (width, height), flags=cv2.INTER_CUBIC, borderValue=fill)


def estimate_skew(image: np.ndarray) -> float:
    """How far the text lines tilt, in degrees counter-clockwise (so turning the picture
    clockwise by this much straightens it). 0.0 when there is no clear text to judge by."""
    ink = _ink(_to_gray(image))
    scale = 1000 / max(ink.shape)
    if scale < 1:
        ink = cv2.resize(ink, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

    def sharpness(angle: float) -> float:
        rows = _rotated(ink, -angle, 0).sum(axis=1).astype(np.float64)
        return float(np.sum(np.diff(rows) ** 2))  # sharp row edges: text lines fall exactly on rows

    coarse = np.arange(-MAX_SKEW, MAX_SKEW + 0.01, 0.5)
    scores = [sharpness(angle) for angle in coarse]
    best = float(coarse[int(np.argmax(scores))])
    if max(scores) < 1.3 * float(np.median(scores)):
        return 0.0  # no angle stands out: a blank page, a photo, or text too sparse to judge
    fine = np.arange(best - 0.5, best + 0.51, 0.05)
    return round(float(fine[int(np.argmax([sharpness(angle) for angle in fine]))]), 2)


def _median_colour(image: np.ndarray) -> tuple[int, ...] | int:
    """The page's usual colour -- its paper -- for filling the corners a turn uncovers."""
    flat = image.reshape(-1, image.shape[-1] if image.ndim == 3 else 1)
    colour = tuple(int(v) for v in np.median(flat, axis=0))
    return colour if image.ndim == 3 else colour[0]


def deskew(picture: np.ndarray, measured_on: np.ndarray | None = None) -> tuple[np.ndarray, float]:
    """The picture turned straight, and the angle it was turned back by (0.0 and the same
    picture when the tilt is under :data:`MIN_SKEW`). The tilt is measured on ``measured_on``
    when given -- the page as shown, for a picture stored turned: a page's rotation turns the
    text by a multiple of 90 degrees, which leaves a tilt's angle as it was, so the angle
    measured on one applies to the other."""
    angle = estimate_skew(picture if measured_on is None else measured_on)
    if abs(angle) < MIN_SKEW:
        return picture, 0.0
    return _rotated(picture, -angle, _median_colour(picture)), angle


def _speck_mask(image: np.ndarray) -> np.ndarray:
    """Pixels that belong to a dark speck too small to be part of any character."""
    ink = _ink(_to_gray(image))
    _, labels, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    longest = max(image.shape[:2])
    limit = max(2, round((longest / 3500) ** 2 * 6))  # a 2x2 dot at 200 dpi; a full stop is bigger
    small = np.flatnonzero(stats[1:, cv2.CC_STAT_AREA] <= limit) + 1
    return np.isin(labels, small)


def denoise(image: np.ndarray) -> tuple[np.ndarray, int]:
    """The picture without its dust specks, and how many specks that was. A page with only a
    handful is returned as it is."""
    mask = _speck_mask(image)
    count = int(cv2.connectedComponents(mask.astype(np.uint8), connectivity=8)[0] - 1)
    if count < MIN_SPECKS:
        return image, 0
    cleaned = np.where(mask[..., None] if image.ndim == 3 else mask, cv2.medianBlur(image, 7), image)
    return cleaned.astype(image.dtype), count


# ---------- orientation ----------


def detect_rotation(page: pymupdf.Page) -> tuple[int, float]:
    """The clockwise turn (0, 90, 180, 270) that stands the page upright, with Tesseract's
    confidence in it; (0, 0.0) when it can't tell, as on a page with hardly any text."""
    _, tessdata = resolve_languages("osd")
    with tempfile.TemporaryDirectory(prefix="pdfworkerz-osd-") as folder:
        image = Path(folder) / "page.png"
        image.write_bytes(page.get_pixmap(dpi=ANALYSIS_DPI).tobytes("png"))
        out = run_tool(
            "tesseract", [image, "stdout", "--psm", "0", "-l", "osd", "--tessdata-dir", tessdata], timeout=120
        )
    turn, confidence = _ROTATE.search(out.stdout), _CONFIDENCE.search(out.stdout)
    if out.returncode != 0 or not turn or not confidence:
        return 0, 0.0
    return int(turn.group(1)), float(confidence.group(1))


# ---------- pages ----------


def _only_picture(page: pymupdf.Page) -> int | None:
    """The xref of the page's one full-page picture, or None if the page isn't a scan -- or is one
    that already carries OCR text, which turning or straightening the picture would leave misplaced."""
    picture = scan_picture(page)
    return None if picture is None or has_hidden_text(page) else picture.xref


def _as_shown(page: pymupdf.Page) -> np.ndarray:
    pix = page.get_pixmap(dpi=ANALYSIS_DPI)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)


def _stored_picture(document: Document, xref: int) -> tuple[np.ndarray, str]:
    info = document.raw.extract_image(xref)
    array = cv2.imdecode(np.frombuffer(info["image"], np.uint8), cv2.IMREAD_UNCHANGED)
    if array is None:
        raise OcrError("the page's picture is in a format that can't be read for cleanup")
    return array, str(info["ext"])


def _encoded(image: np.ndarray, ext: str) -> bytes:
    if ext in ("jpg", "jpeg"):
        ok, data = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, _JPEG_QUALITY])
    else:
        ok, data = cv2.imencode(".png", image)
    if not ok:
        raise OcrError("the cleaned picture could not be encoded")
    return data.tobytes()


def clean_scanned_pages(
    document: Document,
    page_indices: list[int] | None = None,
    *,
    rotate: bool = True,
    straighten: bool = True,
    remove_noise: bool = True,
) -> list[PageCleanup]:
    """Rotate, straighten and denoise the scanned pages. A step that is switched off, or that
    finds nothing to do, changes nothing; a page with nothing to do at all is "unchanged"."""
    if rotate:
        resolve_languages("osd")  # refuse up front, before any page is changed, if orientation can't be detected
    indices = list(range(document.page_count)) if page_indices is None else page_indices
    return [_clean_page(document, index, rotate, straighten, remove_noise) for index in indices]


def _clean_page(document: Document, index: int, rotate: bool, straighten: bool, remove_noise: bool) -> PageCleanup:
    page = document.raw[index]
    xref = _only_picture(page)
    if xref is None:
        reason = "it has text already" if page_has_text(page) else "it is not a single full-page picture"
        return PageCleanup(index, "skipped", note=f"{reason}, so there is nothing to straighten")
    picture, ext = _stored_picture(document, xref)

    turned = 0
    if rotate:
        degrees, confidence = detect_rotation(page)
        if degrees and confidence >= MIN_ORIENTATION_CONFIDENCE:
            page.set_rotation((page.rotation + degrees) % 360)
            turned = degrees

    skew = 0.0
    if straighten:
        picture, skew = deskew(picture, measured_on=_as_shown(page))

    specks = 0
    if remove_noise:
        picture, specks = denoise(picture)

    if skew or specks:
        page.replace_image(xref, stream=_encoded(picture, ext))
    status = "cleaned" if (turned or skew or specks) else "unchanged"
    return PageCleanup(index, status, rotated_by=turned, skew_corrected=skew, specks_removed=specks)
