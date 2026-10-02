"""OCR-03: the typed Op that straightens, cleans and turns scanned pages upright (engine.scanprep).
It edits the page's picture and rotation, so it is journaled like any edit."""

from __future__ import annotations

from typing import Literal

from engine.document import Document
from engine.ops.base import Op, register_op
from engine.scanprep import PageCleanup, clean_scanned_pages


@register_op
class CleanScanOp(Op):
    """Rotate upright, deskew and denoise the scanned pages. Each step can be switched off."""

    op: Literal["clean_scan"] = "clean_scan"
    page_indices: list[int] | None = None
    """Which pages (0-based); omitted means every page."""
    rotate: bool = True
    straighten: bool = True
    remove_noise: bool = True

    def apply(self, document: Document) -> list[PageCleanup]:
        return clean_scanned_pages(
            document,
            self.page_indices,
            rotate=self.rotate,
            straighten=self.straighten,
            remove_noise=self.remove_noise,
        )
