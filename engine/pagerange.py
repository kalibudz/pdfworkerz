"""Page ranges as people write them -- ``"1-3,5,8-"`` -- turned into 0-based page indices.

Every export (to Word, images, text, ...) takes an optional range; this is the one place that
reads it, so they all accept the same words and refuse a bad range the same way.
"""

from __future__ import annotations

from engine.errors import OpValidationError


def parse_page_range(spec: str | None, page_count: int) -> list[int]:
    """``None`` or blank means every page. Otherwise comma-separated pages (1-based) and
    ranges: ``3``, ``2-5``, ``8-`` (to the end), ``-4`` (the first four). Order and repeats
    are kept as written, so ``"3,1"`` gives page three then page one."""
    if spec is None or not spec.strip():
        return list(range(page_count))
    pages: list[int] = []
    for part in (piece.strip() for piece in spec.split(",")):
        if not part:
            raise OpValidationError(f"{spec!r}: there is an empty entry between commas")
        first, dash, last = part.partition("-")
        try:
            start = int(first) if first.strip() else 1
            end = int(last) if last.strip() else page_count
            if not dash:
                end = start
        except ValueError:
            raise OpValidationError(f"{part!r} is not a page or a range like 2-5") from None
        if start < 1 or end > page_count or start > end:
            raise OpValidationError(f"{part!r} is outside this document's pages 1-{page_count}")
        pages.extend(range(start - 1, end))
    return pages
