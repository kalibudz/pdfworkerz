"""SIG-01: visual signatures (drawn, typed, image), placed as ordinary page
content -- never an AcroForm signature field (that belongs to FRM-03/SIG-02).

Three ways to make one, one way to place it:

- **drawn**: one or more pen strokes (``strokes``), each a list of
  ``(x, y)`` points in an arbitrary local coordinate space (whatever a
  signature pad naturally produces -- no particular scale or origin is
  assumed). The strokes' own combined bounding box is fitted into `rect`,
  centered, aspect ratio kept, the same "fit and center" rule
  ``engine.images`` uses for a placed image.
- **typed**: a name (``text``) drawn in a script-style font, sized to fit
  `rect`. See ``TYPED_SIGNATURE_FONT`` below for which bundled font this
  resolves to today.
- **image**: an uploaded image, placed exactly like
  ``engine.images.insert_image`` (fit inside `rect`, aspect ratio kept,
  centered).

**Reuse (SIG-01's third criterion).** A signature is reused by the caller
simply sending the same `kind` + (`strokes` | `text`+`font` | `image_bytes`)
again with a different `rect`/`page_index` -- nothing here needs to be
re-drawn, re-typed or re-uploaded by the person to place it a second time,
which is what "without redrawing it from scratch" asks for: the *person*
never repeats the drawing/typing/upload step, only the placement. This
module does not add a separate server-side "signature asset" store (a named,
persisted record the caller looks up by ID): the Op's own parameters already
carry everything needed to place the same signature again, and the caller
(the web UI, the CLI's `--strokes-json`/`--text`/`--image` flags) is already
holding that data from the first placement, so keeping it client-side needs
no new persistence layer. A future session can add a named asset store on
top of this without changing `place_signature`'s own contract, if cross-session
reuse (closing and reopening the app) turns out to be wanted.
"""

from __future__ import annotations

import io
import itertools
from typing import Literal

import pymupdf
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict

from engine.document import Document
from engine.errors import OpValidationError
from engine.fonts.match import BUNDLED_FONTS_DIR
from engine.geometry import page_bounds
from engine.images import _PASSTHROUGH_FORMATS, MAX_IMAGE_BYTES

Point = tuple[float, float]
Rect = tuple[float, float, float, float]
SignatureKind = Literal["drawn", "typed", "image"]

# No cursive/script font ships with PDFWorkerz today (assets/fonts has Vera,
# Roboto, Open Sans and a Noto Sans CJK subset only -- confirmed by listing
# assets/fonts/ before writing this). Open Sans Italic is used as a
# placeholder "script-style" look (a slant reads less like a rubber stamp
# than an upright sans), not a real cursive signature face. Bundling an
# actual script font (e.g. a Google Fonts "Dancing Script"-style face) is a
# follow-up, not done here -- see the P6 session report.
TYPED_SIGNATURE_FONT = BUNDLED_FONTS_DIR / "OpenSans-Italic.ttf"
_MIN_STROKE_WIDTH = 0.75
_PEN_WIDTH_SHARE = 0.015  # of rect's shorter side -- a pen-like, not hairline, stroke


class PlaceResult(BaseModel):
    """Where and how one signature was drawn onto a page."""

    model_config = ConfigDict(frozen=True)

    page_index: int
    rect: Rect
    kind: SignatureKind


def _page(document: Document, page_index: int) -> pymupdf.Page:
    if not 0 <= page_index < document.page_count:
        raise OpValidationError(f"page_index {page_index} is out of range (document has {document.page_count} pages)")
    return document.raw[page_index]


def _validate_rect(page: pymupdf.Page, rect: Rect) -> pymupdf.Rect:
    area = pymupdf.Rect(rect)
    if area.is_empty or area.is_infinite:
        raise OpValidationError(f"signature rectangle {rect} is empty")
    if not area.intersects(page_bounds(page)):
        raise OpValidationError(f"signature rectangle {rect} lies entirely outside the page")
    return area


def _check_exactly_one(
    kind: SignatureKind,
    strokes: list[list[Point]] | None,
    text: str | None,
    image_bytes: bytes | None,
) -> None:
    given = {"drawn": strokes, "typed": text, "image": image_bytes}
    needed = given[kind]
    if not needed:
        raise OpValidationError(f"kind={kind!r} needs {'strokes' if kind == 'drawn' else kind}")
    others = {k: v for k, v in given.items() if k != kind}
    extra = [name for name, value in others.items() if value]
    if extra:
        raise OpValidationError(f"kind={kind!r} doesn't use {', '.join(extra)}; leave it unset")


def _fit_box(area: pymupdf.Rect, width: float, height: float) -> pymupdf.Rect:
    """`area` shrunk to a `width`x`height` box's aspect ratio, centered --
    the same rule as ``engine.images._fit``, generalized to a plain size
    instead of needing the image bytes to measure it from."""
    if width <= 0 or height <= 0:
        return area
    aspect = width / height
    box_width, box_height = area.width, area.height
    if box_width / box_height > aspect:
        box_width = box_height * aspect
    else:
        box_height = box_width / aspect
    x0 = area.x0 + (area.width - box_width) / 2
    y0 = area.y0 + (area.height - box_height) / 2
    return pymupdf.Rect(x0, y0, x0 + box_width, y0 + box_height)


def _draw_strokes(page: pymupdf.Page, area: pymupdf.Rect, strokes: list[list[Point]]) -> pymupdf.Rect:
    points = [pymupdf.Point(p) for stroke in strokes for p in stroke]
    if not points:
        raise OpValidationError("a drawn signature needs at least one point")
    xs, ys = [p.x for p in points], [p.y for p in points]
    source = pymupdf.Rect(min(xs), min(ys), max(xs), max(ys))
    if source.is_empty:  # a single point, or every stroke collapsed to one: pad it so it has a size
        source = pymupdf.Rect(source.x0 - 1, source.y0 - 1, source.x1 + 1, source.y1 + 1)
    target = _fit_box(area, source.width, source.height)
    sx = target.width / source.width
    sy = target.height / source.height

    def mapped(p: Point) -> pymupdf.Point:
        px, py = p
        return pymupdf.Point(target.x0 + (px - source.x0) * sx, target.y0 + (py - source.y0) * sy)

    pen_width = max(_MIN_STROKE_WIDTH, min(target.width, target.height) * _PEN_WIDTH_SHARE)
    shape = page.new_shape()
    for stroke in strokes:
        if len(stroke) == 1:  # a dot/tap: draw a tiny line so it still shows up as a mark
            p = mapped(stroke[0])
            shape.draw_line(p, p)
            continue
        mapped_points = [mapped(p) for p in stroke]
        for a, b in itertools.pairwise(mapped_points):
            shape.draw_line(a, b)
    shape.finish(color=(0.0, 0.0, 0.0), fill=None, width=pen_width, lineCap=1, lineJoin=1, closePath=False)
    shape.commit()
    return target


def _draw_typed(page: pymupdf.Page, area: pymupdf.Rect, text: str, font_path: str) -> None:
    label = text.strip()
    if not label:
        raise OpValidationError("a typed signature needs non-empty text")
    font = pymupdf.Font(fontfile=font_path)
    # Largest size (up to the box height) whose measured width still fits `area`.
    size = area.height * 0.8
    width = font.text_length(label, fontsize=size)
    if width > area.width:
        size *= area.width / width
    ascender, descender = font.ascender, font.descender
    text_height = (ascender - descender) * size
    baseline_y = area.y0 + (area.height + text_height) / 2 - ascender * size
    baseline_x = area.x0 + (area.width - font.text_length(label, fontsize=size)) / 2
    page.insert_text(
        pymupdf.Point(baseline_x, baseline_y),
        label,
        fontsize=size,
        fontfile=font_path,
        fontname="pw-sig-typed",
        color=(0.0, 0.0, 0.0),
    )


def _normalized_image(data: bytes) -> bytes:
    """`data` as bytes PyMuPDF can embed: PNG/JPEG unchanged, anything else
    Pillow reads re-encoded as PNG -- the same rule as
    :func:`engine.images.decode_image`, starting from raw bytes instead of
    base64 (the Op layer already decoded that before calling here)."""
    if len(data) > MAX_IMAGE_BYTES:
        raise OpValidationError(f"image is {len(data)} bytes; the limit is {MAX_IMAGE_BYTES}")
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            if image.format in _PASSTHROUGH_FORMATS:
                return data
            out = io.BytesIO()
            image.save(out, format="PNG")
            return out.getvalue()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as exc:
        raise OpValidationError(f"image data could not be read as an image: {exc}") from exc


def _draw_image(page: pymupdf.Page, area: pymupdf.Rect, image_bytes: bytes) -> pymupdf.Rect:
    stream = _normalized_image(image_bytes)
    with Image.open(io.BytesIO(stream)) as image:
        width, height = image.width, image.height
    box = _fit_box(area, width, height)
    page.insert_image(box, stream=stream, keep_proportion=False)
    return box


def place_signature(
    document: Document,
    page_index: int,
    rect: Rect,
    *,
    kind: SignatureKind,
    strokes: list[list[Point]] | None = None,
    text: str | None = None,
    font: str | None = None,
    image_bytes: bytes | None = None,
) -> PlaceResult:
    """Draw a signature as ordinary page content (a vector path, text, or an
    image -- never an AcroForm field) inside `rect`, fitted and centered.

    Exactly one of `strokes` (kind="drawn"), `text` (kind="typed") or
    `image_bytes` (kind="image") must be given, matching `kind`; anything
    else raises :class:`OpValidationError` rather than guessing which one
    was meant. `font` is accepted for the "typed" kind for forward
    compatibility with a future font-choice feature; today every typed
    signature uses :data:`TYPED_SIGNATURE_FONT` regardless of its value, since
    no second script-style font is bundled yet (see module docstring).

    The caller must not hold another live `pymupdf.Page` object for this same
    page across this call (like `engine.images`/`engine.shapes`, this reloads
    the page via `document.raw.reload_page()` once it's done drawing; a
    second outstanding reference to the same page makes PyMuPDF's own
    ref-counting assertion inside that reload fail with a raw, uncaught
    `AssertionError` instead of a clean `OpValidationError`). Not reachable
    from server/app.py today -- every Op re-fetches the page fresh and
    nothing holds a Page across a call -- but would matter for a future
    caller that renders a live preview from an already-open Page while
    editing the same one.
    """
    if kind not in ("drawn", "typed", "image"):
        raise OpValidationError(f"unknown signature kind {kind!r}: use drawn, typed or image")
    _check_exactly_one(kind, strokes, text, image_bytes)
    page = _page(document, page_index)
    area = _validate_rect(page, rect)

    if kind == "drawn":
        assert strokes is not None  # nosec B101 -- type narrowing: _check_exactly_one already required this
        drawn_box = _draw_strokes(page, area, strokes)
    elif kind == "typed":
        assert text is not None  # nosec B101 -- type narrowing: _check_exactly_one already required this
        _draw_typed(page, area, text, str(TYPED_SIGNATURE_FONT))
        drawn_box = area  # text isn't letterboxed to an aspect ratio; it fills the given area
    else:
        assert image_bytes is not None  # nosec B101 -- type narrowing: _check_exactly_one already required this
        drawn_box = _draw_image(page, area, image_bytes)

    document.raw.reload_page(page)  # see docstring: the caller must hold no other live Page for this page
    placed: Rect = (drawn_box.x0, drawn_box.y0, drawn_box.x1, drawn_box.y1)
    return PlaceResult(page_index=page_index, rect=placed, kind=kind)
