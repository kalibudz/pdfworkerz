"""SEC-08/09/11: true redaction -- removing covered content, not drawing over it.

"True redaction" means the covered content is genuinely unrecoverable, not merely
hidden by a later layer: for text, the actual glyph-showing operators are removed from
the content stream (reusing engine.edit's existing per-glyph redaction primitives,
``_redact_chars``/``_redact_spans`` -- the same machinery EDT-01..EDT-07's edits use to
remove the text they replace); for an image fully covered by the redaction area, the
image XObject placement is removed outright (engine.images.delete_image); for an image
only partly covered, the pixels under the covered sub-rectangle are blacked out and the
XObject is replaced, so cropping the saved image can't recover them; for a vector path,
a path fully covered is removed (engine.shapes.delete_shape), and a path only partly
covered is clipped to its uncovered portion when that is a simple line or axis-aligned
rectangle (the only two cases this module knows how to split into new, equivalent-looking
paths without a general polygon-clipping library) -- anything else (a curve, a multi-
segment polyline, a polygon) that is only partly covered is removed in full rather than
left partly "redacted" while still showing some of its original shape. This is a
deliberate simplification, not an oversight: see ``_redact_shape`` and SEC-08's test
suite, and the report that shipped this module, for exactly which cases fall back to
full removal.

No black box is drawn back over a redacted area automatically -- SEC-08's job is pure
removal. A caller that wants a visible mark can draw one afterwards with engine.shapes'
own ``draw_shape``, as a separate, ordinary step.

SEC-09's pattern packs (``find_pattern_matches``) locate candidate rectangles (email,
phone, SSN, credit-card, or a custom regex) without removing anything -- a caller reviews
the list and only then redacts the accepted ones via ``redact_areas``, in one call (one
undo step).

SEC-11's verification (``verify_redaction``) re-serializes the document exactly as a save
would and re-opens that byte stream fresh (the same ``plain_bytes``-based round trip
every other read in this codebase uses to look at "the saved file"), then re-extracts
text, images and vector paths over the redacted rectangles and fails loudly -- it never
rubber-stamps a redaction that left recoverable content behind. ``redact_areas`` calls it
automatically and raises :class:`~engine.errors.RedactionVerificationError` if it finds
anything.
"""

from __future__ import annotations

import io
import itertools
import re
from dataclasses import dataclass, field
from typing import Literal

import pymupdf
from PIL import Image, ImageDraw

from engine.contentstream import protect_text_line_moves
from engine.document import Document
from engine.edit import _redact_chars
from engine.errors import OpValidationError, RedactionVerificationError
from engine.fonts.style import CharBox, SpanTrace, extract_page_spans
from engine.geometry import page_bounds
from engine.images import ImageInfo, delete_image, list_images
from engine.shapes import ShapeInfo, delete_shape, draw_shape, list_shapes
from engine.verify import DiffResult, changed_outside, pixel_diff, render_to_array

Rect = tuple[float, float, float, float]

VERIFY_DPI = 150
_AA_MARGIN_PX = 2  # anti-aliasing spill around a redacted box, at VERIFY_DPI; see engine.edit
_MAX_OBJECTS_PER_RECT = 500
"""A hard cap on images/shapes processed per rect, purely a defense-in-depth backstop
against a future change reintroducing an infinite "still overlaps, reprocess forever"
loop (the real fix is each loop's own ``handled`` set -- see ``_process_rect``); no
real page should ever have this many overlapping objects in one redaction area."""

# -- SEC-09: pattern packs ---------------------------------------------------------

PatternName = Literal["email", "phone", "ssn", "credit_card"]

# These are intentionally ordinary, widely used patterns -- not an exhaustive validator
# (a regex can't actually validate a card's Luhn check digit or a phone's area code),
# only a "this looks like one" finder, which is what a redaction candidate needs to be.
_EMAIL_RE = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
_PHONE_RE = r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"
# Dashed (123-45-6789) is the canonical form, but a plain 9-digit run or a
# space-separated one are just as common on real forms -- found by independent
# review: the dash-only version silently missed both. Accepting a bare 9-digit
# run does mean more false positives against other unrelated 9-digit numbers
# (an account or invoice number, say), which is an acceptable trade-off for a
# "this looks like one, review it" candidate finder (see the module note
# above) rather than a validator.
_SSN_RE = r"\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b"
# Visa / Mastercard / Amex / Discover prefixes, 13-16 digits, optionally grouped by
# spaces or hyphens into the common 4-4-4(-4) or 4-6-5 layouts.
_CREDIT_CARD_RE = r"\b(?:4\d{3}|5[1-5]\d{2}|3[47]\d{2}|6(?:011|5\d{2}))[- ]?\d{2,6}[- ]?\d{4,6}[- ]?\d{0,4}\b"

_BUILTIN_PATTERNS: dict[PatternName, re.Pattern[str]] = {
    "email": re.compile(_EMAIL_RE),
    "phone": re.compile(_PHONE_RE),
    "ssn": re.compile(_SSN_RE),
    "credit_card": re.compile(_CREDIT_CARD_RE),
}


@dataclass(frozen=True)
class PatternMatch:
    """One proposed redaction area, found by a pattern pack. Nothing is removed by
    finding one -- see SEC-09's two-step find-then-apply flow in engine.ops.redact."""

    page_index: int
    span_index: int
    start: int
    """Character offset of the match within its span's text."""
    end: int
    text: str
    rect: Rect


def _compile_pattern(pattern: str) -> re.Pattern[str]:
    if pattern in _BUILTIN_PATTERNS:
        return _BUILTIN_PATTERNS[pattern]
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise OpValidationError(f"invalid regex {pattern!r}: {exc}") from exc


def find_pattern_matches(document: Document, page_index: int, pattern: str) -> list[PatternMatch]:
    """SEC-09: every match of `pattern` (one of "email", "phone", "ssn", "credit_card",
    or any custom regex) on one page, as reviewable candidates -- read-only, like
    engine.spellcheck's check_page, and using the same per-character bbox technique
    (a match's character range mapped back to a box through its span's ``chars``).

    A match that crosses a span boundary (an email split across two differently
    styled runs, say) is not found -- each span's own text is searched independently,
    the same simplification engine.spellcheck makes for the same reason."""
    if not 0 <= page_index < document.page_count:
        raise OpValidationError(f"page_index {page_index} is out of range (document has {document.page_count} pages)")
    regex = _compile_pattern(pattern)
    matches: list[PatternMatch] = []
    for span in extract_page_spans(document.raw, page_index):
        text = span.style.text
        for match in regex.finditer(text):
            if match.start() == match.end():
                continue
            boxes = [char.bbox for char in span.style.chars[match.start() : match.end()]]
            if not boxes:
                continue
            rect = (
                min(b[0] for b in boxes),
                min(b[1] for b in boxes),
                max(b[2] for b in boxes),
                max(b[3] for b in boxes),
            )
            matches.append(
                PatternMatch(
                    page_index=page_index,
                    span_index=span.style.span_index,
                    start=match.start(),
                    end=match.end(),
                    text=match.group(),
                    rect=rect,
                )
            )
    return matches


# -- SEC-08: true redaction ---------------------------------------------------------


@dataclass(frozen=True)
class RectStats:
    rect: Rect
    glyphs_removed: int = 0
    images_removed: int = 0
    images_altered: int = 0
    shapes_removed: int = 0
    shapes_clipped: int = 0


@dataclass(frozen=True)
class RedactionResult:
    """What SEC-08's redaction did, and SEC-11's proof that it really worked."""

    stats: list[RectStats]
    diff: DiffResult
    outside_changed_fraction: float
    """Fraction of the page's pixels that changed outside every redacted rect (SEC-08
    criterion 4: "redacting one area never alters pixels outside it")."""
    verification: RedactionVerification


def _page(document: Document, page_index: int) -> pymupdf.Page:
    if not 0 <= page_index < document.page_count:
        raise OpValidationError(f"page_index {page_index} is out of range (document has {document.page_count} pages)")
    return document.raw[page_index]


def _validate_rect(page: pymupdf.Page, rect: Rect) -> pymupdf.Rect:
    area = pymupdf.Rect(rect).normalize()
    if area.is_empty or area.is_infinite:
        raise OpValidationError(f"redaction rectangle {rect} is empty")
    if not area.intersects(page_bounds(page)):
        raise OpValidationError(f"redaction rectangle {rect} lies entirely outside the page")
    return area


def _overlaps(a: pymupdf.Rect, b: pymupdf.Rect) -> bool:
    """Whether `a` and `b` share any area or boundary -- unlike
    ``pymupdf.Rect.intersects()``, which treats a degenerate (zero-width or
    zero-height) rect as empty and always reports False for it, even lying exactly on
    top of the other rect. A straight horizontal or vertical line's own bounding box
    (``get_drawings()``'s ``rect``) is always degenerate like this, so every overlap
    test in this module uses this helper instead -- confirmed against pymupdf 1.28.2:
    a 2pt-wide horizontal line's own rect is still reported with zero height."""
    return not (a.x1 < b.x0 or a.x0 > b.x1 or a.y1 < b.y0 or a.y0 > b.y1)


def _chars_in_rect(spans: list[SpanTrace], rect: pymupdf.Rect) -> list[CharBox]:
    """Every glyph whose own box genuinely overlaps `rect` (real shared area, not
    merely touching its edge) -- so a redaction box drawn to just cover a word's ink
    also covers glyphs whose advance-width padding pokes slightly past it, but a
    neighbouring space character is not swept in too just because its own box happens
    to end exactly on the rect's boundary (confirmed: PyMuPDF's texttrace reports
    consecutive glyphs' boxes sharing an edge with no gap). ``pymupdf.Rect.intersects``
    -- not the module's own, deliberately touch-inclusive ``_overlaps`` (see its
    docstring) -- is the right test here for exactly that reason. ``_redact_chars``
    itself still only ever removes the exact glyphs it's given (SEC-08 criterion 3)."""
    return [char for span in spans for char in span.style.chars if pymupdf.Rect(char.bbox).intersects(rect)]


def _image_png_bytes(document: Document, xref: int) -> bytes:
    """The image XObject's own pixels as PNG, alpha (SMask) included -- the same
    technique engine.images' (private) placement-bytes helper uses, duplicated here
    rather than imported so this module never needs true redaction to depend on an
    images.py function that could change its contract for a different purpose."""
    pixmap = pymupdf.Pixmap(document.raw, xref)
    smask = document.raw.xref_get_key(xref, "SMask")
    if smask[0] == "xref":
        pixmap = pymupdf.Pixmap(pixmap, pymupdf.Pixmap(document.raw, int(smask[1].split()[0])))
    if pixmap.colorspace and pixmap.colorspace.n not in (1, 3):
        pixmap = pymupdf.Pixmap(pymupdf.csRGB, pixmap)
    data: bytes = pixmap.tobytes("png")
    return data


def _blacken_region(png_bytes: bytes, image_rect: pymupdf.Rect, covered: pymupdf.Rect) -> bytes:
    """`png_bytes` with the sub-rectangle of `covered` (in the same page-point space as
    `image_rect`, the image's own placement) painted solid black, mapped into pixel space
    by the image's own pixel-to-point scale."""
    with Image.open(io.BytesIO(png_bytes)) as opened:
        opened.load()
        image: Image.Image = opened
        if image.mode not in ("RGB", "RGBA", "L"):
            image = image.convert("RGBA" if "A" in image.mode else "RGB")
        sx = image.width / image_rect.width if image_rect.width else 1.0
        sy = image.height / image_rect.height if image_rect.height else 1.0
        box = (
            max(0, round((covered.x0 - image_rect.x0) * sx)),
            max(0, round((covered.y0 - image_rect.y0) * sy)),
            min(image.width, round((covered.x1 - image_rect.x0) * sx)),
            min(image.height, round((covered.y1 - image_rect.y0) * sy)),
        )
        fill: tuple[int, ...] | int
        if image.mode == "RGBA":
            fill = (0, 0, 0, 255)
        elif image.mode == "RGB":
            fill = (0, 0, 0)
        else:
            fill = 0
        ImageDraw.Draw(image).rectangle(box, fill=fill)
        out = io.BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()


def _redact_image(
    document: Document, page_index: int, info: ImageInfo, rect: pymupdf.Rect
) -> Literal["removed", "altered"]:
    """SEC-08: fully covered -> remove the placement; partly covered -> black out the
    covered pixels in the image's own data and redraw it in exactly the same place.

    A rotated or skewed placement, or an inline image (no reusable xref), can't have its
    pixel data reliably remapped back into page space here, so those are removed outright
    when only partly covered -- a documented simplification, not a partial redaction that
    silently leaves some of the image showing without actually touching its pixels."""
    img_rect = pymupdf.Rect(info.rect)
    if rect.contains(img_rect):
        delete_image(document, page_index, info.index)
        return "removed"
    if not info.axis_aligned or not info.xref:
        delete_image(document, page_index, info.index)
        return "removed"
    covered = rect & img_rect
    new_png = _blacken_region(_image_png_bytes(document, info.xref), img_rect, covered)
    delete_image(document, page_index, info.index)
    page = document.raw[page_index]
    # keep_proportion=False, at the image's own original rect (not re-fitted to its
    # aspect ratio): the placement's exact geometry must not change, only its pixels.
    page.insert_image(img_rect, stream=new_png, keep_proportion=False)
    document.raw.reload_page(page)
    return "altered"


def _rect_difference(outer: pymupdf.Rect, hole: pymupdf.Rect) -> list[pymupdf.Rect]:
    """`outer` minus `hole` (both axis-aligned), as up to four axis-aligned strips that
    exactly tile the remaining L/U/ring-shaped area. Each strip becomes its own small
    rectangle shape once redrawn -- a documented cosmetic simplification: the original
    rectangle's single, continuous border becomes several shorter borders instead,
    because that's what it takes to truly remove the covered part."""
    hole = outer & hole
    pieces = []
    if hole.y0 > outer.y0:
        pieces.append(pymupdf.Rect(outer.x0, outer.y0, outer.x1, hole.y0))
    if hole.y1 < outer.y1:
        pieces.append(pymupdf.Rect(outer.x0, hole.y1, outer.x1, outer.y1))
    if hole.x0 > outer.x0:
        pieces.append(pymupdf.Rect(outer.x0, max(outer.y0, hole.y0), hole.x0, min(outer.y1, hole.y1)))
    if hole.x1 < outer.x1:
        pieces.append(pymupdf.Rect(hole.x1, max(outer.y0, hole.y0), outer.x1, min(outer.y1, hole.y1)))
    return [p for p in pieces if p.width > 1e-6 and p.height > 1e-6]


def _clip_segment(
    p0: pymupdf.Point, p1: pymupdf.Point, rect: pymupdf.Rect
) -> list[tuple[pymupdf.Point, pymupdf.Point]]:
    """The portion(s) of segment `p0`-`p1` that lie *outside* `rect`: zero, one or two
    sub-segments (a line crossing clean through the middle of the box leaves two)."""
    steps = sorted({0.0, 1.0} | set(_segment_rect_crossings(p0, p1, rect)))
    kept: list[tuple[pymupdf.Point, pymupdf.Point]] = []
    for t0, t1 in itertools.pairwise(steps):
        mid = pymupdf.Point(p0.x + (p1.x - p0.x) * (t0 + t1) / 2, p0.y + (p1.y - p0.y) * (t0 + t1) / 2)
        if mid not in rect:
            a = pymupdf.Point(p0.x + (p1.x - p0.x) * t0, p0.y + (p1.y - p0.y) * t0)
            b = pymupdf.Point(p0.x + (p1.x - p0.x) * t1, p0.y + (p1.y - p0.y) * t1)
            kept.append((a, b))
    return kept


def _segment_rect_crossings(p0: pymupdf.Point, p1: pymupdf.Point, rect: pymupdf.Rect) -> list[float]:
    """Parametric t in (0, 1) where the segment crosses one of `rect`'s four edges."""
    dx, dy = p1.x - p0.x, p1.y - p0.y
    ts: list[float] = []
    for edge_value, is_x in ((rect.x0, True), (rect.x1, True), (rect.y0, False), (rect.y1, False)):
        denom = dx if is_x else dy
        if abs(denom) < 1e-9:
            continue
        t = (edge_value - (p0.x if is_x else p0.y)) / denom
        if 0.0 < t < 1.0:
            other = (p0.y + dy * t) if is_x else (p0.x + dx * t)
            lo, hi = (rect.y0, rect.y1) if is_x else (rect.x0, rect.x1)
            if lo - 1e-9 <= other <= hi + 1e-9:
                ts.append(t)
    return ts


def _redact_shape(
    document: Document, page_index: int, info: ShapeInfo, rect: pymupdf.Rect
) -> tuple[Literal["removed", "clipped"], list[pymupdf.Rect]]:
    """SEC-08: fully covered -> remove; a simple line or axis-aligned rectangle only
    partly covered -> clip to its uncovered portion(s), redrawn as new shapes with the
    same stroke/fill/width; anything else only partly covered (a curve, a multi-segment
    polyline, a polygon) is removed outright -- true per-path geometric clipping for an
    arbitrary path is out of scope for this pass; see the module docstring.

    Returns the outcome and the rects of any newly drawn replacement pieces: a clipped
    piece sits exactly on `rect`'s own boundary by construction (that's where it was
    cut), so the caller must not treat it as still needing redaction just because it
    touches that boundary -- see ``_process_rect``'s ``handled`` set."""
    page = document.raw[page_index]
    drawings = list(page.get_drawings())
    path = drawings[info.index]
    shape_rect = pymupdf.Rect(info.rect)
    del page  # a live reference here would make pymupdf's own reload_page assert, below
    if rect.contains(shape_rect):
        delete_shape(document, page_index, info.index)
        return "removed", []

    items = path["items"]
    if info.kind == "line" and len(items) == 1 and items[0][0] == "l":
        p0, p1 = pymupdf.Point(items[0][1]), pymupdf.Point(items[0][2])
        kept = _clip_segment(p0, p1, rect)
        delete_shape(document, page_index, info.index)
        new_rects = []
        for a, b in kept:
            drawn = draw_shape(
                document,
                page_index,
                "line",
                [(a.x, a.y), (b.x, b.y)],
                stroke_color=info.stroke_color,
                fill_color=None,
                line_width=info.line_width or 1.0,
            )
            new_rects.append(pymupdf.Rect(drawn.rect))
        return "clipped", new_rects

    if info.kind == "rect" and len(items) == 1 and items[0][0] == "re":
        pieces = _rect_difference(shape_rect, rect)
        delete_shape(document, page_index, info.index)
        new_rects = []
        for piece in pieces:
            drawn = draw_shape(
                document,
                page_index,
                "rect",
                [(piece.x0, piece.y0), (piece.x1, piece.y1)],
                stroke_color=info.stroke_color,
                fill_color=info.fill_color,
                line_width=info.line_width or 1.0,
            )
            new_rects.append(pymupdf.Rect(drawn.rect))
        return "clipped", new_rects

    delete_shape(document, page_index, info.index)
    return "removed", []


def _process_rect(document: Document, page_index: int, rect: pymupdf.Rect) -> RectStats:
    page = document.raw[page_index]
    protect_text_line_moves(page)

    chars = _chars_in_rect(extract_page_spans(document.raw, page_index), rect)
    if chars:
        _redact_chars(page, chars)
    del page  # a live reference here would make pymupdf's own reload_page assert, below
    # (images.delete_image / shapes.delete_shape each reload the page internally)

    def _key(r: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        return tuple(round(v, 3) for v in r)  # type: ignore[return-value]

    images_removed = images_altered = 0
    handled_images: set[tuple[float, float, float, float]] = set()
    for _iteration in range(_MAX_OBJECTS_PER_RECT + 1):
        target = next(
            (
                info
                for info in list_images(document, page_index)
                if _overlaps(pymupdf.Rect(info.rect), rect) and _key(info.rect) not in handled_images
            ),
            None,
        )
        if target is None:
            break
        if _iteration == _MAX_OBJECTS_PER_RECT:
            raise OpValidationError("redact_areas: too many overlapping images to process for one rect")
        outcome = _redact_image(document, page_index, target, rect)
        if outcome == "removed":
            images_removed += 1
        else:
            # An altered image keeps its exact original rect (only its pixels changed),
            # so it would still "overlap" rect forever without this: mark it done.
            images_altered += 1
            handled_images.add(_key(target.rect))

    shapes_removed = shapes_clipped = 0
    handled_shapes: set[tuple[float, float, float, float]] = set()
    for _iteration in range(_MAX_OBJECTS_PER_RECT + 1):
        shape_target = next(
            (
                info
                for info in list_shapes(document, page_index)
                if _overlaps(pymupdf.Rect(info.rect), rect) and _key(info.rect) not in handled_shapes
            ),
            None,
        )
        if shape_target is None:
            break
        if _iteration == _MAX_OBJECTS_PER_RECT:
            raise OpValidationError("redact_areas: too many overlapping shapes to process for one rect")
        shape_outcome, new_rects = _redact_shape(document, page_index, shape_target, rect)
        if shape_outcome == "removed":
            shapes_removed += 1
        else:
            # A clipped piece sits exactly on rect's own boundary by construction, which
            # the (deliberately touch-inclusive) overlap test above would otherwise keep
            # matching forever: mark every newly drawn piece done.
            shapes_clipped += 1
            for new_rect in new_rects:
                handled_shapes.add(_key((new_rect.x0, new_rect.y0, new_rect.x1, new_rect.y1)))

    return RectStats(
        rect=(rect.x0, rect.y0, rect.x1, rect.y1),
        glyphs_removed=len(chars),
        images_removed=images_removed,
        images_altered=images_altered,
        shapes_removed=shapes_removed,
        shapes_clipped=shapes_clipped,
    )


def _allowed_boxes(page: pymupdf.Page, rects: list[pymupdf.Rect]) -> list[tuple[int, int, int, int]]:
    """Pixel boxes (at VERIFY_DPI) where a pixel change is expected: the redacted
    rects themselves, grown by an anti-aliasing margin exactly as engine.edit's own
    after-edit verification grows an edit's box. A partly covered image or shape can
    change pixels slightly outside its own redaction rect only within its own
    placement box, which engine.images/engine.shapes already guarantee stays put --
    the redaction rect passed in here is the caller's own, user-drawn area, which by
    definition is meant to cover whatever it was drawn around."""
    scale = VERIFY_DPI / 72
    allowed = []
    for box in rects:
        shown = box * page.rotation_matrix
        allowed.append(
            (
                int(shown.x0 * scale) - _AA_MARGIN_PX,
                int(shown.y0 * scale) - _AA_MARGIN_PX,
                int(shown.x1 * scale) + _AA_MARGIN_PX + 1,
                int(shown.y1 * scale) + _AA_MARGIN_PX + 1,
            )
        )
    return allowed


def redact_areas(document: Document, page_index: int, rects: list[Rect], *, verify: bool = True) -> RedactionResult:
    """SEC-08: remove every glyph, image pixel and vector path inside each of `rects`
    on `page_index` -- true removal, never a box drawn on top (see the module
    docstring). Runs SEC-11's ``verify_redaction`` automatically afterwards and raises
    :class:`~engine.errors.RedactionVerificationError` if anything redacted is still
    recoverable -- this never silently "succeeds" on a redaction that didn't really
    remove what it was asked to."""
    if not rects:
        raise OpValidationError("redact_areas: no rectangles given")
    page = _page(document, page_index)
    page_rects = [_validate_rect(page, rect) for rect in rects]
    del page  # a live reference here would make pymupdf's own reload_page assert, inside _process_rect

    before = render_to_array(document.raw, page_index, dpi=VERIFY_DPI) if verify else None
    stats = [_process_rect(document, page_index, rect) for rect in page_rects]

    verification = verify_redaction(document, page_index, rects)
    if not verification.ok:
        raise RedactionVerificationError("redaction verification failed: " + "; ".join(verification.problems))

    if before is None:
        diff = DiffResult(changed_fraction=0.0, max_channel_diff=0, changed_bbox=None)
        outside_fraction = 0.0
    else:
        after = render_to_array(document.raw, page_index, dpi=VERIFY_DPI)
        diff = pixel_diff(before, after)
        page = document.raw[page_index]
        allowed = _allowed_boxes(page, page_rects)
        outside_fraction = changed_outside(before, after, allowed)

    return RedactionResult(stats=stats, diff=diff, outside_changed_fraction=outside_fraction, verification=verification)


# -- SEC-11: redaction verification --------------------------------------------------


@dataclass(frozen=True)
class RedactionVerification:
    ok: bool
    problems: list[str] = field(default_factory=list)


def verify_redaction(document: Document, page_index: int, rects: list[Rect]) -> RedactionVerification:
    """SEC-11: re-serialize `document` exactly as a save would (the same ``to_bytes()``
    every other read in this codebase treats as "the file"), re-open that byte stream
    fresh, and confirm nothing inside any of `rects` on `page_index` is still
    extractable or re-drawable:

    - no glyph (checked per character, via texttrace -- which is itself derived from
      replaying the page's content stream, Form XObjects included, so text drawn
      through a form is covered the same as page-level text) has a box touching a
      redacted rect;
    - no image placement fully inside a redacted rect is still drawn there (a
      partly-covered image that was pixel-redacted, not removed, is expected to still
      be *placed* there -- only its *content* changed, so it is not flagged);
    - no vector path fully inside a redacted rect is still drawn there, for the same
      reason.

    This never rubber-stamps: an unredacted black box drawn merely on top of live text
    fails it (the text is still there, however it's covered), and it raises nothing of
    its own -- callers (redact_areas) decide what a failed result means.

    Limitation: an Image or Form XObject present in the page's /Resources but never
    invoked by a ``Do`` anywhere (truly orphaned, not drawn at all) has no geometry to
    compare against a rect and can't be caught by a check that works in page-rendering
    space; such an object is not visually recoverable by anything that renders the
    page, but a tool that reads the raw PDF object stream directly could still find its
    bytes. Catching that case exhaustively needs a full object-reachability audit
    across the whole file, not just the redacted page, and is out of scope here.
    """
    data = document.to_bytes()
    problems: list[str] = []
    rect_objs = [pymupdf.Rect(r) for r in rects]

    reopened = pymupdf.open(stream=data, filetype="pdf")
    try:
        if not 0 <= page_index < reopened.page_count:
            return RedactionVerification(ok=False, problems=[f"page {page_index} does not exist in the saved file"])
        page = reopened[page_index]

        for span in extract_page_spans(reopened, page_index):
            for char in span.style.chars:
                if char.char.isspace():
                    continue
                box = pymupdf.Rect(char.bbox)
                if any(box.intersects(r) for r in rect_objs):
                    problems.append(
                        f"page {page_index}: glyph {char.char!r} at {tuple(round(v, 1) for v in char.bbox)} "
                        "is still present inside a redacted area"
                    )

        for info in page.get_image_info(xrefs=True):
            box = pymupdf.Rect(info["bbox"])
            if any(r.contains(box) for r in rect_objs):
                problems.append(f"page {page_index}: an image at {info['bbox']} is still fully inside a redacted area")

        for path in page.get_drawings():
            box = pymupdf.Rect(path["rect"])
            if any(r.contains(box) for r in rect_objs):
                problems.append(
                    f"page {page_index}: a vector path at {path['rect']} is still fully inside a redacted area"
                )
    finally:
        reopened.close()

    return RedactionVerification(ok=not problems, problems=problems)
