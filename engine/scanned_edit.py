"""EDT-12: edit text on a scanned page.

A scan is a picture, so "editing" a word means changing pixels, and doing it honestly:

1. The old text's ink is **removed from the picture itself** (inpainted from the paper around
   it) -- not covered by a patch -- so nothing of it is left to be recovered underneath.
2. The new text is drawn in the picture in the style estimated from the old (FNT-16: face
   class, weight, slant, size, colour), left-aligned where the old text began and sitting on
   the same baseline, so the page remains what it was: one picture.
3. If the page carries an invisible OCR text layer (OCR-01), the old words in the area are
   removed from it and the new ones added, so search keeps finding what the page shows.

Only a scanned page is edited this way (one picture, no visible text); a page with real text
belongs to the normal text editor. Pixels outside the edited area are not touched.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import cv2
import numpy as np
import pymupdf

from engine.document import Document
from engine.edit import _REDACT_KWARGS as _REDACT_TEXT_ONLY
from engine.errors import OcrError, OpValidationError
from engine.ocr import OcrWord, add_invisible_text, recognize_page, recognize_png
from engine.scanned_style import FACES, FontClass, ScannedStyle, estimate_style, ink_box, ink_mask
from engine.scanpage import ScanPicture, has_hidden_text, scan_picture

DEFAULT_DPI = 300
_PAD = 3  # pixels of paper kept round the rectangle, so the ink's edge is never at the crop's edge
_MIN_SHRINK = 0.6
_JPEG_QUALITY = 92
_SOFTEN = 0.5  # blur sigma, at 300 dpi, applied to the new text so it isn't sharper than its scan


@dataclass(frozen=True)
class ScannedLine:
    """One line of text OCR found on a scanned page."""

    text: str
    rect: tuple[float, float, float, float]
    """x0, y0, x1, y1 in points on the page as shown."""
    confidence: float


@dataclass(frozen=True)
class StyleChoice:
    """Anything the user sets by hand overrides the estimate."""

    font_class: FontClass | None = None
    bold: bool | None = None
    italic: bool | None = None
    size_pt: float | None = None
    color: tuple[int, int, int] | None = None


@dataclass(frozen=True)
class ScannedEdit:
    """What an edit did."""

    page_index: int
    old_text: str
    new_text: str
    style: ScannedStyle
    """The style the new text was drawn in: the estimate with any overrides applied."""
    rect: tuple[float, float, float, float]
    notes: list[str] = field(default_factory=list)


# ---------- the page and its picture ----------


def _scanned(document: Document, page_index: int) -> tuple[pymupdf.Page, ScanPicture]:
    page = document.raw[page_index]
    picture = scan_picture(page)
    if picture is None:
        raise OcrError(
            f"Page {page_index + 1} is not a scanned page (it has real text, or more than one picture), "
            "so use the normal text editor on it"
        )
    if not picture.axis_aligned:
        raise OcrError(f"The picture on page {page_index + 1} is turned or flipped; straighten the scan first")
    return page, picture


def find_scanned_lines(
    document: Document, page_index: int, *, language: str = "eng", dpi: int = DEFAULT_DPI
) -> list[ScannedLine]:
    """The lines of text on a scanned page, as OCR reads them -- what the user picks from."""
    page, _ = _scanned(document, page_index)
    lines: dict[tuple[int, int, int], list[OcrWord]] = {}
    for word in recognize_page(page, language, dpi=dpi):
        lines.setdefault(word.line, []).append(word)
    found = []
    for words in lines.values():
        words.sort(key=lambda w: w.rect[0])
        rect = (
            min(w.rect[0] for w in words),
            min(w.rect[1] for w in words),
            max(w.rect[2] for w in words),
            max(w.rect[3] for w in words),
        )
        found.append(
            ScannedLine(" ".join(w.text for w in words), rect, round(sum(w.confidence for w in words) / len(words), 1))
        )
    return sorted(found, key=lambda line: (line.rect[1], line.rect[0]))


def _stored_image(document: Document, picture: ScanPicture) -> tuple[np.ndarray, str]:
    info = document.raw.extract_image(picture.xref)
    image = cv2.imdecode(np.frombuffer(info["image"], np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise OcrError("the page's picture is in a format that can't be edited")
    return image, str(info["ext"])


def _pixel_box(
    page: pymupdf.Page, picture: ScanPicture, rect: tuple[float, float, float, float]
) -> tuple[int, int, int, int]:
    """The rectangle (as shown, in points) as a box of the stored picture's pixels, padded a little."""
    unrotated = (pymupdf.Rect(rect) * page.derotation_matrix).normalize()
    a, _, _, d, e, f = picture.transform
    x0, x1 = (unrotated.x0 - e) / a * picture.width, (unrotated.x1 - e) / a * picture.width
    y0, y1 = (unrotated.y0 - f) / d * picture.height, (unrotated.y1 - f) / d * picture.height
    box = (int(x0) - _PAD, int(y0) - _PAD, int(np.ceil(x1)) + _PAD, int(np.ceil(y1)) + _PAD)
    clipped = (max(box[0], 0), max(box[1], 0), min(box[2], picture.width), min(box[3], picture.height))
    if clipped[2] - clipped[0] < 4 or clipped[3] - clipped[1] < 4:
        raise OpValidationError("the area to edit is empty or outside the page")
    return clipped


def _picture_dpi(picture: ScanPicture) -> float:
    return picture.width / (picture.transform[0] / 72)


_TURNS = {0: None, 90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}
_UNTURNS = {0: None, 90: cv2.ROTATE_90_COUNTERCLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_CLOCKWISE}


def _turned(image: np.ndarray, code: int | None) -> np.ndarray:
    return image if code is None else cv2.rotate(image, code)


@dataclass(frozen=True)
class _Region:
    """A rectangle of a scanned page, as pixels of its picture."""

    page: pymupdf.Page
    picture: ScanPicture
    image: np.ndarray
    extension: str
    box: tuple[int, int, int, int]
    crop: np.ndarray
    """The pixels as the page *shows* them: upright text, even when the picture is stored turned
    and the page's rotation turns it back."""
    dpi: float

    def stored(self, shown: np.ndarray) -> np.ndarray:
        """Pixels edited as shown, back in the orientation the picture is stored in."""
        return _turned(shown, _UNTURNS[self.page.rotation])


def _region(document: Document, page_index: int, rect: tuple[float, float, float, float]) -> _Region:
    page, picture = _scanned(document, page_index)
    image, extension = _stored_image(document, picture)
    x0, y0, x1, y1 = box = _pixel_box(page, picture, rect)
    shown = _turned(image[y0:y1, x0:x1].copy(), _TURNS[page.rotation])
    return _Region(page, picture, image, extension, box, shown, _picture_dpi(picture))


# ---------- reading, removing and drawing text ----------


def _read_line(crop: np.ndarray) -> str:
    ok, png = cv2.imencode(".png", cv2.copyMakeBorder(crop, 20, 20, 20, 20, cv2.BORDER_REPLICATE))
    words = recognize_png(png.tobytes(), "eng", psm=7) if ok else []
    return " ".join(word[0] for word in words)


def _erase_ink(crop: np.ndarray) -> np.ndarray:
    """The crop with its text's ink filled in from the paper around it."""
    mask = ink_mask(crop)
    reach = max(2, round(0.07 * crop.shape[0]))
    grown = cv2.dilate(
        mask.astype(np.uint8) * 255, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * reach + 1, 2 * reach + 1))
    )
    return cv2.inpaint(crop, grown, reach + 1, cv2.INPAINT_TELEA)


def _text_alpha(
    text: str, face: str, size_pt: float, dpi: float, shape: tuple[int, int], origin: tuple[float, float]
) -> np.ndarray:
    """The text as coverage (0 to 1) on a canvas ``shape`` (rows, columns) pixels, its baseline
    starting at ``origin`` (x, y) pixels."""
    rows, cols = shape
    scale = 72 / dpi
    doc = pymupdf.open()
    page = doc.new_page(width=cols * scale, height=rows * scale)
    page.insert_text(pymupdf.Point(origin[0] * scale, origin[1] * scale), text, fontsize=size_pt, fontname=face)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(dpi / 72, dpi / 72), colorspace=pymupdf.csGRAY, alpha=False)
    gray = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width)
    canvas = np.full(shape, 255, np.uint8)
    canvas[: min(rows, pix.height), : min(cols, pix.width)] = gray[:rows, :cols]
    softened = cv2.GaussianBlur(canvas, (0, 0), _SOFTEN * dpi / 300)
    return 1.0 - softened.astype(np.float32) / 255.0


def _apply_overrides(style: ScannedStyle, choice: StyleChoice | None) -> ScannedStyle:
    if choice is None:
        return style
    changes = {
        k: v
        for k, v in (
            ("font_class", choice.font_class),
            ("bold", choice.bold),
            ("italic", choice.italic),
            ("size_pt", choice.size_pt),
            ("color", choice.color),
        )
        if v is not None
    }
    return replace(style, **changes)  # type: ignore[arg-type]


def _check_drawable(text: str) -> None:
    if "\n" in text or "\r" in text:
        raise OpValidationError("Edit one line at a time: the new text cannot contain a line break")
    bad = sorted({char for char in text if not _drawable(char)})
    if bad:
        raise OpValidationError(
            f"These characters can't be drawn on a scanned page: {' '.join(bad)}. Use letters, digits and symbols"
        )


def _drawable(char: str) -> bool:
    try:
        char.encode("cp1252")
    except UnicodeEncodeError:
        return False
    return char.isprintable()


# ---------- the edit ----------


def edit_scanned_text(
    document: Document,
    page_index: int,
    rect: tuple[float, float, float, float],
    new_text: str,
    *,
    old_text: str | None = None,
    style: StyleChoice | None = None,
) -> ScannedEdit:
    """Replace the text inside ``rect`` (points, as shown) with ``new_text``. ``old_text`` is read
    by OCR when not given. An empty ``new_text`` removes the text."""
    new_text = new_text.strip()
    _check_drawable(new_text)
    region = _region(document, page_index, rect)
    page, picture, image, crop, dpi = region.page, region.picture, region.image, region.crop, region.dpi
    x0, y0, x1, y1 = region.box

    ink = ink_box(ink_mask(crop))
    if ink is None:
        raise OcrError("There is no text in that area to replace")
    old_text = (old_text if old_text is not None else _read_line(crop)).strip()
    if not old_text:
        raise OcrError(
            "The text in that area could not be read; type what it says as `old_text` so its style can be measured"
        )
    estimate = estimate_style(crop, old_text, dpi=dpi)
    if estimate is None:
        raise OcrError("There is no text in that area to replace")
    used = _apply_overrides(estimate, style)
    notes: list[str] = []

    cleaned = _erase_ink(crop)
    face = FACES[(used.font_class, used.bold, used.italic)]
    if new_text:
        size = used.size_pt
        width_px = pymupdf.Font(face).text_length(new_text, fontsize=size) * dpi / 72
        room = crop.shape[1] - ink[0] - _PAD
        if width_px > room:
            shrink = room / width_px
            if shrink < _MIN_SHRINK:
                raise OpValidationError(
                    f"The new text is {width_px / room:.1f} times wider than the space it replaces; "
                    "shorten it or select a wider area"
                )
            size = round(size * shrink, 1)
            notes.append(f"The text was reduced from {used.size_pt} to {size} pt to fit the space")
            used = replace(used, size_pt=size)
        alpha = _text_alpha(new_text, face, size, dpi, crop.shape[:2], (ink[0], used.baseline))[..., None]
        colour = np.array(used.color[::-1], np.float32)  # BGR
        cleaned = (cleaned.astype(np.float32) * (1 - alpha) + colour * alpha).clip(0, 255).astype(np.uint8)

    edited = image.copy()
    edited[y0:y1, x0:x1] = region.stored(cleaned)
    ok, data = cv2.imencode(
        ".jpg" if region.extension in ("jpg", "jpeg") else ".png", edited, [cv2.IMWRITE_JPEG_QUALITY, _JPEG_QUALITY]
    )
    if not ok:
        raise OcrError("the edited picture could not be encoded")
    if has_hidden_text(page):
        _update_text_layer(page, rect, new_text, face=face, size=used.size_pt)
    page.replace_image(picture.xref, stream=data.tobytes())
    return ScannedEdit(page_index, old_text, new_text, used, rect, notes)


def _update_text_layer(
    page: pymupdf.Page, rect: tuple[float, float, float, float], new_text: str, *, face: str, size: float
) -> None:
    """Take the old words in ``rect`` out of the invisible OCR layer and put the new ones in."""
    area = (pymupdf.Rect(rect) * page.derotation_matrix).normalize()
    page.add_redact_annot(area)
    page.apply_redactions(**_REDACT_TEXT_ONLY)
    if not new_text:
        return
    font = pymupdf.Font(face)
    words, cursor = [], rect[0]
    for word in new_text.split():
        width = font.text_length(word, fontsize=size)
        words.append(OcrWord(word, (cursor, rect[1], cursor + width, rect[3]), 100.0, (0, 0, 0)))
        cursor += font.text_length(word + " ", fontsize=size)
    add_invisible_text(page, words)


def estimate_line_style(
    document: Document, page_index: int, rect: tuple[float, float, float, float], text: str | None = None
) -> ScannedStyle | None:
    """How the text inside ``rect`` was typeset (FNT-16), reading it by OCR if ``text`` isn't given.
    None when the area holds no text."""
    region = _region(document, page_index, rect)
    return estimate_style(region.crop, text if text is not None else _read_line(region.crop), dpi=region.dpi)
