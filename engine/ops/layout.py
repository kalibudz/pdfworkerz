"""P5 slice 2: typed Ops for page geometry (ORG-09..12); see engine.layout. All journaled."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from engine import layout
from engine.ops.base import Op, register_op

if TYPE_CHECKING:
    from engine.document import Document


@register_op
class CropPagesOp(Op):
    """ORG-09: show only `box` of each page, or trim `margins` (left, top, right, bottom)."""

    op: Literal["crop_pages"] = "crop_pages"
    page_indices: list[int]
    box: tuple[float, float, float, float] | None = None
    margins: tuple[float, float, float, float] | None = None

    def apply(self, document: Document) -> None:
        layout.crop(document, self.page_indices, box=self.box, margins=self.margins)


@register_op
class UncropPagesOp(Op):
    """ORG-09: show the whole page again."""

    op: Literal["uncrop_pages"] = "uncrop_pages"
    page_indices: list[int]

    def apply(self, document: Document) -> None:
        layout.uncrop(document, self.page_indices)


@register_op
class ResizePagesOp(Op):
    """ORG-10: a new page size ("a4", "letter-landscape", ... or [width, height] points),
    scaling content to fit (or keeping its size, centered, with `scale` false)."""

    op: Literal["resize_pages"] = "resize_pages"
    page_indices: list[int]
    size: str | tuple[float, float]
    scale: bool = True
    drop_interactive: bool = False
    """Resize even though annotations or form fields on those pages can't be carried over."""

    def apply(self, document: Document) -> None:
        layout.resize(document, self.page_indices, self.size, scale=self.scale, drop_interactive=self.drop_interactive)


@register_op
class NUpOp(Op):
    """ORG-11: `cols` x `rows` pages per sheet, in reading order. Replaces every page."""

    op: Literal["n_up"] = "n_up"
    cols: int = 2
    rows: int = 1
    sheet: str | tuple[float, float] = "a4-landscape"
    gap: float = 12.0
    drop_interactive: bool = False
    """Impose even though links, annotations and form fields can't be carried onto the sheets."""

    def apply(self, document: Document) -> int:
        return layout.n_up(
            document,
            cols=self.cols,
            rows=self.rows,
            sheet=self.sheet,
            gap=self.gap,
            drop_interactive=self.drop_interactive,
        )


@register_op
class BookletOp(Op):
    """ORG-12: saddle-stitch booklet imposition, two pages per sheet side. Replaces every page."""

    op: Literal["booklet"] = "booklet"
    sheet: str | tuple[float, float] = "a4-landscape"
    gap: float = 0.0
    drop_interactive: bool = False
    """Impose even though links, annotations and form fields can't be carried onto the sheets."""

    def apply(self, document: Document) -> int:
        return layout.booklet(document, sheet=self.sheet, gap=self.gap, drop_interactive=self.drop_interactive)
