"""OCR-01 / OCR-02: the typed Op that makes pages searchable, and the read-only language list.

``OcrOp`` mutates the document (it adds the text layer), so it goes through the undo journal
like any edit: one OCR run is one undo step. See engine.ocr."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from engine.document import Document
from engine.ocr import DEFAULT_DPI, OcrReport, ocr_document
from engine.ocr_langs import Language, installed_languages
from engine.ops.base import Op, register_op


@register_op
class OcrOp(Op):
    """Recognise the text on scanned pages and add it as an invisible layer (OCR-01)."""

    op: Literal["ocr"] = "ocr"
    page_indices: list[int] | None = None
    """Which pages (0-based); omitted means every page."""
    language: str = "eng"
    """A Tesseract pack code, or several joined with +, like ``eng+deu`` (OCR-02)."""
    force: bool = False
    """Read pages that already have text too."""
    dpi: int = Field(default=DEFAULT_DPI, ge=72, le=600)

    def apply(self, document: Document) -> OcrReport:
        return ocr_document(document, self.page_indices, language=self.language, force=self.force, dpi=self.dpi)


@register_op
class OcrLanguagesOp(Op):
    """The OCR languages that are installed (OCR-02). Read-only, never journaled."""

    op: Literal["ocr_languages"] = "ocr_languages"

    def apply(self, document: Document | None = None) -> list[Language]:
        return installed_languages()
