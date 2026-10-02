"""FNT-16: estimate how a piece of *scanned* text was typeset -- font class, weight, slant,
size and colour -- from its pixels alone, so replacement text can be drawn to match it.

The text is already known (Tesseract read it), so rather than guess from loose measurements the
estimator **renders that exact text** in each of twelve candidate faces (sans, serif and
monospace, each regular / bold / italic / bold-italic), stretches each rendering to the ink box
of the scanned text, and keeps the candidate whose picture correlates best with the scan. The
size then follows from how wide that candidate had to be drawn to span the same ink.

The twelve faces are the PDF base fonts (Helvetica, Times, Courier families): standing in for
the *kind* of face, not naming the exact one. So the estimate always comes with a confidence
tier saying how far to trust it, and a short word, or one whose best two candidates are nearly
tied, is reported as less certain instead of claiming precision it doesn't have.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import cv2
import numpy as np
import pymupdf

FontClass = Literal["sans", "serif", "mono"]
Confidence = Literal["high", "medium", "low"]

# (class, bold, italic) -> PDF base-14 font name
FACES: dict[tuple[FontClass, bool, bool], str] = {
    ("sans", False, False): "helv",
    ("sans", True, False): "hebo",
    ("sans", False, True): "heit",
    ("sans", True, True): "hebi",
    ("serif", False, False): "tiro",
    ("serif", True, False): "tibo",
    ("serif", False, True): "tiit",
    ("serif", True, True): "tibi",
    ("mono", False, False): "cour",
    ("mono", True, False): "cobo",
    ("mono", False, True): "coit",
    ("mono", True, True): "cobi",
}
_RENDER_PX = 120  # candidate faces are drawn this tall before being fitted to the scan
_HIGH, _MEDIUM, _MARGIN = 0.85, 0.72, 0.04
_MIN_CHARS_FOR_HIGH = 4


@dataclass(frozen=True)
class ScannedStyle:
    """The estimate for one word or line."""

    font_class: FontClass
    bold: bool
    italic: bool
    size_pt: float
    """Font size in points: how big the candidate face must be drawn to span the same ink width."""
    color: tuple[int, int, int]
    """Ink colour, RGB 0-255."""
    background: tuple[int, int, int]
    """The paper colour around it, RGB 0-255."""
    confidence: Confidence
    score: float
    """The winning candidate's correlation with the scan (1.0 is identical)."""
    baseline: float
    """Row of the baseline in the image given, in pixels from its top."""

    @property
    def base_font(self) -> str:
        """The PDF base-14 font name that stands for this face."""
        return FACES[(self.font_class, self.bold, self.italic)]


# ---------- the ink ----------


def _gray(image: np.ndarray) -> np.ndarray:
    return image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def ink_mask(image: np.ndarray) -> np.ndarray:
    """The text's pixels as a boolean mask: darker than the paper by Otsu's threshold, and
    clearly different from it (a blank patch of paper has no ink, however noisy)."""
    gray = _gray(image)
    level, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    paper = float(np.median(gray[binary == 0])) if (binary == 0).any() else float(np.median(gray))
    if paper - level < 25 or not binary.any():
        return np.zeros(gray.shape, bool)
    return binary > 0


def ink_box(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    rows, cols = np.flatnonzero(mask.any(axis=1)), np.flatnonzero(mask.any(axis=0))
    if rows.size == 0 or cols.size == 0:
        return None
    return int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1


def estimate_colours(image: np.ndarray, mask: np.ndarray) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """(ink, paper) as RGB. The ink is the median colour of the darkest third of the ink pixels,
    leaving out the blended edge pixels that would pull a dark blue towards gray."""
    rgb = cv2.cvtColor(image if image.ndim == 3 else cv2.cvtColor(image, cv2.COLOR_GRAY2BGR), cv2.COLOR_BGR2RGB)
    gray = _gray(image)
    paper_pixels = rgb[~mask] if (~mask).any() else rgb.reshape(-1, 3)
    paper = np.median(paper_pixels, axis=0)
    ink_pixels, ink_gray = rgb[mask], gray[mask]
    if ink_pixels.size == 0:
        return (0, 0, 0), tuple(int(v) for v in paper)  # type: ignore[return-value]
    core = ink_pixels[ink_gray <= np.percentile(ink_gray, 33)]
    ink = np.median(core if core.size else ink_pixels, axis=0)
    return tuple(int(v) for v in ink), tuple(int(v) for v in paper)  # type: ignore[return-value]


def estimate_baseline(mask: np.ndarray) -> float:
    """The row the letters stand on: where most of the glyphs end (those with a descender --
    g, j, p, q, y -- end lower and are the minority)."""
    _, _, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    bottoms = [int(s[cv2.CC_STAT_TOP] + s[cv2.CC_STAT_HEIGHT]) for s in stats[1:] if s[cv2.CC_STAT_AREA] >= 4]
    return float(np.median(bottoms)) if bottoms else float(mask.shape[0])


# ---------- candidate renderings ----------


def _render(text: str, face: str) -> np.ndarray:
    """The text drawn black on white in ``face``, as a gray picture."""
    font = pymupdf.Font(face)
    width = font.text_length(text, fontsize=_RENDER_PX)
    page_w, page_h = max(int(width) + 40, 40), _RENDER_PX * 2
    doc = pymupdf.open()
    page = doc.new_page(width=page_w, height=page_h)
    page.insert_text((20, _RENDER_PX * 1.3), text, fontsize=_RENDER_PX, fontname=face)
    pix = page.get_pixmap(dpi=72, colorspace=pymupdf.csGRAY, alpha=False)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width)


def _correlation(scan: np.ndarray, rendered: np.ndarray) -> tuple[float, float]:
    """How alike the scan's ink crop and a candidate rendering are once the rendering is
    stretched to the scan's box (less so if that took a very different stretch across than
    down), and the width scale used (scan px per rendered px)."""
    box = ink_box(rendered < 128)
    if box is None:
        return -1.0, 1.0
    x0, y0, x1, y1 = box
    height, width = scan.shape
    fitted = cv2.resize(rendered[y0:y1, x0:x1], (width, height), interpolation=cv2.INTER_AREA)
    blurred_scan, blurred_fit = cv2.GaussianBlur(scan, (0, 0), 1.0), cv2.GaussianBlur(fitted, (0, 0), 1.0)
    score = float(cv2.matchTemplate(blurred_scan, blurred_fit, cv2.TM_CCOEFF_NORMED)[0, 0])
    across, down = width / (x1 - x0), height / (y1 - y0)
    return score * min(across / down, down / across), across  # a face of the wrong proportions is penalised


def estimate_style(image: np.ndarray, text: str, *, dpi: float) -> ScannedStyle | None:
    """The style of ``text`` as it appears in ``image`` (a crop around one word or line, BGR or
    gray, scanned at ``dpi``). None when the crop holds no ink to measure."""
    text = text.strip()
    mask = ink_mask(image)
    box = ink_box(mask)
    if not text or box is None:
        return None
    x0, y0, x1, y1 = box
    crop = _gray(image)[y0:y1, x0:x1]
    crop = np.where(mask[y0:y1, x0:x1], crop, 255).astype(np.uint8)  # the paper becomes pure white
    scored = []
    for key, face in FACES.items():
        score, scale = _correlation(crop, _render(text, face))
        scored.append((score, scale, key))
    scored.sort(reverse=True)
    (best, scale, (font_class, bold, italic)), runner_up = scored[0], scored[1][0]
    ink, paper = estimate_colours(image, mask)
    long_enough = len(text.replace(" ", "")) >= _MIN_CHARS_FOR_HIGH
    if best >= _HIGH and best - runner_up >= _MARGIN and long_enough:
        confidence: Confidence = "high"
    elif best >= _MEDIUM:
        confidence = "medium"
    else:
        confidence = "low"
    size_px = _RENDER_PX * scale
    return ScannedStyle(
        font_class=font_class,
        bold=bold,
        italic=italic,
        size_pt=round(size_px * 72 / dpi, 1),
        color=ink,
        background=paper,
        confidence=confidence,
        score=round(best, 3),
        baseline=estimate_baseline(mask),
    )
