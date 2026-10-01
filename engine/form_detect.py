"""FRM-04: auto-detect likely form fields on a flat (non-interactive) page --
a page with no AcroForm at all, just the visual cues a human filling it out by
hand would use: a horizontal line/underscore to write a name on, a small empty
box to tick for "yes". Detection is **read-only** (:func:`detect_form_fields` /
:class:`DetectFormFieldsOp`): it never creates anything by itself. Turning an
accepted proposal into a real AcroForm field is a second, explicit step
(:class:`~engine.ops.forms.CreateDetectedFieldsOp`), built on FRM-03's own
``create_field`` -- see that Op's own docstring for why this is a separate Op
rather than folding creation into detection itself.

Algorithm, and why (verified empirically against this module's own test
fixtures in ``tests/engine/test_form_detect.py`` before settling on it -- see
that file for the false-positive corpus this was tuned against):

- The page is rendered to a **grayscale** pixmap at `dpi` (``page.get_pixmap``,
  confirmed in a REPL check: ``colorspace=pymupdf.csGRAY`` gives a single-
  channel buffer; ``pix.samples`` is read into a numpy array via
  ``np.frombuffer(...).reshape(height, stride)[:, :width]`` since a pixmap's
  stride can exceed its width).
- **Lines** (candidate text-field underscores): ``cv2.Canny`` then
  ``cv2.HoughLinesP`` (confirmed against the installed opencv-python-headless
  5.0.0.93: ``HoughLinesP`` returns an ``(N, 4)`` array of ``[x1, y1, x2, y2]``
  rows here, *not* the ``(N, 1, 4)`` shape older OpenCV docs describe -- this
  module iterates it as plain 4-tuples, which works either way since numpy
  unpacks a trailing length-4 axis the same way). Kept only if near-horizontal
  (``|y2-y1| <= 2`` px) and at least `_MIN_LINE_LENGTH_PT` long.
- **Boxes** (candidate checkboxes): the page is also binarized
  (``cv2.threshold(..., cv2.THRESH_BINARY_INV)``) and its **external**
  contours taken (``cv2.findContours(..., cv2.RETR_EXTERNAL, ...)`` --
  ``RETR_EXTERNAL`` rather than ``RETR_LIST``, confirmed in a REPL check, to
  avoid a hollow rectangle's inner hole being reported as a second, nested
  near-duplicate contour of the same box). Kept only if roughly square
  (aspect ratio 0.6-1.6), a plausible checkbox size (`_MIN_BOX_PT` to
  `_MAX_BOX_PT`), and not spanning most of the page (excludes a table's own
  outer frame).
- **Rotated pages**: ``page.get_pixmap`` (and therefore every pixel coordinate
  this module works in, including the rendered line/box candidates and the
  final ``FieldProposal.rect``) already honors the page's own ``/Rotate`` --
  confirmed in a REPL check, a 90-degree-rotated page's pixmap comes out
  width/height-swapped, matching the rotated display frame, and a field
  created at a rect in that same frame (via ``create_field``/``page.rect``)
  round-trips correctly through ``list_fields``. ``extract_page_spans``
  (``page.get_texttrace()``), by contrast, reports every span's and
  character's bbox in the page's **un**rotated content-stream space --
  confirmed empirically to *not* reflect ``/Rotate`` at all. Comparing those
  two coordinate systems directly (as an earlier version of this module did)
  silently broke both the text-overlap false-positive guard and the label
  search on any rotated page. Every span/character bbox used here is now
  first transformed by ``page.rotation_matrix`` (identity when ``/Rotate`` is
  0, confirmed) into the same display/rotated frame the pixel candidates and
  `FieldProposal.rect` already use, before any comparison -- see
  ``_rotate_spans``/``_rotate_char_boxes``.
- **The key false-positive guard, for both cues**: a candidate is discarded if
  it substantially overlaps the page's own *embedded text* -- each span's
  per-character boxes, from :func:`engine.fonts.style.extract_page_spans`
  (reused rather than hand-rolling OCR: a flat form made by a real authoring
  tool has its labels as ordinary embedded text, not scanned pixels). This one
  check is what keeps an ordinary paragraph of justified text from reading as
  dozens of spurious "lines" -- confirmed empirically: ``cv2.HoughLinesP`` on
  a plain text page finds ~70 near-horizontal segments (word/letter edges that
  happen to align), every one of them >90% covered by a character's own
  bbox, and 0 survive this filter; the same filter also keeps a letter like
  "o" or "D" from being mistaken for a checkbox.
- **Table/grid guard**: surviving line candidates are merged (nearby,
  overlapping-x duplicates -- a drawn line's top and bottom stroke edges both
  trigger Hough independently) and then bucketed by their (start, end) x
  range; a bucket with `_TABLE_BUCKET_MIN` or more lines sharing the same
  column span is a table's repeated row/cell borders, not one field's own
  underscore, and the whole bucket is dropped. A lone field's line has no
  such repetition. (Checkboxes get the same ``_MAX_BOX_PT``/page-fraction
  guards above; a real table's cells in this module's own test fixture are
  far larger than a plausible checkbox, so no additional grid guard was
  needed there empirically -- see the test file for the corpus this was
  checked against.)
- **Labelling**: the nearest text span (already extracted, in PDF point
  space, not pixel space) is looked up -- to the candidate's *left* for a
  line (the field's own label, e.g. "Name:"), or to its *right* then *above*
  for a box (e.g. "[ ] Subscribe"). That span's text becomes `label_text`;
  `suggested_name` is a slugified, page-unique version of it (falling back
  to a generic ``text_field``/``checkbox`` name when no label is found).

Limitations (deliberately documented, not hidden): this is a heuristic over
rendered pixels, not a form-structure parser, so it inherits every limit that
implies --

- Only the two cue types the tracker's acceptance criteria actually ask for
  are detected: a line/underscore proposes a **text** field, a small box
  proposes a **checkbox**. Radio buttons, dropdowns and listboxes have no
  reliable *visual-only* cue (a form author draws them however they like)
  and are out of scope here, same as the tracker criteria.
- A line/underscore built out of **text** characters (e.g. a run of literal
  "_" glyphs, rather than a vector-drawn rule) is excluded by the very same
  text-overlap guard that suppresses false positives -- the guard cannot
  distinguish "this is a run of underscore glyphs, which is exactly the cue
  I want" from "this is a sentence of ordinary prose, which isn't". Only a
  **vector-drawn** line (``page.draw_line``, or any content-stream stroke
  that doesn't correlate with a text-showing operator) is detected.
- A checkbox cue needs enough blank margin around it to read as "not part of
  running text" and "not part of a dense grid"; packed into a tight table of
  same-sized cells, or drawn flush against its own label with no gap, either
  cue type can be missed (a false negative) rather than mis-detected.
- **Rotation and line orientation**: a horizontal line/underscore cue is only
  detected if it is still near-horizontal in the page's own *display*
  orientation (after ``/Rotate`` is applied) -- the same orientation a person
  viewing the rendered page would see. A page rotated 180 degrees keeps a
  horizontal line horizontal (confirmed empirically), so it is detected
  exactly as at 0 degrees; a page rotated 90 or 270 degrees turns what was a
  horizontal line in the page's raw content stream into a vertical mark on
  screen, which is correctly *not* proposed as a text-field line -- it
  genuinely is not a horizontal cue once rendered. This is an inherent
  consequence of detecting *visual* cues from rendered pixels (the same
  pixels a human reviewing the page would judge it by), not a coordinate bug:
  the false-positive-guard coordinate mismatch this module used to have on
  rotated pages (phantom checkbox proposals near a rotated label's own
  glyphs) is fixed (see "Rotated pages" above and
  ``tests/engine/test_form_detect.py``'s rotation regression tests); a
  checkbox cue, being orientation-agnostic (still roughly square after any
  90-degree-multiple rotation), is detected the same at every rotation.
- Confidence is a simple two-level heuristic (a found label vs. none), not a
  calibrated probability -- it is meant to rank proposals for review, not to
  stand in for a human glancing at the page before accepting anything.
"""

from __future__ import annotations

import re
from typing import Literal, NamedTuple

import cv2
import numpy as np
import pymupdf
from pydantic import BaseModel, ConfigDict

from engine.document import Document
from engine.fonts.style import SpanTrace, extract_page_spans
from engine.forms import list_fields

Rect = tuple[float, float, float, float]

_MIN_LINE_LENGTH_PT = 20.0
"""A line shorter than this is more likely a stray stroke/underline artifact
than a usable text-field cue."""
_MAX_LINE_FRACTION_OF_PAGE = 0.85
"""A near-horizontal line spanning more than this fraction of the page's own
width is a page rule or a table/frame border, not one field's own line."""
_TEXT_OVERLAP_MAX = 0.25
"""A candidate line more than this fraction covered (at its own y, across its
own x-span) by embedded-text character boxes is text, not a drawn line."""
_LINE_Y_TOLERANCE_PX = 2
"""How far off-horizontal (in rendered pixels) a Hough segment may be and
still count as a horizontal line."""
_LINE_CLUSTER_Y_TOLERANCE_PX = 4
"""Near-duplicate line detections (a drawn line's top/bottom stroke edge,
or close Hough segments) this close in y, with overlapping x, are merged."""
_TABLE_BUCKET_TOLERANCE_PX = 6
"""Two line candidates sharing (x-start, x-end) within this many pixels are
treated as the same column span for the table/grid guard."""
_TABLE_BUCKET_MIN = 3
"""At least this many line candidates sharing one column span means "a
table's repeated row borders", not one field's own line; the whole bucket
is dropped."""

_MIN_BOX_PT = 5.0
_MAX_BOX_PT = 22.0
"""A plausible checkbox side length, in PDF points; outside this range a
contour is either noise or something too large to be a checkbox (a table
cell, a photo placeholder, ...)."""
_BOX_ASPECT_MIN = 0.6
_BOX_ASPECT_MAX = 1.6
_BOX_MAX_FRACTION_OF_PAGE = 0.5
"""A "box" spanning more than this fraction of the page's width or height
is a frame/border, never a checkbox."""
_BOX_TEXT_OVERLAP_MAX = 0.3
"""A candidate box more than this fraction covered by a character's own bbox
is a glyph (e.g. "o", "D", a bullet), not a drawn checkbox."""

_TEXT_FIELD_HEIGHT_PT = 14.0
"""A proposed text field sits just above its own line, this tall -- matching
how a person writes above a blank line on a real flat form."""
_LABEL_MAX_GAP_PT = 160.0
"""A label more than this far from its own field candidate is probably
labelling something else; such a span is not used."""
_LABEL_ROW_TOLERANCE_PT = 10.0
"""How far off a field candidate's own vertical center a label's vertical
center may be and still count as "the same row"."""

FieldProposalType = Literal["text", "checkbox"]


class FieldProposal(BaseModel):
    """FRM-04: one auto-detected candidate field, for review before anything is
    created. Nothing in this module ever turns a proposal into a real field on
    its own -- see :class:`~engine.ops.forms.CreateDetectedFieldsOp`."""

    model_config = ConfigDict(frozen=True)

    field_type: FieldProposalType
    rect: Rect
    """In PDF page-point space (the same space FRM-01's FieldInfo.rect uses),
    already converted back from the render DPI used to detect it."""
    confidence: float
    suggested_name: str
    """Slugified from the nearest label, de-duplicated across this page's other
    proposals and its document's existing field names; a generic fallback
    (``text_field_N`` / ``checkbox_N``) when no label was found."""
    label_text: str | None
    """The nearest text span's own text, verbatim (not slugified); ``None``
    when no nearby label was found."""


def _pixmap_to_gray_array(page: pymupdf.Page, dpi: int) -> np.ndarray:
    pixmap = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    buffer = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.stride)
    return np.ascontiguousarray(buffer[:, : pixmap.width])


class _RotatedSpan(NamedTuple):
    """One text span's own text and bbox, already transformed into the page's
    display/rotated frame (see this module's own docstring, "Rotated pages") --
    the same frame every pixel-derived candidate and `FieldProposal.rect`
    already use, so label search never compares across mismatched coordinate
    systems."""

    text: str
    bbox: Rect


def _rotate_spans(spans: list[SpanTrace], rotation_matrix: pymupdf.Matrix) -> list[_RotatedSpan]:
    rotated: list[_RotatedSpan] = []
    for trace in spans:
        rect = pymupdf.Rect(trace.style.bbox) * rotation_matrix
        rotated.append(_RotatedSpan(trace.style.text, (rect.x0, rect.y0, rect.x1, rect.y1)))
    return rotated


def _char_boxes_px(
    spans: list[SpanTrace], scale: float, rotation_matrix: pymupdf.Matrix
) -> list[tuple[float, float, float, float]]:
    boxes: list[tuple[float, float, float, float]] = []
    for trace in spans:
        for char in trace.style.chars:
            rect = pymupdf.Rect(char.bbox) * rotation_matrix
            boxes.append((rect.x0 * scale, rect.y0 * scale, rect.x1 * scale, rect.y1 * scale))
    return boxes


def _line_text_overlap_fraction(
    x0: float, y: float, x1: float, char_boxes: list[tuple[float, float, float, float]]
) -> float:
    span = x1 - x0
    if span <= 0:
        return 0.0
    covered = np.zeros(int(span) + 1, dtype=bool)
    for bx0, by0, bx1, by1 in char_boxes:
        if by0 - _LINE_Y_TOLERANCE_PX <= y <= by1 + _LINE_Y_TOLERANCE_PX:
            lo, hi = max(x0, bx0), min(x1, bx1)
            if hi > lo:
                covered[int(lo - x0) : int(hi - x0)] = True
    return float(covered.mean())


def _box_text_overlap_fraction(
    box: tuple[float, float, float, float], char_boxes: list[tuple[float, float, float, float]]
) -> float:
    cx0, cy0, cx1, cy1 = box
    area = max(1.0, cx1 - cx0) * max(1.0, cy1 - cy0)
    covered = 0.0
    for bx0, by0, bx1, by1 in char_boxes:
        lo_x, hi_x = max(cx0, bx0), min(cx1, bx1)
        lo_y, hi_y = max(cy0, by0), min(cy1, by1)
        if hi_x > lo_x and hi_y > lo_y:
            covered += (hi_x - lo_x) * (hi_y - lo_y)
    return covered / area


def _cluster_horizontal_lines(
    segments: list[tuple[float, float, float]],
) -> list[tuple[float, float, float]]:
    """Merge near-duplicate detections of the same drawn line (a stroke's top
    and bottom edge both trigger Hough independently) -- segments close in y
    with overlapping (or near-touching) x ranges collapse into one, spanning
    their combined extent."""
    clusters: list[list[tuple[float, float, float]]] = []
    for segment in sorted(segments, key=lambda s: s[1]):
        x0, y, x1 = segment
        for cluster in clusters:
            cx0, cy, cx1 = cluster[-1]
            if abs(y - cy) <= _LINE_CLUSTER_Y_TOLERANCE_PX and not (
                x1 < cx0 - _TABLE_BUCKET_TOLERANCE_PX or x0 > cx1 + _TABLE_BUCKET_TOLERANCE_PX
            ):
                cluster.append(segment)
                break
        else:
            clusters.append([segment])
    merged = []
    for cluster in clusters:
        merged_x0 = min(s[0] for s in cluster)
        merged_x1 = max(s[2] for s in cluster)
        merged_y = sum(s[1] for s in cluster) / len(cluster)
        merged.append((merged_x0, merged_y, merged_x1))
    return merged


def _drop_table_grid_lines(segments: list[tuple[float, float, float]]) -> list[tuple[float, float, float]]:
    """A form's own single field line stands alone; several lines sharing the
    same (start, end) column span are a table's repeated row borders instead
    (see this module's own docstring) -- drop every line in such a bucket."""
    buckets: dict[int, list[tuple[float, float, float]]] = {}
    bucket_keys: list[tuple[float, float]] = []
    for segment in segments:
        x0, _, x1 = segment
        for index, (bx0, bx1) in enumerate(bucket_keys):
            if abs(x0 - bx0) <= _TABLE_BUCKET_TOLERANCE_PX and abs(x1 - bx1) <= _TABLE_BUCKET_TOLERANCE_PX:
                buckets[index].append(segment)
                break
        else:
            bucket_keys.append((x0, x1))
            buckets[len(bucket_keys) - 1] = [segment]
    return [segment for group in buckets.values() if len(group) < _TABLE_BUCKET_MIN for segment in group]


def _detect_line_candidates(gray: np.ndarray, scale: float, page_width_pt: float) -> list[tuple[float, float, float]]:
    edges = cv2.Canny(gray, 50, 150)
    min_len_px = max(1, int(_MIN_LINE_LENGTH_PT * scale))
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=40, minLineLength=min_len_px, maxLineGap=3)
    if lines is None:
        return []
    segments: list[tuple[float, float, float]] = []
    for x1, y1, x2, y2 in lines:
        if abs(int(y2) - int(y1)) > _LINE_Y_TOLERANCE_PX:
            continue
        x0, x1b = (float(x1), float(x2)) if x1 <= x2 else (float(x2), float(x1))
        if x1b - x0 < min_len_px:
            continue
        length_pt = (x1b - x0) / scale
        if length_pt > _MAX_LINE_FRACTION_OF_PAGE * page_width_pt:
            continue
        segments.append((x0, float((y1 + y2) / 2), x1b))
    return segments


def _detect_box_candidates(
    gray: np.ndarray, scale: float, page_width_pt: float, page_height_pt: float
) -> list[tuple[float, float, float, float]]:
    _, binary = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY_INV)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes: list[tuple[float, float, float, float]] = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w == 0 or h == 0:
            continue
        aspect = w / h
        if not (_BOX_ASPECT_MIN <= aspect <= _BOX_ASPECT_MAX):
            continue
        w_pt, h_pt = w / scale, h / scale
        if not (_MIN_BOX_PT <= w_pt <= _MAX_BOX_PT and _MIN_BOX_PT <= h_pt <= _MAX_BOX_PT):
            continue
        if w_pt > _BOX_MAX_FRACTION_OF_PAGE * page_width_pt or h_pt > _BOX_MAX_FRACTION_OF_PAGE * page_height_pt:
            continue
        boxes.append((float(x), float(y), float(x + w), float(y + h)))
    return boxes


def _nearest_label_left(rect: Rect, spans: list[_RotatedSpan]) -> _RotatedSpan | None:
    x0, y0, _x1, y1 = rect
    center_y = (y0 + y1) / 2
    best: tuple[float, _RotatedSpan] | None = None
    for span in spans:
        _sx0, sy0, sx1, sy1 = span.bbox
        if sx1 > x0 + 2:
            continue
        if abs((sy0 + sy1) / 2 - center_y) > _LABEL_ROW_TOLERANCE_PT:
            continue
        gap = x0 - sx1
        if gap > _LABEL_MAX_GAP_PT:
            continue
        if best is None or gap < best[0]:
            best = (gap, span)
    return best[1] if best else None


def _nearest_label_right_or_above(rect: Rect, spans: list[_RotatedSpan]) -> _RotatedSpan | None:
    x0, y0, x1, y1 = rect
    center_y = (y0 + y1) / 2
    best_right: tuple[float, _RotatedSpan] | None = None
    best_above: tuple[float, _RotatedSpan] | None = None
    for span in spans:
        sx0, sy0, sx1, sy1 = span.bbox
        if sx0 >= x1 - 2 and abs((sy0 + sy1) / 2 - center_y) <= _LABEL_ROW_TOLERANCE_PT * 1.5:
            gap = sx0 - x1
            if gap <= _LABEL_MAX_GAP_PT and (best_right is None or gap < best_right[0]):
                best_right = (gap, span)
        if sy1 <= y0 + 2 and sx0 < x1 + _LABEL_MAX_GAP_PT and sx1 > x0 - _LABEL_MAX_GAP_PT:
            gap = y0 - sy1
            if gap <= _LABEL_MAX_GAP_PT and (best_above is None or gap < best_above[0]):
                best_above = (gap, span)
    if best_right is not None:
        return best_right[1]
    if best_above is not None:
        return best_above[1]
    return None


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(text: str) -> str:
    slug = _SLUG_RE.sub("_", text.strip().lower()).strip("_")
    return slug


def _unique_name(base: str, used: set[str]) -> str:
    if base not in used:
        used.add(base)
        return base
    suffix = 2
    while f"{base}_{suffix}" in used:
        suffix += 1
    name = f"{base}_{suffix}"
    used.add(name)
    return name


def detect_form_fields(document: Document, page_index: int, *, dpi: int = 150) -> list[FieldProposal]:
    """FRM-04: propose likely field locations on a flat (non-interactive) page,
    from visual cues alone -- a horizontal line/underscore proposes a text
    field, a small rectangular box proposes a checkbox. Read-only: nothing is
    created. See this module's own docstring for the algorithm and its
    documented limitations."""
    page = document.raw[page_index]
    page_width_pt, page_height_pt = page.rect.width, page.rect.height
    scale = dpi / 72.0
    rotation_matrix = page.rotation_matrix

    gray = _pixmap_to_gray_array(page, dpi)
    spans = extract_page_spans(document.raw, page_index)
    char_boxes_px = _char_boxes_px(spans, scale, rotation_matrix)
    rotated_spans = _rotate_spans(spans, rotation_matrix)

    raw_lines = _detect_line_candidates(gray, scale, page_width_pt)
    raw_lines = [
        segment for segment in raw_lines if _line_text_overlap_fraction(*segment, char_boxes_px) <= _TEXT_OVERLAP_MAX
    ]
    merged_lines = _cluster_horizontal_lines(raw_lines)
    kept_lines = _drop_table_grid_lines(merged_lines)

    raw_boxes = _detect_box_candidates(gray, scale, page_width_pt, page_height_pt)
    kept_boxes = [box for box in raw_boxes if _box_text_overlap_fraction(box, char_boxes_px) <= _BOX_TEXT_OVERLAP_MAX]

    used_names: set[str] = {field.name for field in list_fields(document, page_index)}
    proposals: list[FieldProposal] = []

    for x0_px, y_px, x1_px in sorted(kept_lines, key=lambda s: (s[1], s[0])):
        x0_pt, x1_pt, y_pt = x0_px / scale, x1_px / scale, y_px / scale
        field_rect: Rect = (x0_pt, max(0.0, y_pt - _TEXT_FIELD_HEIGHT_PT), x1_pt, y_pt)
        label = _nearest_label_left(field_rect, rotated_spans)
        label_text = label.text.strip() if label else None
        base_name = _slugify(label_text) if label_text else ""
        name = _unique_name(base_name or "text_field", used_names)
        proposals.append(
            FieldProposal(
                field_type="text",
                rect=field_rect,
                confidence=0.9 if label_text else 0.55,
                suggested_name=name,
                label_text=label_text,
            )
        )

    for x0_px, y0_px, x1_px, y1_px in sorted(kept_boxes, key=lambda b: (b[1], b[0])):
        box_rect: Rect = (x0_px / scale, y0_px / scale, x1_px / scale, y1_px / scale)
        label = _nearest_label_right_or_above(box_rect, rotated_spans)
        label_text = label.text.strip() if label else None
        base_name = _slugify(label_text) if label_text else ""
        name = _unique_name(base_name or "checkbox", used_names)
        proposals.append(
            FieldProposal(
                field_type="checkbox",
                rect=box_rect,
                confidence=0.9 if label_text else 0.55,
                suggested_name=name,
                label_text=label_text,
            )
        )

    return proposals
