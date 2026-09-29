"""P5 slice 5: typed Ops for document structure (DOC-01..05, COR-12); see engine.structure."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

import engine.structure as st
from engine.ops.base import Op, register_op

if TYPE_CHECKING:
    from engine.document import Document


@register_op
class GetMetadataOp(Op):
    """DOC-01: Info fields and XMP properties. Read-only."""

    op: Literal["get_metadata"] = "get_metadata"

    def apply(self, document: Document) -> st.Metadata:
        return st.get_metadata(document)


@register_op
class SetMetadataOp(Op):
    """DOC-01: change Info fields (kept in step with XMP) and further XMP properties."""

    op: Literal["set_metadata"] = "set_metadata"
    fields: dict[str, str] = Field(default_factory=dict)
    xmp: dict[str, Any] | None = None

    def apply(self, document: Document) -> st.Metadata:
        return st.set_metadata(document, self.fields, xmp=self.xmp)


@register_op
class ListBookmarksOp(Op):
    """DOC-02: the outline, flattened in order. Read-only."""

    op: Literal["list_bookmarks"] = "list_bookmarks"

    def apply(self, document: Document) -> list[st.Bookmark]:
        return st.list_bookmarks(document)


@register_op
class SetBookmarksOp(Op):
    """DOC-02: replace the outline: [level, title, 0-based page] per bookmark."""

    op: Literal["set_bookmarks"] = "set_bookmarks"
    entries: list[tuple[int, str, int]]

    def apply(self, document: Document) -> list[st.Bookmark]:
        return st.set_bookmarks(document, self.entries)


@register_op
class AddBookmarkOp(Op):
    """DOC-02: insert one bookmark at outline position `at` (default: the end)."""

    op: Literal["add_bookmark"] = "add_bookmark"
    title: str
    page_index: int
    level: int = 1
    at: int | None = None

    def apply(self, document: Document) -> list[st.Bookmark]:
        return st.add_bookmark(document, self.title, self.page_index, level=self.level, at=self.at)


@register_op
class UpdateBookmarkOp(Op):
    """DOC-02: rename, retarget or re-level bookmark `index` (children move with it)."""

    op: Literal["update_bookmark"] = "update_bookmark"
    index: int
    title: str | None = None
    target_page_index: int | None = None
    level: int | None = None

    def apply(self, document: Document) -> list[st.Bookmark]:
        return st.update_bookmark(
            document, self.index, title=self.title, page_index=self.target_page_index, level=self.level
        )


@register_op
class DeleteBookmarkOp(Op):
    """DOC-02: remove bookmark `index` and its children."""

    op: Literal["delete_bookmark"] = "delete_bookmark"
    index: int

    def apply(self, document: Document) -> list[st.Bookmark]:
        return st.delete_bookmark(document, self.index)


@register_op
class MoveBookmarkOp(Op):
    """DOC-02: move bookmark `index` (with its children) to outline position `to`."""

    op: Literal["move_bookmark"] = "move_bookmark"
    index: int
    to: int

    def apply(self, document: Document) -> list[st.Bookmark]:
        return st.move_bookmark(document, self.index, self.to)


@register_op
class AutoBookmarksOp(Op):
    """DOC-03: replace the outline with bookmarks for the headings found."""

    op: Literal["auto_bookmarks"] = "auto_bookmarks"
    max_levels: int = 3
    min_ratio: float = 1.15

    def apply(self, document: Document) -> list[st.Bookmark]:
        return st.auto_bookmarks(document, max_levels=self.max_levels, min_ratio=self.min_ratio)


@register_op
class ContentsPageOp(Op):
    """DOC-03: insert a linked contents page at `at` from the outline (or headings)."""

    op: Literal["contents_page"] = "contents_page"
    at: int = 0
    title: str = "Contents"

    def apply(self, document: Document) -> int:
        return st.contents_page(document, at=self.at, title=self.title)


@register_op
class ListAttachmentsOp(Op):
    """DOC-04: embedded files. Read-only."""

    op: Literal["list_attachments"] = "list_attachments"

    def apply(self, document: Document) -> list[st.Attachment]:
        return st.list_attachments(document)


@register_op
class AttachFileOp(Op):
    """DOC-04: embed a file."""

    op: Literal["attach_file"] = "attach_file"
    path: str
    name: str | None = None
    description: str = ""

    def apply(self, document: Document) -> st.Attachment:
        return st.attach_file(document, self.path, name=self.name, description=self.description)


@register_op
class ExtractAttachmentOp(Op):
    """DOC-04: save an embedded file to `out`. The document is unchanged."""

    op: Literal["extract_attachment"] = "extract_attachment"
    name: str
    out: str
    overwrite: bool = False

    def apply(self, document: Document) -> int:
        return st.extract_attachment(document, self.name, self.out, overwrite=self.overwrite)


@register_op
class RemoveAttachmentOp(Op):
    """DOC-04: remove an embedded file."""

    op: Literal["remove_attachment"] = "remove_attachment"
    name: str

    def apply(self, document: Document) -> None:
        st.remove_attachment(document, self.name)


@register_op
class SetPageLabelsOp(Op):
    """DOC-05: replace the page labels (i, ii, 1, 2, A-1 ...); returns every page's label."""

    op: Literal["set_page_labels"] = "set_page_labels"
    ranges: list[st.LabelRange]

    def apply(self, document: Document) -> list[str]:
        return st.set_page_labels(document, self.ranges)


@register_op
class ListLayersOp(Op):
    """COR-12: optional-content layers and whether each is shown. Read-only."""

    op: Literal["list_layers"] = "list_layers"

    def apply(self, document: Document) -> list[st.Layer]:
        return st.list_layers(document)


@register_op
class SetLayerVisibilityOp(Op):
    """COR-12: show or hide layers (by xref) in the default view."""

    op: Literal["set_layer_visibility"] = "set_layer_visibility"
    visibility: dict[int, bool]

    def apply(self, document: Document) -> list[st.Layer]:
        return st.set_layer_visibility(document, self.visibility)
