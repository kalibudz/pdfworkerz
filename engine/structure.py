"""P5 slice 5, document structure (DOC-01..05, COR-12): metadata and XMP, the bookmark
outline, bookmarks and a contents page generated from headings, file attachments, page labels,
and optional-content layers.
"""

from __future__ import annotations

import io
from collections import Counter
from pathlib import Path
from typing import Any, Literal

import pikepdf
import pymupdf
from pydantic import BaseModel, ConfigDict

from engine.document import Document
from engine.errors import OpValidationError, OverwriteRefusedError
from engine.pages import _check_indices
from engine.pdfbytes import plain_bytes

# -- DOC-01 metadata --

INFO_FIELDS = ("title", "author", "subject", "keywords", "creator", "producer")
# Each Info field and the XMP property viewers read instead when XMP is present.
_XMP_FOR = {
    "title": "dc:title",
    "author": "dc:creator",
    "subject": "dc:description",
    "keywords": "pdf:Keywords",
    "creator": "xmp:CreatorTool",
    "producer": "pdf:Producer",
}


class Metadata(BaseModel):
    model_config = ConfigDict(frozen=True)

    title: str = ""
    author: str = ""
    subject: str = ""
    keywords: str = ""
    creator: str = ""
    producer: str = ""
    xmp: dict[str, Any] = {}
    """XMP properties by prefixed name, e.g. "dc:title"."""


def _xmp_properties(document: Document) -> dict[str, Any]:
    with pikepdf.open(io.BytesIO(plain_bytes(document.raw))) as pdf:
        meta = pdf.open_metadata()
        out: dict[str, Any] = {}
        for qname, value in meta.items():
            uri, _, local = qname.strip("{").partition("}")
            prefix = next((p for u, p in pikepdf.models.metadata.DEFAULT_NAMESPACES if u == uri), uri)
            out[f"{prefix}:{local}"] = sorted(value) if isinstance(value, set) else value
        return out


def get_metadata(document: Document) -> Metadata:
    info = document.raw.metadata or {}
    return Metadata(**{field: info.get(field) or "" for field in INFO_FIELDS}, xmp=_xmp_properties(document))


def set_metadata(document: Document, fields: dict[str, str], *, xmp: dict[str, Any] | None = None) -> Metadata:
    """Change Info fields (title, author, subject, keywords, creator, producer) and keep the
    XMP copy in step, so every viewer shows the same values; `xmp` sets further XMP properties
    by prefixed name (e.g. {"dc:rights": "CC-BY"}). An empty string clears a field."""
    unknown = set(fields) - set(INFO_FIELDS)
    if unknown:
        raise OpValidationError(f"unknown metadata field(s) {sorted(unknown)}: use {', '.join(INFO_FIELDS)}")
    if not fields and not xmp:
        raise OpValidationError("give at least one field to change")
    info = dict(document.raw.metadata or {})
    info.update(fields)
    document.raw.set_metadata({k: v for k, v in info.items() if k in INFO_FIELDS or k in {"creationDate", "modDate"}})
    with pikepdf.open(io.BytesIO(plain_bytes(document.raw))) as pdf:
        with pdf.open_metadata(set_pikepdf_as_editor=False, update_docinfo=False) as meta:
            for field, value in fields.items():
                key = _XMP_FOR[field]
                if not value:
                    if key in meta:
                        del meta[key]
                elif field == "author":
                    meta[key] = [name.strip() for name in value.split(";") if name.strip()]
                else:
                    meta[key] = value
            for key, value in (xmp or {}).items():
                if ":" not in key:
                    raise OpValidationError(f"XMP names need a prefix, like dc:rights, not {key!r}")
                try:
                    meta[key] = value
                except (KeyError, ValueError) as exc:
                    raise OpValidationError(f"can't set XMP {key!r}: {exc}") from exc
        document.raw.set_xml_metadata(pdf.Root.Metadata.read_bytes().decode("utf-8"))
    return get_metadata(document)


# -- DOC-02 bookmarks --


class Bookmark(BaseModel):
    model_config = ConfigDict(frozen=True)

    index: int
    level: int
    title: str
    page_index: int
    """0-based target page; -1 when the bookmark points nowhere in this document."""


def list_bookmarks(document: Document) -> list[Bookmark]:
    return [
        Bookmark(index=i, level=level, title=title, page_index=page - 1)
        for i, (level, title, page, *_rest) in enumerate(document.raw.get_toc(simple=True))
    ]


def _write(document: Document, entries: list[tuple[int, str, int]]) -> list[Bookmark]:
    previous = 0
    for level, title, page_index in entries:
        if level < 1 or level > previous + 1:
            raise OpValidationError(
                f"bookmark {title!r} is at level {level}: start at 1, and go at most one level deeper each step"
            )
        if not title.strip():
            raise OpValidationError("bookmark titles can't be empty")
        if not -1 <= page_index < document.page_count:
            raise OpValidationError(f"bookmark {title!r} points to page {page_index + 1}, which doesn't exist")
        previous = level
    document.raw.set_toc([[level, title, page_index + 1] for level, title, page_index in entries])
    return list_bookmarks(document)


def _entries(document: Document) -> list[tuple[int, str, int]]:
    return [(b.level, b.title, b.page_index) for b in list_bookmarks(document)]


def _subtree_end(entries: list[tuple[int, str, int]], index: int) -> int:
    """One past the last descendant of entries[index]."""
    end = index + 1
    while end < len(entries) and entries[end][0] > entries[index][0]:
        end += 1
    return end


def _check_bookmark(entries: list[tuple[int, str, int]], index: int) -> None:
    if not 0 <= index < len(entries):
        raise OpValidationError(f"there is no bookmark {index}: the outline has {len(entries)}")


def set_bookmarks(document: Document, entries: list[tuple[int, str, int]]) -> list[Bookmark]:
    """Replace the whole outline: (level, title, 0-based page) per bookmark, in order."""
    return _write(document, entries)


def add_bookmark(
    document: Document, title: str, page_index: int, *, level: int = 1, at: int | None = None
) -> list[Bookmark]:
    """Insert a bookmark at outline position `at` (default: the end)."""
    entries = _entries(document)
    position = len(entries) if at is None else at
    if not 0 <= position <= len(entries):
        raise OpValidationError(f"position {position} is outside the outline (0-{len(entries)})")
    entries.insert(position, (level, title, page_index))
    return _write(document, entries)


def update_bookmark(
    document: Document, index: int, *, title: str | None = None, page_index: int | None = None, level: int | None = None
) -> list[Bookmark]:
    """Rename, retarget or change the level of one bookmark (its children move with it)."""
    entries = _entries(document)
    _check_bookmark(entries, index)
    old_level, old_title, old_page = entries[index]
    if level is not None and level != old_level:
        end = _subtree_end(entries, index)
        shift = level - old_level
        entries[index + 1 : end] = [(lv + shift, t, p) for lv, t, p in entries[index + 1 : end]]
    entries[index] = (
        old_level if level is None else level,
        old_title if title is None else title,
        old_page if page_index is None else page_index,
    )
    return _write(document, entries)


def delete_bookmark(document: Document, index: int) -> list[Bookmark]:
    """Remove a bookmark and its children."""
    entries = _entries(document)
    _check_bookmark(entries, index)
    del entries[index : _subtree_end(entries, index)]
    return _write(document, entries)


def move_bookmark(document: Document, index: int, to: int) -> list[Bookmark]:
    """Move a bookmark and its children so it starts at outline position `to` (counted in the
    outline without it). Its level is kept; a move that leaves it too deep is refused."""
    entries = _entries(document)
    _check_bookmark(entries, index)
    end = _subtree_end(entries, index)
    block = entries[index:end]
    rest = entries[:index] + entries[end:]
    if not 0 <= to <= len(rest):
        raise OpValidationError(f"position {to} is outside the outline (0-{len(rest)})")
    return _write(document, rest[:to] + block + rest[to:])


# -- DOC-03 bookmarks and contents page from headings --


def find_headings(document: Document, *, max_levels: int = 3, min_ratio: float = 1.15) -> list[tuple[int, str, int]]:
    """Lines set noticeably larger than the body text (the most common size, weighted by
    characters) become headings; the largest `max_levels` distinct sizes are levels 1, 2, 3."""
    if not 1 <= max_levels <= 6:
        raise OpValidationError("use 1 to 6 heading levels")
    lines: list[tuple[int, float, str, bool]] = []
    sizes: Counter[float] = Counter()
    for index in range(document.page_count):
        for block in document.raw[index].get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                spans = [s for s in line["spans"] if s["text"].strip()]
                if not spans:
                    continue
                text = " ".join("".join(s["text"] for s in spans).split())
                size = round(max(s["size"] for s in spans), 1)
                bold = all(s["flags"] & 16 for s in spans)
                sizes[size] += len(text)
                lines.append((index, size, text, bold))
    if not sizes:
        return []
    body = sizes.most_common(1)[0][0]
    heading_sizes = sorted(
        {size for _i, size, text, _b in lines if size >= body * min_ratio and len(text) <= 120}, reverse=True
    )
    levels = {size: n + 1 for n, size in enumerate(heading_sizes[:max_levels])}
    found: list[tuple[int, str, int]] = []
    previous = 0
    for index, size, text, _bold in lines:
        if size not in levels:
            continue
        level = min(levels[size], previous + 1)
        found.append((level, text, index))
        previous = level
    return found


def auto_bookmarks(document: Document, *, max_levels: int = 3, min_ratio: float = 1.15) -> list[Bookmark]:
    """Replace the outline with bookmarks for every heading found."""
    headings = find_headings(document, max_levels=max_levels, min_ratio=min_ratio)
    if not headings:
        raise OpValidationError("no headings found: nothing is set larger than the body text")
    return _write(document, headings)


def contents_page(document: Document, *, at: int = 0, title: str = "Contents") -> int:
    """Insert a contents page (from the outline, or from headings when there is none) at `at`,
    each entry linked to its page. Returns how many entries it lists (up to one page's worth)."""
    if not 0 <= at <= document.page_count:
        raise OpValidationError(f"position {at} is outside the document (0-{document.page_count})")
    entries = _entries(document) or find_headings(document)
    if not entries:
        raise OpValidationError("no bookmarks or headings to list")
    first = document.raw[0].rect if document.page_count else pymupdf.paper_rect("a4")
    page = document.raw.new_page(at, width=first.width, height=first.height)
    page.insert_text((72, 90), title, fontsize=20, fontname="hebo")
    y = 130.0
    listed = 0
    for level, text, target in entries:
        if y > first.height - 60:
            break
        if target < 0:
            continue
        shown = target + 1 if target < at else target + 2  # the new page pushes later pages on by one
        indent = 72 + 18 * (level - 1)
        label = text if len(text) <= 70 else text[:67] + "..."
        page.insert_text((indent, y), label, fontsize=11)
        number = str(shown)
        page.insert_text((first.width - 72 - pymupdf.get_text_length(number, fontsize=11), y), number, fontsize=11)
        page.insert_link(
            {
                "kind": pymupdf.LINK_GOTO,
                "from": pymupdf.Rect(indent, y - 11, first.width - 72, y + 3),
                "page": shown - 1,
            }
        )
        y += 18
        listed += 1
    return listed


# -- DOC-04 attachments --

MAX_ATTACHMENT_BYTES = 200 * 1024 * 1024


class Attachment(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    filename: str
    size: int
    description: str = ""


def list_attachments(document: Document) -> list[Attachment]:
    out = []
    for name in document.raw.embfile_names():
        info = document.raw.embfile_info(name)
        out.append(
            Attachment(
                name=name,
                filename=info.get("filename") or name,
                size=info.get("size") or info.get("length") or 0,
                description=info.get("description") or info.get("desc") or "",
            )
        )
    return out


def attach_file(document: Document, path: str, *, name: str | None = None, description: str = "") -> Attachment:
    source = Path(path)
    if not source.is_file():
        raise OpValidationError(f"file {path!r} does not exist")
    if source.stat().st_size > MAX_ATTACHMENT_BYTES:
        raise OpValidationError("attachments are limited to 200 MB")
    key = name or source.name
    if key in document.raw.embfile_names():
        raise OpValidationError(f"there is already an attachment called {key!r}")
    document.raw.embfile_add(key, source.read_bytes(), filename=source.name, ufilename=source.name, desc=description)
    return next(a for a in list_attachments(document) if a.name == key)


def _check_attachment(document: Document, name: str) -> None:
    names = document.raw.embfile_names()
    if name not in names:
        raise OpValidationError(f"no attachment called {name!r}" + (f": there are {', '.join(names)}" if names else ""))


def extract_attachment(document: Document, name: str, out: str, *, overwrite: bool = False) -> int:
    """Save one attachment to `out`; returns its size in bytes. The document is unchanged."""
    _check_attachment(document, name)
    target = Path(out)
    if target.exists() and not overwrite:
        raise OverwriteRefusedError(f"{target} already exists; pass overwrite to replace it")
    if not target.parent.is_dir():
        raise OpValidationError(f"folder {target.parent} does not exist")
    data = document.raw.embfile_get(name)
    target.write_bytes(data)
    return len(data)


def remove_attachment(document: Document, name: str) -> None:
    _check_attachment(document, name)
    document.raw.embfile_del(name)


# -- DOC-05 page labels --

LABEL_STYLES = {"decimal": "D", "roman": "r", "ROMAN": "R", "letters": "a", "LETTERS": "A", "none": ""}


class LabelRange(BaseModel):
    """Labels from `start_page` (0-based) until the next range: `prefix` + a number in `style`
    counting from `first`. Styles: decimal (1, 2), roman (i, ii), ROMAN (I, II), letters (a, b),
    LETTERS (A, B), none (prefix only)."""

    model_config = ConfigDict(frozen=True)

    start_page: int
    style: Literal["decimal", "roman", "ROMAN", "letters", "LETTERS", "none"] = "decimal"
    prefix: str = ""
    first: int = 1


def page_labels(document: Document) -> list[str]:
    return [document.raw[i].get_label() or str(i + 1) for i in range(document.page_count)]


def set_page_labels(document: Document, ranges: list[LabelRange]) -> list[str]:
    """Replace the page labels (an empty list removes them). Returns every page's label."""
    starts = [r.start_page for r in ranges]
    if ranges and starts[0] != 0:
        raise OpValidationError("the first label range must start at the first page (0)")
    if starts != sorted(set(starts)):
        raise OpValidationError("label ranges must start on different pages, in order")
    _check_indices(document, starts) if starts else None
    if any(r.first < 1 for r in ranges):
        raise OpValidationError("label numbers start at 1 or more")
    document.raw.set_page_labels(
        [
            {"startpage": r.start_page, "prefix": r.prefix, "style": LABEL_STYLES[r.style], "firstpagenum": r.first}
            for r in ranges
        ]
    )
    return page_labels(document)


# -- COR-12 optional content (layers) --


class Layer(BaseModel):
    model_config = ConfigDict(frozen=True)

    xref: int
    name: str
    visible: bool


def list_layers(document: Document) -> list[Layer]:
    # Measured on pymupdf 1.28.2: after set_layer, get_ocgs()'s "on" flags (and rendering) stay
    # stale until the document is reloaded; get_layer() and the saved file are current.
    state = document.raw.get_layer(-1)
    shown, hidden = set(state.get("on", [])), set(state.get("off", []))
    return [
        Layer(xref=xref, name=info["name"], visible=xref in shown or (xref not in hidden and bool(info["on"])))
        for xref, info in document.raw.get_ocgs().items()
    ]


def set_layer_visibility(document: Document, visibility: dict[int, bool]) -> list[Layer]:
    """Show or hide layers by xref in the document's default view (what opens in a viewer,
    prints and renders). Layers not named keep their state."""
    layers = {layer.xref: layer.visible for layer in list_layers(document)}
    unknown = set(visibility) - set(layers)
    if unknown:
        raise OpValidationError(f"no layer(s) {sorted(unknown)}: the document has {sorted(layers) or 'none'}")
    layers.update(visibility)
    document.raw.set_layer(-1, on=[x for x, v in layers.items() if v], off=[x for x, v in layers.items() if not v])
    return list_layers(document)
