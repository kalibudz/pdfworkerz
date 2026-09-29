"""P5: typed Ops for page organization (ORG-01..08, ORG-13); see engine.pages.

Ops that change the document (merge, insert, move, rotate, delete, duplicate, remove blank
pages) are journaled like any edit. Ops that only write other files (extract, split) or only
look (find blank pages) leave the document unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal

from engine import pages
from engine.ops.base import Op, register_op

if TYPE_CHECKING:
    from engine.document import Document


@register_op
class MergeOp(Op):
    """ORG-01: add every page of another PDF, at the end or before page index `at`."""

    op: Literal["merge"] = "merge"
    path: str
    password: str | None = None
    at: int | None = None

    def apply(self, document: Document) -> int:
        return pages.merge(document, self.path, password=self.password, at=self.at)


@register_op
class InsertPagesOp(Op):
    """ORG-07: insert blank pages, or pages from another PDF, before page index `at`."""

    op: Literal["insert_pages"] = "insert_pages"
    at: int
    count: int = 1
    size: Literal["a4", "letter", "legal", "a3", "a5", "same"] = "same"
    from_path: str | None = None
    from_pages: list[int] | None = None
    password: str | None = None

    def apply(self, document: Document) -> int:
        size = None if self.size == "same" else pages.PAGE_SIZES[self.size]
        return pages.insert_pages(
            document,
            self.at,
            count=self.count,
            size=size,
            from_path=self.from_path,
            from_pages=self.from_pages,
            password=self.password,
        )


@register_op
class MovePagesOp(Op):
    """ORG-03: move pages so they start at position `to` among the other pages."""

    op: Literal["move_pages"] = "move_pages"
    page_indices: list[int]
    to: int

    def apply(self, document: Document) -> None:
        pages.move_pages(document, self.page_indices, self.to)


@register_op
class ReorderPagesOp(Op):
    """ORG-03 / UI-07: put every page in a new order (the organizer's drag-and-drop result)."""

    op: Literal["reorder_pages"] = "reorder_pages"
    order: list[int]

    def apply(self, document: Document) -> None:
        pages.reorder(document, self.order)


@register_op
class RotatePagesOp(Op):
    """ORG-04: turn pages clockwise by a multiple of 90 degrees."""

    op: Literal["rotate_pages"] = "rotate_pages"
    page_indices: list[int]
    degrees: int = 90

    def apply(self, document: Document) -> None:
        pages.rotate(document, self.page_indices, self.degrees)


@register_op
class DeletePagesOp(Op):
    """ORG-05: remove pages (at least one must remain)."""

    op: Literal["delete_pages"] = "delete_pages"
    page_indices: list[int]

    def apply(self, document: Document) -> None:
        pages.delete_pages(document, self.page_indices)


@register_op
class DuplicatePagesOp(Op):
    """ORG-08: insert copies of pages right after each one."""

    op: Literal["duplicate_pages"] = "duplicate_pages"
    page_indices: list[int]
    copies: int = 1

    def apply(self, document: Document) -> None:
        pages.duplicate_pages(document, self.page_indices, copies=self.copies)


@register_op
class ExtractPagesOp(Op):
    """ORG-06: write pages to a new PDF; the document itself is unchanged."""

    op: Literal["extract_pages"] = "extract_pages"
    page_indices: list[int]
    out: str
    overwrite: bool = False

    def apply(self, document: Document) -> str:
        return str(pages.extract_pages(document, self.page_indices, self.out, overwrite=self.overwrite))


@register_op
class SplitOp(Op):
    """ORG-02: write the document out as several PDFs -- every N pages, by page ranges, by a
    maximum file size, or at each top-level bookmark. The document itself is unchanged."""

    op: Literal["split"] = "split"
    out_dir: str
    every: int | None = None
    ranges: list[list[int]] | None = None
    max_bytes: int | None = None
    by_bookmarks: bool = False
    overwrite: bool = False

    def apply(self, document: Document) -> list[dict[str, object]]:
        plan = pages.split_plan(
            document, every=self.every, ranges=self.ranges, max_bytes=self.max_bytes, by_bookmarks=self.by_bookmarks
        )
        stem = document.source_path.stem if document.source_path else "document"
        written = pages.split(document, plan, Path(self.out_dir), stem=stem, overwrite=self.overwrite)
        return [{"path": str(part.path), "pages": part.pages} for part in written]


@register_op
class FindBlankPagesOp(Op):
    """ORG-13: list pages that are blank (no text, nearly all white). Changes nothing."""

    op: Literal["find_blank_pages"] = "find_blank_pages"
    threshold: float = 0.999

    def apply(self, document: Document) -> list[int]:
        return pages.blank_pages(document, threshold=self.threshold)


@register_op
class RemoveBlankPagesOp(Op):
    """ORG-13: delete every blank page (never the last remaining page). Returns those removed."""

    op: Literal["remove_blank_pages"] = "remove_blank_pages"
    threshold: float = 0.999

    def apply(self, document: Document) -> list[int]:
        blank = pages.blank_pages(document, threshold=self.threshold)
        if blank:
            pages.delete_pages(document, blank)
        return blank
