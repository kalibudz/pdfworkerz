"""OCR-01: make a scanned PDF searchable by adding an invisible text layer.

Each image-only page is rendered, read by Tesseract, and the words it found are written
back as invisible text (PDF render mode 3) exactly over the words in the picture. The page
looks pixel-for-pixel the same afterwards, but its text can be searched, selected and
copied -- and, because it is ordinary text, ``Document.inspect`` and every text feature
see it like any other.

Tesseract's own command-line program is used (not a library binding) because it is the one
interface that gives per-word confidence and the line each word belongs to, which
FNT-16 / EDT-12 need as well.
"""

from __future__ import annotations

import csv
import io
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from engine.document import Document
from engine.errors import OcrError
from engine.external import run_tool
from engine.ocr_langs import resolve_languages

DEFAULT_DPI = 300
MAX_PIXELS_PER_SIDE = 9000  # keeps one page's render (and Tesseract's memory) bounded
_POINTS_PER_INCH = 72.0
_CSV_FIELD_LIMIT = 1 << 20
_FALLBACK_NAME = "pwfallback"


@dataclass(frozen=True)
class OcrWord:
    """One recognised word."""

    text: str
    rect: tuple[float, float, float, float]
    """x0, y0, x1, y1 in PDF points, on the page as it is *shown* (after the page's rotation)."""
    confidence: float
    """0-100, as Tesseract reports it."""
    line: tuple[int, int, int]
    """(block, paragraph, line) numbers: words sharing them are one line of text."""


@dataclass(frozen=True)
class PageOcr:
    """What OCR did to one page."""

    page_index: int
    status: str
    """"recognised", "skipped" (it already has text) or "no text found"."""
    words: int
    note: str = ""


@dataclass(frozen=True)
class OcrReport:
    language: str
    pages: list[PageOcr]

    @property
    def words(self) -> int:
        return sum(page.words for page in self.pages)


def page_has_text(page: pymupdf.Page) -> bool:
    """Whether the page already carries text a person or a search could use."""
    return bool(page.get_text("text").strip())


def _render_png(page: pymupdf.Page, dpi: int) -> tuple[bytes, float]:
    """The page as a PNG at ``dpi`` (lowered when the page is huge), and the scale used."""
    side = max(page.rect.width, page.rect.height) / _POINTS_PER_INCH
    dpi = max(36, min(dpi, int(MAX_PIXELS_PER_SIDE / side)))
    return page.get_pixmap(dpi=dpi).tobytes("png"), dpi / _POINTS_PER_INCH


def recognize_png(
    png: bytes, language: str, *, psm: int = 3, timeout: float = 300
) -> list[tuple[str, float, int, int, int, int, tuple[int, int, int]]]:
    """Run Tesseract on PNG bytes. Returns (text, confidence, left, top, width, height, line id)
    for every word, in pixels."""
    resolved, tessdata = resolve_languages(language)
    with tempfile.TemporaryDirectory(prefix="pdfworkerz-ocr-") as folder:
        image = Path(folder) / "page.png"
        image.write_bytes(png)
        result = run_tool(
            "tesseract",
            [image, "stdout", "-l", resolved, "--tessdata-dir", tessdata, "--psm", str(psm), "tsv"],
            timeout=timeout,
        )
    if result.returncode != 0:
        raise OcrError(f"Tesseract failed: {result.stderr.strip() or 'no message'}")
    csv.field_size_limit(_CSV_FIELD_LIMIT)
    words = []
    for row in csv.DictReader(io.StringIO(result.stdout), delimiter="\t", quoting=csv.QUOTE_NONE):
        text = (row.get("text") or "").strip()
        if row.get("level") != "5" or not text:
            continue
        words.append(
            (
                text,
                float(row["conf"]),
                int(row["left"]),
                int(row["top"]),
                int(row["width"]),
                int(row["height"]),
                (int(row["block_num"]), int(row["par_num"]), int(row["line_num"])),
            )
        )
    return words


def recognize_page(page: pymupdf.Page, language: str = "eng", *, dpi: int = DEFAULT_DPI) -> list[OcrWord]:
    """Read one page. Rects come back in points on the page as shown."""
    png, scale = _render_png(page, dpi)
    return [
        OcrWord(text, (left / scale, top / scale, (left + width) / scale, (top + height) / scale), conf, line)
        for text, conf, left, top, width, height, line in recognize_png(png, language)
    ]


def _fits_base_font(text: str) -> bool:
    try:
        text.encode("cp1252")
    except UnicodeEncodeError:
        return False
    return True


def add_invisible_text(page: pymupdf.Page, words: list[OcrWord]) -> None:
    """Write ``words`` onto the page as invisible text, each stretched to its word's box so a
    search highlight and a text selection land on the word you see. The text is laid out
    upright on the page as shown, and turned back by the page's own rotation."""
    base = pymupdf.Font("helv")
    fallback: pymupdf.Font | None = None  # for words the base font has no glyphs for (Cyrillic, Greek, CJK, ...)
    derotate = page.derotation_matrix
    turn = pymupdf.Matrix(page.rotation)
    for word in words:
        x0, y0, x1, y1 = word.rect
        height, width = y1 - y0, x1 - x0
        if height <= 0 or width <= 0:
            continue
        size = max(height * 0.85, 1.0)
        if _fits_base_font(word.text):
            font, fontname = base, "helv"
        else:
            if fallback is None:
                fallback = pymupdf.Font("cjk")
                page.insert_font(fontname=_FALLBACK_NAME, fontbuffer=fallback.buffer)
            font, fontname = fallback, _FALLBACK_NAME
        natural = font.text_length(word.text, fontsize=size)
        if natural <= 0:
            continue
        origin = pymupdf.Point(x0, y1 - height * 0.2) * derotate
        stretch = pymupdf.Matrix(width / natural, 0, 0, 1, 0, 0) * turn
        page.insert_text(origin, word.text, fontsize=size, fontname=fontname, render_mode=3, morph=(origin, stretch))


def ocr_document(
    document: Document,
    page_indices: list[int] | None = None,
    *,
    language: str = "eng",
    force: bool = False,
    dpi: int = DEFAULT_DPI,
) -> OcrReport:
    """Add a text layer to the given pages (all by default). A page that already has text is
    left alone and reported as skipped, unless ``force`` -- which reads it again and adds the
    result on top, for a page whose existing text is wrong or only partial."""
    resolved, _ = resolve_languages(language)  # refuses an unknown language before any page is touched
    indices = list(range(document.page_count)) if page_indices is None else page_indices
    results = []
    for index in indices:
        page = document.raw[index]
        if page_has_text(page) and not force:
            results.append(PageOcr(index, "skipped", 0, "it already has text; use force to read it again"))
            continue
        words = recognize_page(page, resolved, dpi=dpi)
        if not words:
            results.append(PageOcr(index, "no text found", 0, "nothing readable on this page"))
            continue
        add_invisible_text(page, words)
        results.append(PageOcr(index, "recognised", len(words)))
    return OcrReport(resolved, results)
