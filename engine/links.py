"""EDT-10: add, edit and remove hyperlinks.

A page's links are addressed by their position in ``Page.get_links()``
(``index`` below) -- the same "index into a fresh read of the page" model
the span Ops use, and only meaningful against the page's current state.

Two kinds of link are written: a URI link (``uri``) and an internal
go-to-page link (``target_page``). Anything else already in a document
(launch, named, remote go-to) is listed as ``kind="other"`` and can be
moved or removed but not retargeted. URIs are restricted to a small
scheme allowlist: a PDF link is clicked by someone else later, in a
viewer this tool doesn't control, so ``javascript:``/``file:``/``data:``
targets are refused rather than written.
"""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import urlsplit

import pymupdf
from pydantic import BaseModel, ConfigDict

from engine.document import Document
from engine.errors import OpValidationError
from engine.geometry import page_bounds

Rect = tuple[float, float, float, float]

ALLOWED_URI_SCHEMES = frozenset({"http", "https", "mailto"})


class LinkInfo(BaseModel):
    """One link on a page, as the UI and CLI see it."""

    model_config = ConfigDict(frozen=True)

    index: int
    rect: Rect
    kind: Literal["uri", "goto", "other"]
    uri: str | None = None
    target_page: int | None = None
    """0-based page a go-to link jumps to."""


def _kind(raw: dict[str, Any]) -> Literal["uri", "goto", "other"]:
    if raw["kind"] == pymupdf.LINK_URI:
        return "uri"
    if raw["kind"] == pymupdf.LINK_GOTO:
        return "goto"
    return "other"


def _info(index: int, raw: dict[str, Any]) -> LinkInfo:
    kind = _kind(raw)
    return LinkInfo(
        index=index,
        rect=tuple(raw["from"]),
        kind=kind,
        uri=raw.get("uri") if kind == "uri" else None,
        target_page=raw.get("page") if kind == "goto" else None,
    )


def validate_uri(uri: str) -> str:
    scheme = urlsplit(uri).scheme.lower()
    if scheme not in ALLOWED_URI_SCHEMES:
        allowed = ", ".join(sorted(ALLOWED_URI_SCHEMES))
        raise OpValidationError(f"link URI scheme {scheme or '(none)'!r} is not allowed; use one of: {allowed}")
    return uri


def _validate_rect(page: pymupdf.Page, rect: Rect) -> pymupdf.Rect:
    area = pymupdf.Rect(rect)
    if area.is_empty or area.is_infinite:
        raise OpValidationError(f"link rectangle {rect} is empty")
    if not area.intersects(page_bounds(page)):
        raise OpValidationError(f"link rectangle {rect} lies entirely outside the page")
    return area


def _validate_target(document: Document, target_page: int) -> int:
    if not 0 <= target_page < document.page_count:
        raise OpValidationError(f"target_page {target_page} is out of range (document has {document.page_count} pages)")
    return target_page


def _raw_links(document: Document, page_index: int) -> tuple[pymupdf.Page, list[dict[str, Any]]]:
    if not 0 <= page_index < document.page_count:
        raise OpValidationError(f"page_index {page_index} is out of range (document has {document.page_count} pages)")
    page = document.raw[page_index]
    return page, list(page.get_links())


def _raw_link_at(document: Document, page_index: int, index: int) -> tuple[pymupdf.Page, dict[str, Any]]:
    page, links = _raw_links(document, page_index)
    if not 0 <= index < len(links):
        raise OpValidationError(f"page {page_index} has {len(links)} link(s); index {index} is out of range")
    return page, links[index]


def _refresh(document: Document, page: pymupdf.Page) -> None:
    """PyMuPDF caches a page's link list: after insert/update/delete_link,
    ``get_links()`` -- on that Page object *or* a fresh ``doc[i]`` -- still
    returns the old list until the page is reloaded (confirmed against
    pymupdf 1.28.2; the saved bytes were already right)."""
    document.raw.reload_page(page)


def list_links(document: Document, page_index: int) -> list[LinkInfo]:
    _page, links = _raw_links(document, page_index)
    return [_info(index, raw) for index, raw in enumerate(links)]


def _target_fields(document: Document, uri: str | None, target_page: int | None) -> dict[str, Any]:
    if (uri is None) == (target_page is None):
        raise OpValidationError("a link needs exactly one of uri or target_page")
    if target_page is None:
        return {"kind": pymupdf.LINK_URI, "uri": validate_uri(str(uri))}
    return {"kind": pymupdf.LINK_GOTO, "page": _validate_target(document, target_page), "to": pymupdf.Point(0, 0)}


def add_link(
    document: Document, page_index: int, rect: Rect, *, uri: str | None = None, target_page: int | None = None
) -> LinkInfo:
    page, _links = _raw_links(document, page_index)
    link = {"from": _validate_rect(page, rect), **_target_fields(document, uri, target_page)}
    page.insert_link(link)
    _refresh(document, page)
    return list_links(document, page_index)[-1]


def update_link(
    document: Document,
    page_index: int,
    index: int,
    *,
    rect: Rect | None = None,
    uri: str | None = None,
    target_page: int | None = None,
) -> LinkInfo:
    """Move and/or retarget an existing link; fields left as None keep their
    current value. Retargeting may switch a link between URI and go-to."""
    page, raw = _raw_link_at(document, page_index, index)
    if rect is None and uri is None and target_page is None:
        raise OpValidationError("update_link: nothing to change (set rect, uri or target_page)")
    if rect is not None:
        raw["from"] = _validate_rect(page, rect)
    if uri is not None or target_page is not None:
        if _kind(raw) == "other":
            raise OpValidationError("this link's kind can't be retargeted; remove it and add a new one")
        for stale in ("uri", "page", "to", "zoom"):
            raw.pop(stale, None)
        raw.update(_target_fields(document, uri, target_page))
    page.update_link(raw)
    _refresh(document, page)
    return list_links(document, page_index)[index]


def remove_link(document: Document, page_index: int, index: int) -> LinkInfo:
    page, raw = _raw_link_at(document, page_index, index)
    removed = _info(index, raw)
    page.delete_link(raw)
    _refresh(document, page)
    return removed
