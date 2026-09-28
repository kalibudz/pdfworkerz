"""Page geometry shared by the editing modules."""

from __future__ import annotations

import pymupdf


def page_bounds(page: pymupdf.Page) -> pymupdf.Rect:
    """The page's area in the coordinates every edit uses (texttrace, links, images,
    drawings): unrotated. ``page.rect`` is the *rotated* view -- 800x600 for a
    600x800 page with /Rotate 90 -- so bounds checks against it refused valid
    positions and accepted ones off the visible page."""
    return page.rect * page.derotation_matrix
