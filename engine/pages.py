"""P5 page organization (ORG-01..08, ORG-13): whole pages added, removed, moved, turned,
copied or written out -- as opposed to engine.edit, which changes what is on a page.

Page numbers here are 0-based page indices, like every Op. Operations that write new files
(extract, split) never overwrite an existing file unless asked to (COR-08's rule for saves).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pymupdf

from engine.document import Document
from engine.errors import OpValidationError, OverwriteRefusedError
from engine.pdfbytes import PDF_ENCRYPT_KEEP, encrypted_snapshot, working_copy

A4 = (595.0, 842.0)
LETTER = (612.0, 792.0)
PAGE_SIZES = {"a4": A4, "letter": LETTER, "legal": (612.0, 1008.0), "a3": (842.0, 1191.0), "a5": (420.0, 595.0)}


def _check_indices(document: Document, pages: list[int], *, what: str = "page") -> None:
    if not pages:
        raise OpValidationError(f"no {what}s given")
    bad = [p for p in pages if not 0 <= p < document.page_count]
    if bad:
        raise OpValidationError(
            f"{what} index {bad[0]} is out of range: the document has {document.page_count} page(s)"
        )


def _open_other(path: str | Path, password: str | None) -> pymupdf.Document:
    source = Path(path)
    if not source.is_file():
        raise OpValidationError(f"{source} does not exist or is not a file")
    other = pymupdf.open(source, filetype="pdf")
    if other.needs_pass and not other.authenticate(password or ""):
        other.close()
        raise OpValidationError(f"{source} is password-protected; give its password")
    return other


def refuse_own_file(document: Document, target: Path) -> None:
    """An output file may never be the open document's own PDF: overwrite or not, that would
    replace the file a later save (and an incremental save especially) still reads from."""
    if document.source_path is not None and target.resolve() == document.source_path.resolve():
        raise OpValidationError(f"{target} is the open document itself; write to a different file")
    if target.is_dir():
        raise OpValidationError(f"{target} is a folder; give a file name")


def _refuse_overwrite(target: Path, overwrite: bool) -> None:
    if target.exists() and not overwrite:
        raise OverwriteRefusedError(f"{target} already exists; pass overwrite to replace it")


def merge(document: Document, path: str | Path, *, password: str | None = None, at: int | None = None) -> int:
    """ORG-01: append (or insert before page `at`) every page of another PDF. Returns pages added."""
    other = _open_other(path, password)
    try:
        start = document.page_count if at is None else at
        if not 0 <= start <= document.page_count:
            raise OpValidationError(
                f"can't insert at page index {start}: the document has {document.page_count} page(s)"
            )
        document.raw.insert_pdf(other, start_at=start)
        return other.page_count
    finally:
        other.close()


def insert_pages(
    document: Document,
    at: int,
    *,
    count: int = 1,
    size: tuple[float, float] | None = None,
    from_path: str | Path | None = None,
    from_pages: list[int] | None = None,
    password: str | None = None,
) -> int:
    """ORG-07: insert `count` blank pages (sized like page `at`, or `size`) before page index
    `at` -- or, with `from_path`, the given pages of another PDF. Returns pages added."""
    if not 0 <= at <= document.page_count:
        raise OpValidationError(f"can't insert at page index {at}: the document has {document.page_count} page(s)")
    if from_path is not None:
        other = _open_other(from_path, password)
        try:
            wanted = from_pages if from_pages is not None else list(range(other.page_count))
            bad = [p for p in wanted if not 0 <= p < other.page_count]
            if bad:
                raise OpValidationError(f"page index {bad[0]} is out of range in {from_path}")
            for offset, page in enumerate(wanted):
                document.raw.insert_pdf(other, from_page=page, to_page=page, start_at=at + offset)
            return len(wanted)
        finally:
            other.close()
    if count < 1:
        raise OpValidationError("count must be at least 1")
    if size is None:
        template = document.raw[min(at, document.page_count - 1)].rect
        size = (template.width, template.height)
    for offset in range(count):
        document.raw.new_page(at + offset, width=size[0], height=size[1])
    return count


def reorder(document: Document, order: list[int]) -> None:
    """ORG-03: the pages in a new order -- `order` lists every page index exactly once."""
    if sorted(order) != list(range(document.page_count)):
        raise OpValidationError("the new order must list every page exactly once")
    document.raw.select(order)


def move_pages(document: Document, pages: list[int], to: int) -> None:
    """ORG-03: move `pages` (kept in their current order) so they start at index `to` of the
    remaining pages -- 0 means the front; the number of remaining pages means the end."""
    _check_indices(document, pages)
    moving = sorted(set(pages))
    rest = [p for p in range(document.page_count) if p not in moving]
    if not 0 <= to <= len(rest):
        raise OpValidationError(f"can't move to position {to}: {len(rest)} other page(s)")
    document.raw.select(rest[:to] + moving + rest[to:])


def rotate(document: Document, pages: list[int], degrees: int) -> None:
    """ORG-04: turn pages by a multiple of 90 degrees (clockwise), on top of any rotation they have."""
    if degrees % 90:
        raise OpValidationError("pages rotate by multiples of 90 degrees")
    _check_indices(document, pages)
    for index in pages:
        page = document.raw[index]
        page.set_rotation((page.rotation + degrees) % 360)


def delete_pages(document: Document, pages: list[int]) -> None:
    """ORG-05: remove pages. At least one page must remain."""
    _check_indices(document, pages)
    doomed = set(pages)
    keep = [p for p in range(document.page_count) if p not in doomed]
    if not keep:
        raise OpValidationError("can't delete every page: a PDF needs at least one")
    document.raw.select(keep)


def duplicate_pages(document: Document, pages: list[int], *, copies: int = 1) -> None:
    """ORG-08: insert `copies` independent copies of each page right after it."""
    _check_indices(document, pages)
    if copies < 1:
        raise OpValidationError("copies must be at least 1")
    for index in sorted(set(pages), reverse=True):
        for _ in range(copies):
            document.raw.fullcopy_page(index, index + 1)


def _selected(document: Document, pages: list[int], snapshot: bytes | None = None) -> pymupdf.Document:
    """A copy holding just `pages`, in that order, still encrypted like the document (with the
    same passwords) and keeping its metadata, and the bookmarks and links among those pages."""
    copy = working_copy(document.raw, snapshot)
    copy.select(pages)
    return copy


def extract_pages(document: Document, pages: list[int], out: str | Path, *, overwrite: bool = False) -> Path:
    """ORG-06: write the given pages, in the given order, to a new PDF. The document itself
    is unchanged. An encrypted document's pages are written encrypted, with its passwords."""
    _check_indices(document, pages)
    target = Path(out)
    refuse_own_file(document, target)
    _refuse_overwrite(target, overwrite)
    new = _selected(document, pages)
    try:
        new.save(str(target), garbage=4, deflate=True, encryption=PDF_ENCRYPT_KEEP)
    finally:
        new.close()
    return target


@dataclass(frozen=True)
class SplitPart:
    path: Path
    pages: list[int]


def split_plan(
    document: Document,
    *,
    every: int | None = None,
    ranges: list[list[int]] | None = None,
    max_bytes: int | None = None,
    by_bookmarks: bool = False,
) -> list[list[int]]:
    """ORG-02: which pages go into each part. Exactly one way of splitting must be given."""
    chosen = [every is not None, ranges is not None, max_bytes is not None, by_bookmarks]
    if sum(chosen) != 1:
        raise OpValidationError("split by exactly one of: every N pages, ranges, maximum size, or bookmarks")
    count = document.page_count
    if every is not None:
        if every < 1:
            raise OpValidationError("every must be at least 1")
        return [list(range(start, min(start + every, count))) for start in range(0, count, every)]
    if ranges is not None:
        for part in ranges:
            _check_indices(document, part)
        return [list(part) for part in ranges]
    if by_bookmarks:
        starts = sorted({page - 1 for level, _title, page in document.raw.get_toc() if level == 1 and page >= 1})
        if not starts:
            raise OpValidationError("the document has no top-level bookmarks to split at")
        if starts[0] != 0:
            starts.insert(0, 0)
        ends = [*starts[1:], count]
        return [list(range(s, e)) for s, e in zip(starts, ends, strict=True) if e > s]
    if max_bytes is None or max_bytes < 1:
        raise OpValidationError("the maximum size must be positive")
    parts: list[list[int]] = []
    current: list[int] = []
    snapshot = encrypted_snapshot(document.raw)
    for page in range(count):
        trial = [*current, page]
        if current and _bytes_for(document, trial, snapshot) > max_bytes:
            parts.append(current)
            current = [page]
        else:
            current = trial
    parts.append(current)
    return parts


def _bytes_for(document: Document, pages: list[int], snapshot: bytes | None = None) -> int:
    """The size a part holding `pages` is written at (the same way extract_pages writes it)."""
    new = _selected(document, pages, snapshot)
    try:
        return len(new.tobytes(garbage=4, deflate=True, encryption=PDF_ENCRYPT_KEEP))
    finally:
        new.close()


def split(
    document: Document, parts: list[list[int]], out_dir: str | Path, *, stem: str, overwrite: bool = False
) -> list[SplitPart]:
    """ORG-02: write each part to `<out_dir>/<stem>.part<N>.pdf`. The document is unchanged.
    Every target is checked before anything is written, so a refusal writes nothing."""
    directory = Path(out_dir)
    if not directory.is_dir():
        raise OpValidationError(f"{directory} is not a folder")
    targets = [directory / f"{stem}.part{n}.pdf" for n in range(1, len(parts) + 1)]
    for target in targets:
        refuse_own_file(document, target)
        _refuse_overwrite(target, overwrite)
    return [
        SplitPart(extract_pages(document, pages, target, overwrite=True), pages)
        for target, pages in zip(targets, parts, strict=True)
    ]


def blank_pages(document: Document, *, threshold: float = 0.999, dpi: int = 30) -> list[int]:
    """ORG-13: pages that render (almost) entirely white -- no text, and at least `threshold`
    of their pixels within a few levels of pure white. Scanner noise and a faint speck pass;
    a page with any text never does."""
    blank = []
    for index in range(document.page_count):
        page = document.raw[index]
        if page.get_text().strip():
            continue
        pixmap = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
        pixels = np.frombuffer(pixmap.samples, dtype=np.uint8)
        if pixels.size == 0 or float((pixels >= 250).mean()) >= threshold:
            blank.append(index)
    return blank
