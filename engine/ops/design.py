"""P5 slice 3: typed Ops for page design (DES-01..06); see engine.design. All journaled."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from engine import design
from engine.ops.base import Op, register_op

if TYPE_CHECKING:
    from engine.document import Document

Color = str | tuple[float, float, float]


@register_op
class PageNumbersOp(Op):
    """DES-01: number pages; `template` uses {n} and {total}."""

    op: Literal["page_numbers"] = "page_numbers"
    page_indices: list[int] | None = None
    skip_page_indices: list[int] | None = None
    template: str = "{n}"
    start: int = 1
    position: str = "bottom-center"
    size: float = 10.0
    margin: float = 30.0
    font: str = "helvetica"
    color: Color = "black"

    def apply(self, document: Document) -> list[str]:
        return design.page_numbers(
            document,
            pages=self.page_indices,
            skip=self.skip_page_indices,
            template=self.template,
            start=self.start,
            position=self.position,
            size=self.size,
            margin=self.margin,
            font=self.font,
            color=self.color,
        )


@register_op
class BatesOp(Op):
    """DES-02: sequential Bates identifiers on every page; returns the first and last."""

    op: Literal["bates"] = "bates"
    prefix: str = ""
    suffix: str = ""
    start: int = 1
    digits: int = 6
    position: str = "bottom-right"
    size: float = 9.0
    margin: float = 20.0

    def apply(self, document: Document) -> tuple[str, str]:
        return design.bates(
            document,
            prefix=self.prefix,
            suffix=self.suffix,
            start=self.start,
            digits=self.digits,
            position=self.position,
            size=self.size,
            margin=self.margin,
        )


@register_op
class HeaderFooterOp(Op):
    """DES-03: left/center/right header and footer; {page}, {total}, {title}, {file}, {date}."""

    op: Literal["header_footer"] = "header_footer"
    header: tuple[str, str, str] = ("", "", "")
    footer: tuple[str, str, str] = ("", "", "")
    page_indices: list[int] | None = None
    skip_page_indices: list[int] | None = None
    size: float = 9.0
    margin: float = 30.0
    font: str = "helvetica"
    color: Color = "black"
    date: str | None = None

    def apply(self, document: Document) -> int:
        return design.header_footer(
            document,
            header=self.header,
            footer=self.footer,
            pages=self.page_indices,
            skip=self.skip_page_indices,
            size=self.size,
            margin=self.margin,
            font=self.font,
            color=self.color,
            date=self.date,
        )


@register_op
class WatermarkOp(Op):
    """DES-04: a text or image watermark, with opacity, rotation, and over or behind content."""

    op: Literal["watermark"] = "watermark"
    text: str | None = None
    image: str | None = None
    page_indices: list[int] | None = None
    size: float = 60.0
    color: Color = "gray"
    opacity: float = 0.3
    rotation: float = 45.0
    behind: bool = False
    font: str = "helvetica-bold"
    scale: float = 0.5

    def apply(self, document: Document) -> int:
        return design.watermark(
            document,
            text=self.text,
            image=self.image,
            pages=self.page_indices,
            size=self.size,
            color=self.color,
            opacity=self.opacity,
            rotation=self.rotation,
            behind=self.behind,
            font=self.font,
            scale=self.scale,
        )


@register_op
class BackgroundOp(Op):
    """DES-05: a background color or image behind each page's content."""

    op: Literal["background"] = "background"
    color: Color | None = None
    image: str | None = None
    page_indices: list[int] | None = None

    def apply(self, document: Document) -> int:
        return design.background(document, color=self.color, image=self.image, pages=self.page_indices)


@register_op
class StampOp(Op):
    """DES-06: a preset stamp annotation ("approved", "draft", ...) or a custom text stamp."""

    op: Literal["stamp"] = "stamp"
    name: str = "approved"
    text: str | None = None
    page_indices: list[int] | None = None
    position: str = "top-right"
    color: Color = "red"
    width: float = 180.0
    margin: float = 36.0

    def apply(self, document: Document) -> int:
        return design.stamp(
            document,
            name=self.name,
            text=self.text,
            pages=self.page_indices,
            position=self.position,
            color=self.color,
            width=self.width,
            margin=self.margin,
        )
