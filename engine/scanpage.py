"""What makes a page "a scan": one full-page picture, and no *visible* text.

A scanned page may also carry an invisible OCR text layer (OCR-01), which is still a scan: the
words you see are the picture's pixels. Real, visible text means a page made by a word
processor or editor, which the normal text editor handles instead. Shared by the scan cleanup
(OCR-03) and by editing scanned text (EDT-12), so they agree on which pages they will touch.
"""

from __future__ import annotations

from dataclasses import dataclass

import pymupdf

_FULL_PAGE = 0.85
_INVISIBLE = 3  # PDF text render mode 3: neither filled nor stroked


@dataclass(frozen=True)
class ScanPicture:
    """The one picture a scanned page is made of."""

    xref: int
    transform: tuple[float, float, float, float, float, float]
    """Maps the picture's unit square onto the page (PDF's own convention, y down from the top)."""
    width: int
    height: int
    """The stored picture's size in pixels."""

    @property
    def axis_aligned(self) -> bool:
        """Placed upright and unflipped: pixels map to page points by plain scaling, which is
        what edits that work on pixels rely on."""
        a, b, c, d, _, _ = self.transform
        return a > 0 and d > 0 and abs(b) < 1e-3 and abs(c) < 1e-3


def has_visible_text(page: pymupdf.Page) -> bool:
    """Text a reader can see: not the invisible layer OCR adds."""
    return any(
        span["type"] != _INVISIBLE and any(not chr(char[0]).isspace() for char in span["chars"])
        for span in page.get_texttrace()
    )


def has_hidden_text(page: pymupdf.Page) -> bool:
    return any(span["type"] == _INVISIBLE for span in page.get_texttrace())


def scan_picture(page: pymupdf.Page) -> ScanPicture | None:
    """The page's picture if the page is a scan (one picture filling the page, no visible text),
    else None."""
    if has_visible_text(page):
        return None
    pictures = page.get_image_info(xrefs=True)
    if len(pictures) != 1 or not pictures[0].get("xref"):
        return None
    info = pictures[0]
    sheet = (page.rect * page.derotation_matrix).normalize()  # the picture's bbox is in unrotated page space
    if (pymupdf.Rect(info["bbox"]) & sheet).get_area() < _FULL_PAGE * sheet.get_area():
        return None
    return ScanPicture(int(info["xref"]), tuple(info["transform"]), int(info["width"]), int(info["height"]))
