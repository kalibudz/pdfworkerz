"""SEC-08/09/10/11: typed Ops for true redaction, pattern packs and sanitizing.

See engine.redact and engine.sanitize for the actual implementations; these Ops are
thin, journaled wrappers, following the same pattern as engine.ops.images/shapes.
"""

from __future__ import annotations

from typing import Literal

from engine.document import Document
from engine.ops.base import Op, register_op
from engine.redact import PatternMatch, Rect, RedactionResult, find_pattern_matches, redact_areas
from engine.sanitize import SanitizeReport, sanitize_document


@register_op
class RedactAreasOp(Op):
    """SEC-08: true redaction -- removes the glyphs, image pixels and vector paths
    under each of `rects` on `page_index`, not a box drawn over them. Runs SEC-11's
    verification automatically; raises RedactionVerificationError if it finds anything
    still recoverable, which -- like any other Op raising -- leaves the document
    exactly as it was before this Op ran (the undo journal rolls it back)."""

    op: Literal["redact_areas"] = "redact_areas"
    page_index: int
    rects: list[Rect]
    verify: bool = True

    def apply(self, document: Document) -> RedactionResult:
        return redact_areas(document, self.page_index, self.rects, verify=self.verify)


@register_op
class FindRedactionCandidatesOp(Op):
    """SEC-09: every match of a built-in pattern ("email", "phone", "ssn",
    "credit_card") or a custom regex on one page. Read-only -- like PageSpansOp, never
    journaled; nothing is redacted until the caller takes the returned rects and
    applies RedactAreasOp to the ones it wants, in one undo step."""

    op: Literal["find_redaction_candidates"] = "find_redaction_candidates"
    page_index: int
    pattern: str

    def apply(self, document: Document) -> list[PatternMatch]:
        return find_pattern_matches(document, self.page_index, self.pattern)


@register_op
class SanitizeOp(Op):
    """SEC-10: remove metadata/XMP, JavaScript, embedded files and hidden (invisible)
    text from the whole document, each independently opt-out-able. One journal entry
    for every category removed together."""

    op: Literal["sanitize"] = "sanitize"
    remove_metadata: bool = True
    remove_javascript: bool = True
    remove_embedded_files: bool = True
    remove_hidden_text: bool = True

    def apply(self, document: Document) -> SanitizeReport:
        return sanitize_document(
            document,
            remove_metadata=self.remove_metadata,
            remove_javascript=self.remove_javascript,
            remove_embedded_files=self.remove_embedded_files,
            remove_hidden_text=self.remove_hidden_text,
        )
