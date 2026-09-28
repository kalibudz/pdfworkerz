"""EDT-10: typed hyperlink Ops (see engine.links)."""

from __future__ import annotations

from typing import Literal

from engine.document import Document
from engine.links import LinkInfo, Rect, add_link, list_links, remove_link, update_link
from engine.ops.base import Op, register_op


@register_op
class PageLinksOp(Op):
    """Every link on one page. Read-only, so -- like PageSpansOp -- it's
    never journaled; server/app.py serves it from a dedicated GET route."""

    op: Literal["page_links"] = "page_links"
    page_index: int = 0

    def apply(self, document: Document) -> list[LinkInfo]:
        return list_links(document, self.page_index)


@register_op
class AddLinkOp(Op):
    """Add a clickable area at `rect` (PDF points) linking to `uri` or to
    `target_page` -- exactly one of the two."""

    op: Literal["add_link"] = "add_link"
    page_index: int
    rect: Rect
    uri: str | None = None
    target_page: int | None = None

    def apply(self, document: Document) -> LinkInfo:
        return add_link(document, self.page_index, self.rect, uri=self.uri, target_page=self.target_page)


@register_op
class UpdateLinkOp(Op):
    """Move and/or retarget the link at `index` (its position in PageLinksOp's list)."""

    op: Literal["update_link"] = "update_link"
    page_index: int
    index: int
    rect: Rect | None = None
    uri: str | None = None
    target_page: int | None = None

    def apply(self, document: Document) -> LinkInfo:
        return update_link(
            document, self.page_index, self.index, rect=self.rect, uri=self.uri, target_page=self.target_page
        )


@register_op
class RemoveLinkOp(Op):
    """Delete the link at `index`; the text or artwork under it is untouched."""

    op: Literal["remove_link"] = "remove_link"
    page_index: int
    index: int

    def apply(self, document: Document) -> LinkInfo:
        return remove_link(document, self.page_index, self.index)
