"""Pixel-diff visual regression harness (INF-07).

This is the same machinery FNT-12 will use at runtime to verify a text edit
("render before and after, flag anything outside the edit mask that
changed") and that the P1 regression suite uses to guard against silent
rendering regressions. It has no dependency on any specific feature, only
on PyMuPDF's renderer and numpy.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pymupdf
from numpy.typing import NDArray

DEFAULT_TOLERANCE = 0.001  # fraction of pixels allowed to differ (anti-aliasing noise)


@dataclass(frozen=True)
class DiffResult:
    """The result of comparing two equally-sized renders."""

    changed_fraction: float
    """Fraction of pixels (0.0-1.0) that differ by more than one channel step."""
    max_channel_diff: int
    """The single largest per-channel absolute difference found (0-255)."""
    changed_bbox: tuple[int, int, int, int] | None
    """(x0, y0, x1, y1) bounding box of changed pixels, or None if nothing changed."""

    @property
    def matches(self) -> bool:
        return self.changed_fraction == 0.0


def render_to_array(doc: pymupdf.Document, page_index: int, *, dpi: int = 150) -> NDArray[np.uint8]:
    """Render one page to an (H, W, channels) uint8 array."""
    pixmap = doc[page_index].get_pixmap(dpi=dpi)
    array: NDArray[np.uint8] = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
        pixmap.height, pixmap.width, pixmap.n
    )
    return array.copy()  # pixmap.samples is a view into memory MuPDF may reuse; copy before it's freed


def pixel_diff(before: NDArray[np.uint8], after: NDArray[np.uint8]) -> DiffResult:
    """Compare two renders of the same size and report how much, and where, they differ."""
    if before.shape != after.shape:
        raise ValueError(f"cannot diff renders of different shapes: {before.shape} vs {after.shape}")

    diff = np.abs(before.astype(np.int16) - after.astype(np.int16))
    changed_pixels = np.any(diff > 0, axis=-1)
    changed_count = int(changed_pixels.sum())
    total_pixels = changed_pixels.size

    if changed_count == 0:
        return DiffResult(changed_fraction=0.0, max_channel_diff=0, changed_bbox=None)

    rows = np.any(changed_pixels, axis=1)
    cols = np.any(changed_pixels, axis=0)
    y0, y1 = int(np.argmax(rows)), int(len(rows) - 1 - np.argmax(rows[::-1]))
    x0, x1 = int(np.argmax(cols)), int(len(cols) - 1 - np.argmax(cols[::-1]))

    return DiffResult(
        changed_fraction=changed_count / total_pixels,
        max_channel_diff=int(diff.max()),
        changed_bbox=(x0, y0, x1 + 1, y1 + 1),
    )


def changed_outside(
    before: NDArray[np.uint8], after: NDArray[np.uint8], allowed: list[tuple[int, int, int, int]]
) -> float:
    """Fraction of all pixels that changed outside the `allowed` (x0, y0, x1, y1) pixel boxes --
    FNT-12's "flag anything outside the edit mask that changed"."""
    if before.shape != after.shape:
        raise ValueError(f"cannot diff renders of different shapes: {before.shape} vs {after.shape}")
    changed = np.any(before != after, axis=-1)
    for x0, y0, x1, y1 in allowed:
        changed[max(y0, 0) : max(y1, 0), max(x0, 0) : max(x1, 0)] = False
    return float(changed.sum()) / changed.size


def assert_pixel_similar(
    before: NDArray[np.uint8], after: NDArray[np.uint8], *, tolerance: float = DEFAULT_TOLERANCE
) -> DiffResult:
    """Raise AssertionError with a helpful message if the renders differ by more than `tolerance`."""
    result = pixel_diff(before, after)
    if result.changed_fraction > tolerance:
        raise AssertionError(
            f"renders differ in {result.changed_fraction:.4%} of pixels "
            f"(tolerance {tolerance:.4%}), bbox={result.changed_bbox}, "
            f"max channel diff={result.max_channel_diff}"
        )
    return result
