"""EDT-01/02/03/04/05/06/07: style-faithful text editing.

Every edit here follows the same pipeline (SPEC.md section 5):

1. Read the target span's style and text state (engine.fonts.style).
2. Resolve which font program to draw the replacement with -- the same
   font whenever possible, a close match otherwise (engine.fonts.resolve).
3. Redact the region being replaced: true removal, not a visual cover.
4. Draw the replacement one character at a time, at positions computed
   from the resolved font's own advance widths plus the original span's
   Tc/Tw/Tz/Ts/Tr, so it fits the way the original text did (SPEC.md
   section 5.4's fit strategy builds on this).

Drawing per character (rather than one Tj call for the whole string) costs
a little file size on longer edits, but it means every character's exact
advance is computed the same verified way regardless of length, and it
sidesteps PyMuPDF's own text-shaping choices, which the caller doesn't
control otherwise.

5. Verify (FNT-12, SPEC.md section 5.6): render the page before and after,
   confirm the intended text is now really present (checked against
   texttrace's own character list, not ``get_text()``'s word-break
   heuristics -- large Tc/Tw can fool those, see tests/engine/test_edit.py),
   and report how much of the page's pixels changed. This never blocks the
   edit; it reports what happened so a caller (or a person) can review it.
"""

from __future__ import annotations

import dataclasses
import hashlib
import io
import itertools
import math
import re
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pikepdf
import pymupdf
from numpy.typing import NDArray

from engine.contentstream import protect_text_line_moves
from engine.document import Document
from engine.errors import FontResourceNotFoundError, OpValidationError
from engine.fonts import research as font_research
from engine.fonts.blocks import TextBlock, detect_alignment, detect_blocks
from engine.fonts.classify import classify_font_xref, split_subset_tag
from engine.fonts.fit import fit_to_width
from engine.fonts.icons import icon_members, is_icon_char
from engine.fonts.kerning import build_kern_pairs
from engine.fonts.match import FontCandidate, loose_font_key, normalize_font_name
from engine.fonts.merge import program_postscript_name, with_postscript_name
from engine.fonts.reflow import wrap_text
from engine.fonts.resolve import TIER_EXACT, FontResolution, embedded_program_covers, resolve_font
from engine.fonts.style import CharBox, SpanTrace, TextState, advance_for_char, dedupe_texttrace, extract_page_spans
from engine.fonts.tounicode import has_cmap, unicode_to_glyph_ids, with_cmap
from engine.fonts.units import TextLine, TextWord
from engine.geometry import page_bounds
from engine.verify import DiffResult, changed_outside, pixel_diff, render_to_array

VERIFY_DPI = 150

_EDIT_FONT_RESOURCE = "PDFWorkerzEdit"
_MIN_CONTAINMENT_MATCH_LENGTH = 6  # see _find_font_entry's fallback pass
_REDACT_HALF = 0.2  # half-size (pt) of the per-glyph redaction square, see _redact_spans
_AA_MARGIN_PX = 2  # anti-aliasing spill around an edited box, at VERIFY_DPI
_OUTSIDE_TOLERANCE = 1e-5  # fraction of page pixels; a few stray pixels at most
_OVERLAP_MIN_PT = 1.0  # adjacent spans' boxes touch, and kerning can nudge them together
# Verified at runtime (pymupdf 1.28.2); missing from pymupdf's stub like PDF_ENCRYPT_KEEP
# (see engine/document.py's note on the same class of gap).
_REDACT_KWARGS: dict[str, int] = {
    "images": pymupdf.PDF_REDACT_IMAGE_NONE,  # type: ignore[attr-defined]
    "graphics": pymupdf.PDF_REDACT_LINE_ART_NONE,  # type: ignore[attr-defined]
    "text": pymupdf.PDF_REDACT_TEXT_REMOVE,  # type: ignore[attr-defined]
}


@dataclass(frozen=True)
class VerificationResult:
    """FNT-12: what re-rendering and re-extracting the page after an edit found."""

    text_matches: bool
    """Whether the intended text is now present, checked via texttrace, not
    ``get_text()`` (its word-break heuristics can be fooled by wide Tc/Tw)."""
    diff: DiffResult
    """The before/after pixel comparison of the whole page (engine.verify)."""
    outside_changed_fraction: float = 0.0
    """Fraction of the page's pixels that changed *outside* the edited area (the removed
    text's boxes plus the newly drawn text's): SPEC.md 5.6's collateral-damage check."""
    overlaps_other_text: bool = False
    """New text was drawn on top of text the edit didn't touch (e.g. a longer replacement
    running into the next word). Those pixels sit inside the new text's own box, so the
    outside-the-edit check alone can't see it."""
    misplaced_text: bool = False
    """New text appeared away from where the edit drew it (from its start point to its end point)."""
    looks_right: bool = dataclasses.field(init=False)
    """Every check passed. A real field, not a property, so it reaches the JSON the UI reads."""

    def __post_init__(self) -> None:
        ok = self.text_matches and self.diff.changed_fraction > 0.0 and not self.overlaps_other_text
        ok = ok and not self.misplaced_text and self.outside_changed_fraction <= _OUTSIDE_TOLERANCE
        object.__setattr__(self, "looks_right", ok)


@dataclass(frozen=True)
class _BeforeEdit:
    render: NDArray[np.uint8]
    span_keys: frozenset[tuple[str, tuple[float, ...]]]
    ink: dict[tuple[str, tuple[float, ...]], list[tuple[float, float, float, float]]] = dataclasses.field(
        default_factory=dict
    )
    """Each span's inked glyph boxes (spaces left out): what new text must not be drawn
    over. A space's box is empty paper, and a span can run across a gap a removed word
    left -- nudging a word a point into that gap was reported as drawing over other text."""


def _span_keys(page: pymupdf.Page) -> frozenset[tuple[str, tuple[float, ...]]]:
    return frozenset(
        ("".join(chr(c[0]) for c in span["chars"]), tuple(round(v, 2) for v in span["bbox"]))
        for span in dedupe_texttrace(page.get_texttrace())
    )


def _ink_boxes(
    page: pymupdf.Page,
) -> dict[tuple[str, tuple[float, ...]], list[tuple[float, float, float, float]]]:
    boxes: dict[tuple[str, tuple[float, ...]], list[tuple[float, float, float, float]]] = {}
    for span in dedupe_texttrace(page.get_texttrace()):
        key = ("".join(chr(c[0]) for c in span["chars"]), tuple(round(v, 2) for v in span["bbox"]))
        boxes[key] = [tuple(c[3]) for c in span["chars"] if not chr(c[0]).isspace()]
    return boxes


def _capture(document: Document, page_index: int, verify: bool) -> _BeforeEdit | None:
    if not verify:
        return None
    page = document.raw[page_index]
    return _BeforeEdit(
        render=render_to_array(document.raw, page_index, dpi=VERIFY_DPI),
        span_keys=_span_keys(page),
        ink=_ink_boxes(page),
    )


@dataclass(frozen=True)
class EditResult:
    """What a text edit did, and how much confidence backs the font it used."""

    tier: str
    confidence: float
    requires_approval: bool
    note: str
    end_point: tuple[float, float]
    """Where the next character after the drawn text would start (baseline)."""
    verification: VerificationResult | None = None
    """None only when the caller passed verify=False."""


def _collect_font_usage(spans: list[SpanTrace], font_name: str) -> str:
    """Every character already shown anywhere on the page with the given font."""
    return "".join(trace.style.text for trace in spans if trace.style.font == font_name)


def _verify_edit(
    document: Document,
    page_index: int,
    before: _BeforeEdit,
    expected_text: str,
    removed: list[SpanTrace],
    drawn: list[_DrawnLine] | None = None,
    *,
    allow_overlap: bool = False,
    kept: list[tuple[float, float, float, float]] | None = None,
) -> VerificationResult:
    """FNT-12: confirm `expected_text` is now really on the page, measure how much
    of the page changed, how much changed outside the edit itself (the `removed`
    spans' boxes plus every span that is new since `before`), whether the new
    text landed on top of untouched text, and whether it landed where it was
    `drawn` (each line's start point, end point and size)."""
    page = document.raw[page_index]
    after = render_to_array(document.raw, page_index, dpi=VERIFY_DPI)
    diff = pixel_diff(before.render, after)
    flat_text = "".join(span.style.text for span in extract_page_spans(document.raw, page_index))
    # A no-break space drawn in a standard-14 font reads back as a plain space.
    text_matches = _plain(expected_text) in _plain(flat_text) if expected_text else True

    removed_keys = {(span.style.text, tuple(round(v, 2) for v in span.style.bbox)) for span in removed}
    untouched = [
        pymupdf.Rect(box)
        for key in before.span_keys
        if key not in removed_keys
        for box in before.ink.get(key, [key[1]])
    ]
    # `kept`: the glyphs of partly removed spans that stayed where they were. Their spans are
    # in `removed`, but they are other text all the same -- a word moved along its own line
    # onto the next word was not reported.
    still_there = {tuple(round(v, 1) for v in box) for box in kept or []}
    untouched += [pymupdf.Rect(box) for box in kept or []]
    new_keys = [key for key in _span_keys(page) if key not in before.span_keys]
    new_boxes = [pymupdf.Rect(key[1]) for key in new_keys]
    after_ink = _ink_boxes(page)
    new_ink = [
        pymupdf.Rect(box)
        for key in new_keys
        for box in after_ink.get(key, [])
        if tuple(round(v, 1) for v in box) not in still_there
    ]
    reach = pymupdf.Rect()
    for glyph_box in new_ink:
        reach |= glyph_box
    near = [other for other in untouched if other.intersects(reach)]
    overlaps = any(_overlap(box, other) for box in new_ink for other in near)
    misplaced = False
    if drawn:
        expected = [_drawn_area(line) for line in drawn]
        misplaced = any(not any(box.intersects(area) for area in expected) for box in new_boxes)

    boxes: list[tuple[float, ...]] = [span.style.bbox for span in removed]
    boxes += [(box.x0, box.y0, box.x1, box.y1) for box in new_boxes]
    scale = VERIFY_DPI / 72
    allowed = []
    for box in boxes:
        shown = pymupdf.Rect(box) * page.rotation_matrix  # the render is of the rotated page
        allowed.append(
            (
                math.floor(shown.x0 * scale) - _AA_MARGIN_PX,
                math.floor(shown.y0 * scale) - _AA_MARGIN_PX,
                math.ceil(shown.x1 * scale) + _AA_MARGIN_PX,
                math.ceil(shown.y1 * scale) + _AA_MARGIN_PX,
            )
        )
    return VerificationResult(
        text_matches=text_matches,
        diff=diff,
        outside_changed_fraction=changed_outside(before.render, after, allowed),
        overlaps_other_text=overlaps and not allow_overlap,
        misplaced_text=misplaced,
    )


def _plain(text: str) -> str:
    return text.replace("\u00a0", " ")


_DrawnLine = tuple[tuple[float, float], tuple[float, float], float]
"""(start point, end point, font size) of one drawn line of text."""


def _drawn_area(line: _DrawnLine) -> pymupdf.Rect:
    """Where a drawn line's glyphs can be: its baseline segment, grown by the font size."""
    (x0, y0), (x1, y1), size = line
    area = pymupdf.Rect(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
    return pymupdf.Rect(area.x0 - size, area.y0 - size * 1.5, area.x1 + size, area.y1 + size * 1.5)


def _overlap(a: pymupdf.Rect, b: pymupdf.Rect) -> bool:
    """Real overlap, not glyph boxes that merely touch or kern into each other."""
    inner = a & b
    return not inner.is_empty and inner.width > _OVERLAP_MIN_PT and inner.height > _OVERLAP_MIN_PT


def _resolve_font_resource(page: pymupdf.Page, resolution: FontResolution) -> str:
    """Register the resolved font on the page (if needed) and return its Tf resource name.

    The name is derived from the font program's bytes: ``insert_font`` reuses
    whatever is already registered under a name, so one shared name drew a
    second, different font (or a re-merged subset with new glyphs) in the first."""
    if resolution.fontname is not None:
        return resolution.fontname
    if resolution.font_bytes is None:
        raise ValueError("FontResolution has neither fontname nor font_bytes set")
    program, postscript = _named_program(resolution)
    name = f"{_EDIT_FONT_RESOURCE}{hashlib.sha256(program).hexdigest()[:12]}"
    xref = page.insert_font(fontname=name, fontbuffer=program)
    _set_base_font(page.parent, xref, postscript)
    return name


def _named_program(resolution: FontResolution) -> tuple[bytes, str]:
    """The font program to register, and the one name it goes by everywhere.

    Text drawn by an edit must be findable by name afterwards (a move, restyle or format
    paint looks its font up by the name texttrace reports). texttrace reports the
    program's own PostScript name, so that name wins when there is one; a nameless
    program is given the resolution's name, or failing that a stable one of its own."""
    if resolution.font_bytes is None:
        raise ValueError("FontResolution has no font_bytes to register")
    own = program_postscript_name(resolution.font_bytes)
    if own:
        return resolution.font_bytes, own
    digest = hashlib.sha256(resolution.font_bytes).hexdigest()[:12]
    name = re.sub(r"[^A-Za-z0-9+._-]", "", resolution.postscript_name or "") or f"PDFWorkerzFont-{digest}"
    return with_postscript_name(resolution.font_bytes, name), name


def _set_base_font(doc: pymupdf.Document, xref: int, postscript: str) -> None:
    """Register the font under its PostScript name. PyMuPDF writes the program's *full*
    name ("Arial Regular") as /BaseFont while texttrace reports the PostScript name
    ("ArialMT"), and the two could not be matched: the next Op on that text failed."""
    base = "/" + re.sub(r"[^A-Za-z0-9+._-]", "", postscript)
    if base == "/":
        return
    doc.xref_set_key(xref, "BaseFont", base)
    descendants = doc.xref_get_key(xref, "DescendantFonts")
    found = re.search(r"(\d+) 0 R", descendants[1]) if descendants[0] == "array" else None
    font_xrefs = [xref] + ([int(found.group(1))] if found else [])
    for font_xref in font_xrefs[1:]:
        doc.xref_set_key(font_xref, "BaseFont", base)
    for font_xref in font_xrefs:
        descriptor = doc.xref_get_key(font_xref, "FontDescriptor")
        match = re.search(r"(\d+) 0 R", descriptor[1]) if descriptor[0] == "xref" else None
        if match:
            doc.xref_set_key(int(match.group(1)), "FontName", base)


def _drawing_metrics(span: SpanTrace) -> tuple[float, TextState]:
    """The size to draw `span` at, and its text state in the same units.

    Tf alone is not the rendered size: ``Tf 1`` under a ``12 0 0 12`` text matrix
    (or ``Tf 120`` under a ``0.1`` CTM) renders at 12pt. texttrace's size is Tf
    times the matrix's horizontal scale including Tz (measured, pymupdf 1.28.2),
    so dividing Tz back out gives the text-space-to-page scale `k`. Tc, Tw, rise
    and leading are in text-space units and scale by `k` too. A non-uniform
    matrix can't be reproduced by insert_text, so its horizontal scale is used."""
    text_state = span.text_state or TextState()
    tf = text_state.font_size
    if not tf:
        return span.style.size, text_state
    k = span.style.size / (tf * text_state.horizontal_scale / 100.0)
    if math.isclose(k, 1.0, rel_tol=1e-6):
        return tf, text_state
    scaled = text_state.model_copy(
        update={
            "char_spacing": text_state.char_spacing * k,
            "word_spacing": text_state.word_spacing * k,
            "rise": text_state.rise * k,
            "leading": text_state.leading * k,
            "font_size": tf * k,
        }
    )
    return tf * k, scaled


def _text_state_note(span: SpanTrace) -> str:
    """Say so when the span's spacing/scaling/rise couldn't be read (engine.fonts.style falls
    back to none when the content stream and the rendered glyphs don't line up), rather than
    silently drawing with default spacing."""
    if span.text_state is not None:
        return ""
    return "; the original character spacing and scaling could not be read, so default spacing was used"


def _glyph_count(page: pymupdf.Page) -> int:
    return sum(len(span["chars"]) for span in dedupe_texttrace(page.get_texttrace()))


def _redact_chars(page: pymupdf.Page, chars: list[CharBox]) -> None:
    """Remove exactly these glyphs, and nothing else.

    A redaction rectangle removes every glyph whose box touches it, and a span's
    bbox runs from ascender to descender -- so with ordinary leading it reached
    into the lines above and below and deleted them too. A tiny rectangle at
    each glyph's own centre touches only that glyph (rotated text included).
    The glyph count is then checked: if anything besides these glyphs vanished,
    or some of them survived, the edit is refused rather than kept.

    EDT-17/18: the glyphs may be any part of a span. MuPDF replaces a removed glyph
    with an equal ``TJ`` offset, so the rest of its ``Tj``/``TJ`` stays exactly where
    it was (measured on pymupdf 1.28.2 for plain, TJ-kerned, Tw-, Tc-spaced,
    Identity-H and rotated text: no pixel changes outside the removed word)."""
    targets = list(chars)
    if not targets:
        return
    protect_text_line_moves(page)  # the redaction's own content cleaning would move other text
    before = _glyph_count(page)
    for char in targets:
        x0, y0, x1, y1 = char.bbox
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        page.add_redact_annot(pymupdf.Rect(cx - _REDACT_HALF, cy - _REDACT_HALF, cx + _REDACT_HALF, cy + _REDACT_HALF))
    page.apply_redactions(**_REDACT_KWARGS)
    removed = before - _glyph_count(page)
    if removed > len(targets):
        raise OpValidationError(
            f"this edit would also remove {removed - len(targets)} character(s) of neighbouring text "
            "that overlaps it; nothing was changed"
        )
    if removed < len(targets):
        raise OpValidationError(
            f"{len(targets) - removed} of the {len(targets)} character(s) being replaced could not be removed; "
            "nothing was changed"
        )


def _redact_spans(page: pymupdf.Page, spans: list[SpanTrace]) -> None:
    """Remove exactly the glyphs of `spans`, and nothing else (see _redact_chars)."""
    _redact_chars(page, [char for span in spans for char in span.style.chars])


def _load_font(resolution: FontResolution) -> pymupdf.Font:
    """A pymupdf.Font for measuring or drawing with the resolved font program."""
    if resolution.fontname is not None:
        return pymupdf.Font(resolution.fontname)
    if resolution.font_bytes is None:
        raise ValueError("FontResolution has neither fontname nor font_bytes set")
    return pymupdf.Font(fontbuffer=resolution.font_bytes)


def _is_default_spacing(text_state: TextState) -> bool:
    return text_state.char_spacing == 0.0 and text_state.word_spacing == 0.0 and text_state.horizontal_scale == 100.0


def draw_styled_text(
    page: pymupdf.Page,
    *,
    text: str,
    origin: tuple[float, float],
    font_size: float,
    color: tuple[float, float, float],
    text_state: TextState,
    rotation_degrees: float,
    resolution: FontResolution,
) -> tuple[float, float]:
    """Draw `text` starting at `origin` (baseline point), matching `text_state`'s
    spacing/scaling/rise/render mode and `rotation_degrees`, through the font
    `resolution` selected. Returns the baseline point just past the last character.

    Text with default spacing (Tc=0, Tw=0, Tz=100%) is drawn with a single call,
    so it stays one contiguous span for any edit that follows
    (confirmed: per-character calls fragment texttrace's spans one character
    each, which then only a single-character search can find). Non-default
    spacing still needs per-character control, and accepts that
    fragmentation as a known trade-off; the one upside is that the font's own
    kerning pairs (FNT-09) are applied there too, since PyMuPDF's own text
    insertion never applies any kerning at all, in either drawing path (also
    confirmed empirically).

    Rotated text is drawn rotated: every insert_text call gets
    ``morph=(point, Matrix(-rotation_degrees))`` -- measured on pymupdf 1.28.2,
    that is the matrix whose output texttrace reports at `rotation_degrees`.
    Without it, rotated text came back as upright glyphs stepped along the angle.
    """
    if not text:
        return origin

    fontname = _resolve_font_resource(page, resolution)
    font = _load_font(resolution)
    rotation = pymupdf.Matrix(-rotation_degrees) if rotation_degrees else None

    def morph_at(x: float, y: float) -> tuple[pymupdf.Point, pymupdf.Matrix] | None:
        return (pymupdf.Point(x, y), rotation) if rotation is not None else None

    angle = math.radians(rotation_degrees)
    cos_a, sin_a = math.cos(angle), math.sin(angle)

    if _is_default_spacing(text_state):
        page.insert_text(
            origin,
            text,
            fontsize=font_size,
            fontname=fontname,
            color=color,
            render_mode=text_state.render_mode,
            morph=morph_at(*origin),
        )
        length = font.text_length(text, fontsize=font_size)
        return (origin[0] + length * cos_a, origin[1] + length * sin_a)

    widths = font.char_lengths(text, fontsize=font_size)
    # FNT-09: PyMuPDF applies no kerning at all in either drawing path (confirmed
    # empirically), so read the font's own kern table for one, here where per-
    # character positioning is already happening for another reason anyway.
    kern_pairs = build_kern_pairs(resolution.font_bytes) if resolution.font_bytes else {}

    # No rise offset here: texttrace's origins already include the span's rise (Ts), so the
    # origin a caller passes is already raised -- adding it again moved raised text twice.
    rise_dx = rise_dy = 0.0

    x, y = origin
    previous_char: str | None = None
    for char, base_width in zip(text, widths, strict=True):
        if previous_char is not None:
            kern = kern_pairs.get((previous_char, char), 0.0) * font_size
            x += kern * cos_a
            y += kern * sin_a
        page.insert_text(
            (x + rise_dx, y + rise_dy),
            char,
            fontsize=font_size,
            fontname=fontname,
            color=color,
            render_mode=text_state.render_mode,
            morph=morph_at(x + rise_dx, y + rise_dy),
        )
        advance = advance_for_char(base_width, char, text_state)
        x += advance * cos_a
        y += advance * sin_a
        previous_char = char
    return (x, y)


def _find_font_entry(page: pymupdf.Page, basefont: str, chars: str = "") -> tuple[int, str] | None:
    """The (xref, Tf resource name) of the page's font whose BaseFont matches, if any.

    Matched with the subset tag stripped and both sides case/punctuation-
    normalized (engine.fonts.match.normalize_font_name), because two real,
    confirmed discrepancies exist between what texttrace reports and what
    ``Page.get_fonts()`` reports for the *same* font: a subset font's name
    loses its "ABCDEF+" prefix in texttrace but not in get_fonts, and --
    separately -- texttrace reports a full (non-subset) embedded font by
    its PostScript name ("BitstreamVeraSans-Bold") while get_fonts reports
    its full name ("Bitstream Vera Sans Bold") from the same /BaseFont
    entry. Normalizing away case, spaces and hyphens resolves both. A font
    with no real BaseFont at all (Type3 commonly has none, FNT-15) gets a
    synthesized "<FontType> (<ref>)" name from texttrace instead; matched
    here by font type against get_fonts's empty entry.

    A third confirmed variant, found testing a system CJK font: texttrace
    can drop a style suffix get_fonts keeps ("MalgunGothic" vs "Malgun
    Gothic Regular", the same /BaseFont). Rather than special-case every
    such suffix, an exact normalized match is tried first on every entry,
    and only if none matches at all does a second pass accept a
    containment match (one normalized name a prefix/suffix of the other,
    with a minimum length so short names can't false-match each other).

    Several fonts can share one name -- a generator's subsets that each hold different
    characters, or nameless programs all reported as "(null)". Among equal matches, the
    one whose program can draw `chars` (the span's own text) is chosen: taking the first
    drew a figure with a subset that held only letters, falling to a look-alike.
    """
    _, plain = split_subset_tag(basefont)
    target = normalize_font_name(plain)
    entries = list(page.get_fonts(full=True))

    exact: list[tuple[int, str]] = []
    for entry in entries:
        xref, _ext, font_type, entry_basefont, resource_name, *_rest = entry
        _, entry_plain = split_subset_tag(entry_basefont)
        if normalize_font_name(entry_plain) == target or (not entry_basefont and plain.startswith(f"{font_type} (")):
            exact.append((xref, resource_name))
    if exact:
        return _best_covering(page.parent, exact, chars)

    # "ArialMT" and "Arial Regular" name the same font: compare the way FNT-06's
    # find_by_name does, with style words like Regular/Book and MT/PSMT ignored.
    loose = loose_font_key(plain)
    if loose:
        similar = [(entry[0], entry[4]) for entry in entries if loose_font_key(split_subset_tag(entry[3])[1]) == loose]
        if similar:
            return _best_covering(page.parent, similar, chars)

    if len(target) >= _MIN_CONTAINMENT_MATCH_LENGTH:
        for entry in entries:
            xref, _ext, _font_type, entry_basefont, resource_name, *_rest = entry
            _, entry_plain = split_subset_tag(entry_basefont)
            candidate = normalize_font_name(entry_plain)
            if candidate and (candidate.startswith(target) or target.startswith(candidate)):
                return xref, resource_name
    return None


def _shared_name(page: pymupdf.Page, basefont: str) -> bool:
    target = normalize_font_name(split_subset_tag(basefont)[1])
    names = [normalize_font_name(split_subset_tag(entry[3])[1]) for entry in page.get_fonts(full=True)]
    return names.count(target) > 1


def _program_with_cmap(doc: pymupdf.Document, xref: int) -> bytes | None:
    """The font program embedded at `xref`, given a cmap when it has none.

    A subset without a cmap can't say which glyph is which character; its ToUnicode
    map, read backwards, can (engine.fonts.tounicode) -- for every character the
    document already shows in that font."""
    program = doc.extract_font(xref)[3] or None
    if program and not has_cmap(program):
        recovered = unicode_to_glyph_ids(doc, xref)
        program = (with_cmap(program, recovered) if recovered else None) or program
    return program


def _best_covering(doc: pymupdf.Document, candidates: list[tuple[int, str]], chars: str) -> tuple[int, str]:
    if len(candidates) == 1 or not chars:
        return candidates[0]
    for xref, resource_name in candidates:
        program = _program_with_cmap(doc, xref)
        if program and embedded_program_covers(program, chars):
            return xref, resource_name
    return candidates[0]


def resolve_font_for_span(
    document: Document,
    page_index: int,
    span: SpanTrace,
    needed_text: str,
    *,
    font_index: list[FontCandidate],
    flag_for_research: bool = True,
) -> FontResolution:
    """The FontResolution (engine.fonts.resolve) for drawing `needed_text` in the
    same style as `span`. Shared by replace, restyle and insert-near-reference.
    `flag_for_research` is False for a preview: only an edit actually made counts
    toward the fonts-to-research list (engine.fonts.research), not every keystroke.
    """
    page = document.raw[page_index]
    style = span.style

    found = _find_font_entry(page, style.font, style.text)
    if found is None:
        raise FontResourceNotFoundError(f"could not find the font resource for {style.font!r} on page {page_index}")
    xref, resource_name = found

    # By xref, not name: get_fonts() includes fonts inside Form XObjects, whose
    # resource names ("F1") can collide with a different page-level font.
    with pikepdf.open(io.BytesIO(document.to_bytes())) as pikepdf_doc:
        classification = classify_font_xref(pikepdf_doc, xref, resource_name)

    original_bytes = _program_with_cmap(document.raw, xref)
    already_used = _collect_font_usage(extract_page_spans(document.raw, page_index), style.font)
    if _shared_name(page, style.font):
        # Several fonts on this page go by this name (nameless "(null)" subsets, say):
        # other spans with the same name may be drawn in a different program, so only
        # this span's own text is known to be in this one.
        already_used = style.text

    resolution = resolve_font(
        classification,
        original_font_bytes=original_bytes,
        already_rendered_text=already_used,
        needed_text=needed_text,
        font_index=font_index,
    )
    if not flag_for_research:
        return resolution
    if resolution.tier != TIER_EXACT:
        # Owner's rule: fall back to the closest match, and flag the font so it can be added
        # to PDFWorkerz's own library later (engine.fonts.research).
        source = document.source_path.name if document.source_path else "(unsaved document)"
        font_research.flag(style.font, tier=resolution.tier, note=resolution.note, document=source)
    else:
        font_research.resolve(style.font)  # matched exactly now: no longer needs research
    return resolution


def _drawn_width(resolution: FontResolution, text: str, font_size: float, text_state: TextState) -> float:
    """How wide draw_styled_text will make `text` (kerning aside)."""
    widths = _load_font(resolution).char_lengths(text, fontsize=font_size)
    return sum(advance_for_char(width, char, text_state) for char, width in zip(text, widths, strict=True))


def anchored_position(
    reference: SpanTrace, where: str, text: str, resolution: FontResolution, font_size: float
) -> tuple[float, float]:
    """EDT-03 / CMD-01: the baseline point for new text placed `where` ("below", "above",
    "after" or "before") an existing span, one line or one space away from it."""
    text_state = _drawing_metrics(reference)[1]
    x0, y = reference.style.chars[0].origin
    pitch = text_state.leading if text_state.leading > 0 else font_size * 1.2
    space = _drawn_width(resolution, " ", font_size, TextState())
    if where == "below":
        return (x0, y + pitch)
    if where == "above":
        return (x0, y - pitch)
    if where == "after":
        return (reference.style.bbox[2] + space, y)
    if where == "before":
        return (x0 - space - _drawn_width(resolution, text, font_size, TextState()), y)
    raise OpValidationError(f"unknown placement {where!r}: use below, above, after or before")


def _aligned_origin(
    document: Document,
    page_index: int,
    span: SpanTrace,
    new_width: float,
) -> tuple[tuple[float, float], str]:
    """Where to start drawing a replacement so a right-aligned or centered
    line keeps its right edge or center, not its left edge (detect_alignment)."""
    origin = span.style.chars[0].origin
    page = document.raw[page_index]
    alignment = detect_alignment(span, extract_page_spans(document.raw, page_index), page_bounds(page).width)
    right = span.style.bbox[2]
    if alignment == "right":
        return (right - new_width, origin[1]), alignment
    if alignment == "center":
        return ((origin[0] + right) / 2 - new_width / 2, origin[1]), alignment
    return origin, alignment


def replace_span_text(
    document: Document,
    page_index: int,
    span: SpanTrace,
    new_text: str,
    *,
    font_index: list[FontCandidate],
    override_size: float | None = None,
    override_color: tuple[float, float, float] | None = None,
    override_font: FontResolution | None = None,
    fit: bool = False,
    verify: bool = True,
) -> EditResult:
    """EDT-01/EDT-02/EDT-04/EDT-06: replace one span's text with `new_text`
    (empty text deletes it; the same text with an override restyles it),
    matching its original style as closely as the resolved font allows.

    `fit` (FNT-10, off by default): when the replacement's natural width
    differs from the original span's, adjust tracking or horizontal
    scaling within tolerance so it occupies the same width, rather than
    running long or short (SPEC.md section 5.4). Off by default because
    any nonzero adjustment forces per-character drawing, which fragments
    the result into one texttrace span per character (see
    draw_styled_text) -- fine for a one-off edit, but it means a second
    edit can no longer find that text as one span. Turn it on when
    matching the original width matters more than staying chainable, for
    example filling a fixed-width field.
    """
    page = document.raw[page_index]
    style = span.style
    natural_size, text_state = _drawing_metrics(span)

    resolution = override_font or resolve_font_for_span(document, page_index, span, new_text, font_index=font_index)
    before = _capture(document, page_index, verify)

    font_size = override_size if override_size is not None else natural_size
    color = override_color if override_color is not None else style.color

    fit_result = None
    if fit and new_text and override_size is None:
        target_width = style.bbox[2] - style.bbox[0]
        fit_result = fit_to_width(_load_font(resolution), new_text, font_size, text_state, target_width)
        text_state = fit_result.text_state

    origin, alignment = style.chars[0].origin, "left"
    if new_text and fit_result is None:
        new_width = _drawn_width(resolution, new_text, font_size, text_state)
        origin, alignment = _aligned_origin(document, page_index, span, new_width)

    _redact_spans(page, [span])

    end_point = draw_styled_text(
        page,
        text=new_text,
        origin=origin,
        font_size=font_size,
        color=color,
        text_state=text_state,
        rotation_degrees=style.rotation_degrees,
        resolution=resolution,
    )

    drawn = [(origin, end_point, font_size)] if new_text else []
    verification = _verify_edit(document, page_index, before, new_text, [span], drawn) if before is not None else None
    note = resolution.note + _text_state_note(span)
    if alignment != "left":
        note += f"; kept the line's {alignment} alignment"
    if fit_result is not None and not fit_result.fits:
        note += (
            f"; fit-to-width could not match the original {fit_result.target_width:.1f}pt width "
            f"within tolerance (drew at {fit_result.natural_width:.1f}pt) -- consider reflow"
        )
    return EditResult(
        tier=resolution.tier,
        confidence=resolution.confidence,
        requires_approval=resolution.requires_approval,
        note=note,
        end_point=end_point,
        verification=verification,
    )


def copy_span_style(
    document: Document,
    source_page_index: int,
    source: SpanTrace,
    target_page_index: int,
    target: SpanTrace,
    *,
    font_index: list[FontCandidate],
    verify: bool = True,
) -> EditResult:
    """EDT-07, format painter: redraw `target`'s own text, in place, with
    `source`'s font, size, color and text state (spacing, scaling, rise,
    render mode). The target keeps its wording, baseline origin and
    rotation; only its look changes.

    The font is resolved against the *source* span (it's the source's
    typeface being copied), for the *target's* characters, so a source font
    subset that lacks one of them falls to a weaker tier exactly the way a
    replacement would. The resolution is a self-contained font program or
    standard-14 name, so source and target may be on different pages.
    """
    target_page = document.raw[target_page_index]
    text = target.style.text
    font_size, text_state = _drawing_metrics(source)

    resolution = resolve_font_for_span(document, source_page_index, source, text, font_index=font_index)
    before = _capture(document, target_page_index, verify)

    _redact_spans(target_page, [target])

    end_point = draw_styled_text(
        target_page,
        text=text,
        origin=target.style.chars[0].origin,
        font_size=font_size,
        color=source.style.color,
        text_state=text_state,
        rotation_degrees=target.style.rotation_degrees,
        resolution=resolution,
    )

    drawn = [(target.style.chars[0].origin, end_point, font_size)]
    verification = (
        _verify_edit(document, target_page_index, before, text, [target], drawn) if before is not None else None
    )
    return EditResult(
        tier=resolution.tier,
        confidence=resolution.confidence,
        requires_approval=resolution.requires_approval,
        note=resolution.note + _text_state_note(source),
        end_point=end_point,
        verification=verification,
    )


def insert_text_near(
    document: Document,
    page_index: int,
    reference_span: SpanTrace,
    text: str,
    origin: tuple[float, float],
    *,
    font_index: list[FontCandidate],
    verify: bool = True,
    override_font: FontResolution | None = None,
    override_size: float | None = None,
    override_color: tuple[float, float, float] | None = None,
) -> EditResult:
    """EDT-03: draw new `text` at `origin`, matching `reference_span`'s style, with any of its
    font, size or color replaced by an explicit choice.

    Nothing is redacted -- this adds text near an existing span without
    touching it, for example a caption or a value beside a label.
    """
    page = document.raw[page_index]
    style = reference_span.style
    font_size, text_state = _drawing_metrics(reference_span)
    font_size = override_size if override_size is not None else font_size

    resolution = override_font or resolve_font_for_span(
        document, page_index, reference_span, text, font_index=font_index
    )
    before = _capture(document, page_index, verify)

    end_point = draw_styled_text(
        page,
        text=text,
        origin=origin,
        font_size=font_size,
        color=override_color if override_color is not None else style.color,
        text_state=text_state,
        rotation_degrees=style.rotation_degrees,
        resolution=resolution,
    )

    drawn = [(origin, end_point, font_size)]
    verification = _verify_edit(document, page_index, before, text, [], drawn) if before is not None else None
    return EditResult(
        tier=resolution.tier,
        confidence=resolution.confidence,
        requires_approval=resolution.requires_approval,
        note=resolution.note + _text_state_note(reference_span),
        end_point=end_point,
        verification=verification,
    )


def insert_styled_text(
    document: Document,
    page_index: int,
    text: str,
    origin: tuple[float, float],
    *,
    font: FontResolution,
    size: float,
    color: tuple[float, float, float],
    verify: bool = True,
) -> EditResult:
    """EDT-03 with a fully explicit style: draw `text` at `origin` (baseline) in the chosen
    `font`, `size` and `color`, copying nothing from existing text."""
    if not text:
        raise OpValidationError("there is no text to add")
    before = _capture(document, page_index, verify)
    end_point = draw_styled_text(
        document.raw[page_index],
        text=text,
        origin=origin,
        font_size=size,
        color=color,
        text_state=TextState(),
        rotation_degrees=0.0,
        resolution=font,
    )
    drawn = [(origin, end_point, size)]
    verification = _verify_edit(document, page_index, before, text, [], drawn) if before is not None else None
    return EditResult(
        tier=font.tier,
        confidence=font.confidence,
        requires_approval=font.requires_approval,
        note=font.note,
        end_point=end_point,
        verification=verification,
    )


def _line_pitch(block: TextBlock, font_size: float) -> float:
    """Baseline-to-baseline distance for re-laid-out lines: the block's own
    spacing when it has two or more lines, else the TL leading, else 1.2x."""
    if len(block.rows) >= 2:
        return block.row_origin(1)[1] - block.row_origin(0)[1]
    leading = _drawing_metrics(_block_reference(block))[1].leading
    return leading if leading > 0 else font_size * 1.2


def _block_reference(block: TextBlock) -> SpanTrace:
    """The span whose style a block is re-laid-out in. A block of plain lines uses its
    first line, as it always has; one with several runs on a line (EDT-19) uses the style
    most of it is drawn in, so a bold first word doesn't turn the whole paragraph bold."""
    return block.dominant if block.fragmented else block.lines[0]


def _row_references(block: TextBlock, reference: SpanTrace) -> list[SpanTrace]:
    """Per line, the span that gives a re-laid-out line its color and text state: the
    line's own span when it has just one, else the block's reference."""
    return [row[0] if len(row) == 1 else reference for row in block.rows]


def _block_icon(block: TextBlock) -> str | None:
    """The first icon/emoji glyph (engine.fonts.icons) anywhere in `block`, or None.
    FNT-20: a block re-wrap or full-width move redraws every line from scratch, which
    would lose an icon glyph it can't reproduce exactly (most commonly Type3, FNT-15)."""
    runs = [span for row in block.rows for span in row] if block.rows else list(block.lines)
    for span in runs:
        for char in span.style.chars:
            if is_icon_char(char.char, span.style.font):
                return char.char
    return None


def _style_loss_note(block: TextBlock, reference: SpanTrace) -> str:
    """What a re-layout in one style costs a block with several styles on a line; empty
    when nothing is lost. Never silent: callers put this in the result's note."""
    if not (block.fragmented and block.mixed_style):
        return ""
    return (
        f"; mixed styles: redrawn in the block's main style ({reference.style.font} "
        f"{reference.style.size:g} pt), so {block.off_style_runs()} run(s) lost their own font, size or color"
    )


def move_resize_block(
    document: Document,
    page_index: int,
    block: TextBlock,
    *,
    dx: float = 0.0,
    dy: float = 0.0,
    width: float | None = None,
    font_index: list[FontCandidate],
    verify: bool = True,
    keep_original: bool = False,
    target_page_index: int | None = None,
) -> list[EditResult]:
    """EDT-05: move a text block by (`dx`, `dy`) page points (y-down, like
    every MuPDF coordinate) and/or re-wrap it to a new `width`.

    EDT-15: with `keep_original`, the block is copied instead -- drawn again at the
    offset, on `target_page_index` if given, with the original left in place. A copy
    is allowed to overlap other text (a paste lands just beside its source).

    Unlike reflow_block, which must fit new text into the block's existing
    lines, resizing may change the line count: a narrower block grows
    downward, a wider one shrinks, at the block's own line spacing. The
    block's text itself never changes. Every original line is redacted
    before anything is drawn, so a short move never has its own new lines
    removed by the redaction of an old one they overlap.

    EDT-19: a block whose lines hold several runs (a bold word, a colored word, the
    pieces an in-line edit left) is moved or copied run by run, each glyph landing at
    its own origin plus the offset in its own font, size and color (move_glyph_ranges);
    that is one change, so one EditResult. Re-wrapping such a block to a new `width`
    can't keep runs on the words they belonged to, so it is redrawn in the block's main
    style and the last result's note says what was lost (and asks for approval).

    FNT-20: a block with an icon/emoji glyph (engine.fonts.icons) is always
    `fragmented` (the icon is its own style run), so a plain move always takes the
    run-by-run path above, which already keeps the icon in its own font exactly where
    it lands. A `width` resize is refused instead: unlike a run-by-run move, it redraws
    every line from scratch, which would lose the icon (most commonly a Type3 glyph,
    FNT-15).
    """
    if not block.lines:
        return []
    target_index = page_index if target_page_index is None else target_page_index
    if target_index != page_index and not keep_original:
        raise OpValidationError("a text block can only be copied to another page, not moved there")
    if width is None and block.fragmented:
        ranges: list[GlyphRange] = [(span, 0, len(span.style.chars)) for span in block.lines]
        return [
            move_glyph_ranges(
                document,
                page_index,
                ranges,
                dx=dx,
                dy=dy,
                keep_original=keep_original,
                target_page_index=target_page_index,
                font_index=font_index,
                verify=verify,
            )
        ]
    if width is not None and (icon := _block_icon(block)) is not None:
        raise OpValidationError(
            f"this block has the icon {icon!r}, which resizing can't keep (it can't be redrawn "
            "exactly); move it instead of resizing it, or edit the width-changing text in Line or "
            "Word mode"
        )
    page = document.raw[target_index]
    reference = _block_reference(block)
    row_references = _row_references(block, reference)
    font_size, text_state = _drawing_metrics(reference)
    resolution = resolve_font_for_span(document, page_index, reference, block.text, font_index=font_index)

    first_x, first_y = block.row_origin(0)
    if width is None:
        placed = [
            (line.style.text, (line.style.chars[0].origin[0] + dx, line.style.chars[0].origin[1] + dy), line)
            for line in block.lines
        ]
    else:
        if reference.style.rotation_degrees != 0.0:
            raise OpValidationError("resizing a rotated text block is not supported; move it instead")
        wrapped = wrap_text(block.text, _load_font(resolution), font_size, width)
        pitch = _line_pitch(block, font_size)
        placed = [
            (text, (first_x + dx, first_y + dy + i * pitch), row_references[min(i, len(row_references) - 1)])
            for i, text in enumerate(wrapped)
        ]

    page_rect = page_bounds(page)
    for _text, (x, y), _line in placed:
        if not (page_rect.x0 <= x < page_rect.x1 and page_rect.y0 < y <= page_rect.y1):
            raise OpValidationError(f"the block would be moved off the page (a line would start at {x:.0f}, {y:.0f})")

    before = _capture(document, target_index, verify)
    if not keep_original:
        _redact_spans(page, list(block.lines))

    results: list[EditResult] = []
    for text, origin, line in placed:
        end_point = draw_styled_text(
            page,
            text=text,
            origin=origin,
            font_size=font_size,
            color=line.style.color,
            text_state=_drawing_metrics(line)[1] if line.text_state else text_state,
            rotation_degrees=line.style.rotation_degrees,
            resolution=resolution,
        )
        results.append(
            EditResult(
                tier=resolution.tier,
                confidence=resolution.confidence,
                requires_approval=resolution.requires_approval,
                note=resolution.note + _text_state_note(reference),
                end_point=end_point,
            )
        )
    if before is not None:
        drawn = [
            (origin, result.end_point, font_size) for (_t, origin, _l), result in zip(placed, results, strict=True)
        ]
        removed = [] if keep_original else list(block.lines)
        verification = _verify_edit(
            document, target_index, before, placed[0][0], removed, drawn, allow_overlap=keep_original
        )
        results[-1] = dataclasses.replace(results[-1], verification=verification)
    lost = _style_loss_note(block, reference)
    if lost and results:
        results[-1] = dataclasses.replace(results[-1], requires_approval=True, note=results[-1].note + lost)
    return results


def delete_block(document: Document, page_index: int, block: TextBlock, *, verify: bool = True) -> EditResult:
    """EDT-15: remove every line of a text block, and nothing else."""
    if not block.lines:
        raise OpValidationError("the text block is empty")
    before = _capture(document, page_index, verify)
    _redact_spans(document.raw[page_index], list(block.lines))
    first = block.lines[0].style
    result = EditResult(
        tier=TIER_EXACT,
        confidence=1.0,
        requires_approval=False,
        note=f"deleted {len(block.rows)} line(s)",
        end_point=first.chars[0].origin if first.chars else (first.bbox[0], first.bbox[3]),
    )
    if before is None:
        return result
    return dataclasses.replace(result, verification=_verify_edit(document, page_index, before, "", list(block.lines)))


def reflow_block(
    document: Document,
    page_index: int,
    block: TextBlock,
    new_text: str,
    *,
    font_index: list[FontCandidate],
    verify: bool = True,
    align: str = "left",
    grow: bool = False,
    override_font: FontResolution | None = None,
    override_size: float | None = None,
    override_color: tuple[float, float, float] | None = None,
) -> list[EditResult]:
    """FNT-11: replace an entire block's text with `new_text`, re-wrapping it
    across the block's own existing lines -- see engine.fonts.reflow for why
    this never grows past the block's original line count. Returns one
    EditResult per line actually drawn (one per line of the block, in order);
    if `new_text` needed more lines than the block has, the last result's
    note says so and its `requires_approval` is set.

    FNT-17: `align="justify"` spreads every line but the last to the block's
    full width, and `grow=True` lets the paragraph take more lines, moving the
    text below it in the same column down to make room (see reflow_paragraph).

    EDT-17: `override_font`, `override_size` and `override_color` restyle the block as it
    is re-wrapped (each left None keeps the block's own), so editing or restyling a whole
    block is one index-addressed reflow.

    EDT-19: new text can't say which of its words were the bold or colored ones, so a
    block with several styles on a line is re-wrapped in its main style (the one most of
    it is drawn in) -- allowed rather than refused, so a paragraph with one bold word can
    still be edited as a block, but never silently: the last result's note says how many
    runs lost their own style and its `requires_approval` is set. Editing in Line or Word
    mode (rewrite_line_range) keeps every other run as it is.

    FNT-20: refused outright, nothing changed, when the block has an icon/emoji glyph
    (engine.fonts.icons) anywhere -- a re-wrap redraws every line from scratch, which
    would lose it (most commonly a Type3 glyph, FNT-15). Edit the block's text in Line
    or Word mode instead, which leaves the icon's own line untouched.
    """
    if not block.lines:
        return []
    if (icon := _block_icon(block)) is not None:
        raise OpValidationError(
            f"this paragraph has the icon {icon!r}, which a re-wrap can't keep (it can't be "
            "redrawn exactly); edit it in Line or Word mode instead"
        )
    if align not in ("left", "justify"):
        raise OpValidationError(f"unknown alignment {align!r}: use left or justify")
    if align == "justify" or grow or block.fragmented:
        # A line in several spans can't be replaced span by span: redraw the paragraph.
        return reflow_paragraph(
            document,
            page_index,
            block,
            new_text,
            font_index=font_index,
            verify=verify,
            justify=align == "justify",
            grow=grow,
            override_font=override_font,
            override_size=override_size,
            override_color=override_color,
        )

    reference = block.lines[0]
    font_size = override_size if override_size is not None else _drawing_metrics(reference)[0]
    resolution = override_font or resolve_font_for_span(
        document, page_index, reference, new_text, font_index=font_index
    )
    font = _load_font(resolution)

    max_width = max(line.style.bbox[2] - line.style.bbox[0] for line in block.lines)
    wrapped = wrap_text(new_text, font, font_size, max_width)

    results = [
        replace_span_text(
            document,
            page_index,
            line,
            wrapped[i] if i < len(wrapped) else "",
            font_index=font_index,
            override_size=override_size,
            override_color=override_color,
            override_font=override_font,
            verify=verify,
        )
        for i, line in enumerate(block.lines)
    ]

    if len(wrapped) > len(block.lines):
        extra_lines = len(wrapped) - len(block.lines)
        note = (
            f"{results[-1].note}; overflow: {extra_lines} more line(s) needed than "
            f"this block has ({len(block.lines)}), not drawn"
        )
        results[-1] = dataclasses.replace(results[-1], requires_approval=True, note=note)
    return results


_JUSTIFY_MAX_GAP_RATIO = 1.5
"""A justified gap wider than 1.5x the font size looks broken (a short line spread across the
column), so such a line is left-aligned instead, as typesetters do with a paragraph's last line."""


def _content_below(
    page: pymupdf.Page, top: float, left: float, right: float, *, until: float | None = None
) -> list[str]:
    """Non-text things between `top` and `until` (default: the page bottom) that overlap the
    column [left, right]: moving text past them would misalign them, and the editor can't move
    them with the text yet."""
    found = []

    def overlaps(rect: pymupdf.Rect) -> bool:
        return rect.y1 > top and (until is None or rect.y0 < until) and rect.x1 > left and rect.x0 < right

    if any(overlaps(pymupdf.Rect(info["bbox"])) for info in page.get_image_info()):
        found.append("images")
    if any(overlaps(pymupdf.Rect(d["rect"])) for d in page.get_drawings()):
        found.append("drawings")
    if any(overlaps(pymupdf.Rect(link["from"])) for link in page.get_links()):
        found.append("links")
    return found


def _draw_justified(
    page: pymupdf.Page,
    text: str,
    origin: tuple[float, float],
    width: float,
    *,
    font: pymupdf.Font,
    font_size: float,
    color: tuple[float, float, float],
    text_state: TextState,
    resolution: FontResolution,
) -> tuple[float, float]:
    """Draw `text` word by word so it spans exactly `width` (the gaps between words share the
    slack). Word by word rather than with word spacing (Tw): each word stays one searchable span."""
    words = text.split(" ")
    natural = sum(font.text_length(word, fontsize=font_size) for word in words)
    gap = (width - natural) / (len(words) - 1)
    x, y = origin
    end = origin
    for word in words:
        end = draw_styled_text(
            page,
            text=word,
            origin=(x, y),
            font_size=font_size,
            color=color,
            text_state=text_state,
            rotation_degrees=0.0,
            resolution=resolution,
        )
        x = end[0] + gap
    return end


def _stretches(text: str, width: float, font: pymupdf.Font, font_size: float) -> bool:
    words = text.split(" ")
    if len(words) < 2:
        return False
    natural = sum(font.text_length(word, fontsize=font_size) for word in words)
    return (width - natural) / (len(words) - 1) <= font_size * _JUSTIFY_MAX_GAP_RATIO


def reflow_paragraph(
    document: Document,
    page_index: int,
    block: TextBlock,
    new_text: str,
    *,
    font_index: list[FontCandidate],
    verify: bool = True,
    justify: bool = False,
    grow: bool = False,
    override_font: FontResolution | None = None,
    override_size: float | None = None,
    override_color: tuple[float, float, float] | None = None,
) -> list[EditResult]:
    """FNT-17: re-wrap `new_text` across the block's width, optionally justified, and, with
    `grow`, on as many lines as it needs: the extra lines continue at the block's own line
    spacing, and every text block below it in the same column moves down by the same amount.

    Refused (nothing changes) when the moved text would run off the page, or when images,
    drawings or links sit in the area that would move -- they'd be left behind, misaligned.
    Without `grow`, text beyond the block's lines is reported as an overflow, as in FNT-11.
    The overrides restyle the paragraph as it is re-wrapped (EDT-17, see reflow_block).
    A block with several styles on a line is redrawn in its main style, and the note says so
    (EDT-19, see reflow_block)."""
    page = document.raw[page_index]
    reference = _block_reference(block)
    row_references = _row_references(block, reference)
    row_count = len(block.rows)
    if any(line.style.rotation_degrees != 0.0 for line in block.lines):
        raise OpValidationError("justifying or growing a rotated paragraph is not supported")
    font_size, text_state = _drawing_metrics(reference)
    if override_size is not None:
        font_size = override_size
    resolution = override_font or resolve_font_for_span(
        document, page_index, reference, new_text, font_index=font_index
    )
    font = _load_font(resolution)
    left = block.row_origin(0)[0]
    width = max(box[2] - box[0] for box in (block.row_bbox(i) for i in range(row_count)))
    wrapped = wrap_text(" ".join(new_text.split()), font, font_size, width)
    pitch = _line_pitch(block, font_size)
    baselines = [block.row_origin(i)[1] for i in range(row_count)]
    last_baseline = baselines[-1]
    extra = max(0, len(wrapped) - row_count)
    drawn_lines = wrapped if grow else wrapped[:row_count]
    baselines += [last_baseline + pitch * (i + 1) for i in range(extra)]

    moved_note = ""
    if grow and extra:
        shift = pitch * extra
        bottom = max(line.style.bbox[3] for line in block.lines)
        right = left + width
        own = {id(line) for line in block.lines}
        below = [
            span
            for span in extract_page_spans(document.raw, page_index)
            if id(span) not in own
            and span.style.bbox[1] >= bottom - 0.5
            and span.style.bbox[2] > left
            and span.style.bbox[0] < right
        ]
        blocking = _content_below(page, bottom, left, right)
        in_band = _content_below(page, bottom, left, right, until=bottom + shift)
        if in_band:
            raise OpValidationError(
                f"growing this paragraph would draw its new lines over {' and '.join(in_band)} below it; "
                "shorten the text, or allow overflow instead"
            )
        if blocking and below:
            raise OpValidationError(
                f"growing this paragraph would move text past {' and '.join(blocking)} below it, which "
                "can't be moved with it yet; shorten the text, or allow overflow instead"
            )
        limit = page_bounds(page).y1
        lowest = max([span.style.bbox[3] + shift for span in below] + [baselines[-1] + font_size * 0.25])
        if lowest > limit:
            raise OpValidationError(
                f"growing this paragraph by {extra} line(s) would push text {lowest - limit:.0f} pt "
                "off the bottom of the page"
            )
        # Bottom-up, so a block never lands on one that hasn't moved yet.
        for later in sorted(detect_blocks(below), key=lambda b: -b.lines[0].style.chars[0].origin[1]):
            move_resize_block(document, page_index, later, dy=shift, font_index=font_index, verify=False)
        if below:
            moved_note = f"; moved {len(below)} line(s) below it down {shift:.1f} pt"

    before = _capture(document, page_index, verify)
    _redact_spans(page, list(block.lines))
    results: list[EditResult] = []
    drawn: list[tuple[tuple[float, float], tuple[float, float], float]] = []
    for i, text in enumerate(drawn_lines):
        line = row_references[min(i, row_count - 1)]
        origin = (left, baselines[i])
        state = _drawing_metrics(line)[1] if line.text_state else text_state
        if justify and i < len(drawn_lines) - 1 and _stretches(text, width, font, font_size):
            end_point = _draw_justified(
                page,
                text,
                origin,
                width,
                font=font,
                font_size=font_size,
                color=override_color if override_color is not None else line.style.color,
                text_state=state,
                resolution=resolution,
            )
        else:
            end_point = draw_styled_text(
                page,
                text=text,
                origin=origin,
                font_size=font_size,
                color=override_color if override_color is not None else line.style.color,
                text_state=state,
                rotation_degrees=0.0,
                resolution=resolution,
            )
        drawn.append((origin, end_point, font_size))
        results.append(
            EditResult(
                tier=resolution.tier,
                confidence=resolution.confidence,
                requires_approval=resolution.requires_approval,
                note=resolution.note + _text_state_note(reference),
                end_point=end_point,
            )
        )
    if not results:
        return results
    if before is not None:
        verification = _verify_edit(document, page_index, before, drawn_lines[0], list(block.lines), drawn)
        results[-1] = dataclasses.replace(results[-1], verification=verification)
    if extra and not grow:
        note = f"{results[-1].note}; overflow: {extra} more line(s) needed than this block has ({row_count}), not drawn"
        results[-1] = dataclasses.replace(results[-1], requires_approval=True, note=note)
    elif moved_note:
        results[-1] = dataclasses.replace(results[-1], note=results[-1].note + moved_note)
    lost = _style_loss_note(block, reference)
    if lost:
        results[-1] = dataclasses.replace(results[-1], requires_approval=True, note=results[-1].note + lost)
    return results


# -- EDT-17 / EDT-18: editing, moving and deleting part of a line ------------------------------
#
# Everything below changes some glyphs of a line and leaves every other glyph on the page
# exactly where it is (SPEC.md 8.2 item 6, "Fidelity"). Removal is by _redact_chars, which
# never moves a neighbour. Glyphs that must be drawn again -- a moved word, the rest of a line
# that shifts because an edit changed a width -- go through _faithful_pieces, so each lands
# at its original position plus the intended offset, never where a fresh layout would put it.

GlyphRange = tuple[SpanTrace, int, int]
"""Characters ``[start, end)`` of one span (indices into ``SpanStyle.chars``)."""

_PIECE_DRIFT_PT = 0.25
"""How far a redrawn glyph may land from where the original sat before its run is split."""
_EXACT_DRIFT_PT = 0.002
"""The same limit for a glyph that must stay exactly where it is: no more than rounding."""
_SAME_WIDTH_PT = 0.01
"""A width change smaller than this moves nothing: the rest of the line is left untouched."""
_UNKNOWN_CHAR = "�"
_TIER_RANK = {"exact": 0, "approximate": 1, "fallback": 2}


@dataclass(frozen=True)
class _Pen:
    """One style to draw in, resolved before anything on the page changes."""

    resolution: FontResolution
    font: pymupdf.Font
    font_size: float
    text_state: TextState
    color: tuple[float, float, float]
    rotation_degrees: float
    kern_pairs: dict[tuple[str, str], float]

    def offsets(self, text: str) -> list[float]:
        """Where draw_styled_text puts each character of `text`, as a distance along the
        baseline from the first one, plus where the pen ends up (one more entry than
        characters). The same arithmetic as the drawing, so it predicts it."""
        widths = self.font.char_lengths(text, fontsize=self.font_size)
        default = _is_default_spacing(self.text_state)
        offsets: list[float] = []
        x = 0.0
        previous: str | None = None
        for char, width in zip(text, widths, strict=True):
            if previous is not None and not default:
                x += self.kern_pairs.get((previous, char), 0.0) * self.font_size
            offsets.append(x)
            x += width if default else advance_for_char(width, char, self.text_state)
            previous = char
        return [*offsets, x]

    def width(self, text: str) -> float:
        return self.offsets(text)[-1] if text else 0.0


def _pen_for(
    span: SpanTrace,
    resolution: FontResolution,
    *,
    size: float | None = None,
    color: tuple[float, float, float] | None = None,
) -> _Pen:
    font_size, text_state = _drawing_metrics(span)
    kerned = resolution.font_bytes is not None and not _is_default_spacing(text_state)
    return _Pen(
        resolution=resolution,
        font=_load_font(resolution),
        font_size=size if size is not None else font_size,
        text_state=text_state,
        color=color if color is not None else span.style.color,
        rotation_degrees=span.style.rotation_degrees,
        kern_pairs=build_kern_pairs(resolution.font_bytes) if kerned and resolution.font_bytes else {},
    )


@dataclass(frozen=True)
class _Piece:
    """One draw_styled_text call: `text` starting at `origin`, in `pen`'s style."""

    text: str
    origin: tuple[float, float]
    pen: _Pen


def _direction(rotation_degrees: float) -> tuple[float, float]:
    angle = math.radians(rotation_degrees)
    return math.cos(angle), math.sin(angle)


def _along(point: tuple[float, float], direction: tuple[float, float]) -> float:
    return point[0] * direction[0] + point[1] * direction[1]


def _box_end(box: CharBox, direction: tuple[float, float]) -> float:
    """The far edge of a glyph's box along the writing direction: texttrace's box is as wide
    as the glyph's advance, so this is where the next glyph would start."""
    x0, y0, x1, y1 = box.bbox
    return max(_along(corner, direction) for corner in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)))


def _faithful_pieces(
    span: SpanTrace,
    start: int,
    end: int,
    pen: _Pen,
    *,
    dx: float = 0.0,
    dy: float = 0.0,
    drift: float = _PIECE_DRIFT_PT,
) -> list[_Piece]:
    """The draw calls that put characters ``[start, end)`` of `span` back exactly where
    they are, offset by (`dx`, `dy`).

    One call for the whole run is best (it stays one searchable span), but a fresh layout
    only reproduces the original when nothing but the font's own advances placed it. A
    ``TJ``-kerned or justified line, or a font whose /Widths differ from its program's,
    would come back with its glyphs shifted. So the run's predicted positions are compared
    with the real ones: if any is more than `drift` off, the run is split at word
    boundaries, each word starting at its own original origin, and a word that still
    drifts is split further, at the glyph where the drift appears.

    Refused when a character's text is unknown (no ToUnicode entry): it can't be redrawn."""
    chars = span.style.chars[start:end]
    if not chars:
        return []
    if any(char.char == _UNKNOWN_CHAR for char in chars):
        raise OpValidationError(
            "this text has characters whose meaning the PDF doesn't record, so it can't be redrawn; nothing was changed"
        )
    direction = _direction(span.style.rotation_degrees)

    def fits(run: list[CharBox]) -> bool:
        offsets = pen.offsets("".join(char.char for char in run))
        x0, y0 = run[0].origin
        return all(
            math.dist((x0 + offset * direction[0], y0 + offset * direction[1]), char.origin) <= drift
            for offset, char in zip(offsets, run, strict=False)
        )

    def piece(run: list[CharBox]) -> _Piece:
        x, y = run[0].origin
        return _Piece(text="".join(char.char for char in run), origin=(x + dx, y + dy), pen=pen)

    if fits(chars):
        return [piece(chars)]
    words: list[list[CharBox]] = [[chars[0]]]
    for previous, char in itertools.pairwise(chars):
        if previous.char.isspace() and not char.char.isspace():
            words.append([char])
        else:
            words[-1].append(char)
    pieces: list[_Piece] = []
    for word in words:
        run = [word[0]]
        for char in word[1:]:
            if fits([*run, char]):
                run.append(char)
            else:
                pieces.append(piece(run))
                run = [char]
        pieces.append(piece(run))
    return pieces


_OFF_PAGE_TOLERANCE_PT = 1.0


def _off_page(bounds: pymupdf.Rect, point: tuple[float, float]) -> float:
    """How far `point` lies outside `bounds` (0 inside)."""
    x, y = point
    return max(bounds.x0 - x, x - bounds.x1, bounds.y0 - y, y - bounds.y1, 0.0)


def _check_on_page(page: pymupdf.Page, pieces: list[_Piece], *, overhang: float = 0.0) -> None:
    """Refuse text that would start, or run, off the page: a longer edit or a move could
    otherwise push the end of a line past the page edge, where no one would see it.

    `overhang` is how far the text being replaced already reached off the page. A line that
    already overhangs the edge (a PDF can place text anywhere) can still be edited,
    recolored or shortened; it just can't be pushed any further off."""
    bounds = page_bounds(page)
    limit = overhang + _OFF_PAGE_TOLERANCE_PT
    for item in pieces:
        direction = _direction(item.pen.rotation_degrees)
        width = item.pen.width(item.text)
        start = item.origin
        end = (start[0] + width * direction[0], start[1] + width * direction[1])
        if _off_page(bounds, start) > limit:
            raise OpValidationError(
                f"the text would be moved off the page (it would start at {start[0]:.0f}, {start[1]:.0f})"
            )
        if _off_page(bounds, end) > limit:
            raise OpValidationError(
                f"the text would run off the page (it would end at {end[0]:.0f}, {end[1]:.0f}); "
                "shorten it or move it first"
            )


def _overhang(page: pymupdf.Page, redact: list[tuple[SpanTrace, int]]) -> float:
    """How far the glyphs being removed already reach off the page."""
    bounds = page_bounds(page)
    far = 0.0
    for span, index in redact:
        char = span.style.chars[index]
        direction = _direction(span.style.rotation_degrees)
        advance = _box_end(char, direction) - _along(char.origin, direction)
        end = (char.origin[0] + advance * direction[0], char.origin[1] + advance * direction[1])
        far = max(far, _off_page(bounds, char.origin), _off_page(bounds, end))
    return far


def _leftover_runs(
    redact: list[tuple[SpanTrace, int]],
) -> tuple[list[SpanTrace], list[_DrawnLine], list[tuple[float, float, float, float]]]:
    """The spans that lose glyphs, and where the glyphs they keep still are.

    Removing part of a span splits what is left into new spans, which _verify_edit would
    otherwise report as text appearing where nothing was drawn. They go in its `drawn`
    list; the original spans go in `removed`."""
    by_span: dict[int, tuple[SpanTrace, set[int]]] = {}
    for span, char_index in redact:
        by_span.setdefault(span.style.span_index, (span, set()))[1].add(char_index)
    spans: list[SpanTrace] = []
    kept: list[_DrawnLine] = []
    glyphs: list[tuple[float, float, float, float]] = []
    for span, gone in by_span.values():
        spans.append(span)
        run: list[CharBox] = []
        for index, char in enumerate([*span.style.chars, None]):
            if char is not None and index not in gone:
                run.append(char)
                if not char.char.isspace():
                    glyphs.append(char.bbox)
            elif run:
                kept.append((run[0].origin, run[-1].origin, span.style.size))
                run = []
    return spans, kept, glyphs


def _change_glyphs(
    document: Document,
    page_index: int,
    *,
    redact: list[tuple[SpanTrace, int]],
    pieces: list[_Piece],
    expected_text: str,
    verify: bool,
    target_page_index: int | None = None,
    allow_overlap: bool = False,
    note: str = "",
    requires_approval: bool = False,
) -> EditResult:
    """Remove the `redact` glyphs from `page_index` and draw `pieces` on the target page,
    as one verified change. Every piece's font was resolved by the caller, before this
    removes anything -- a resolution reads the page as it was."""
    target_index = page_index if target_page_index is None else target_page_index
    target_page = document.raw[target_index]
    overhang = _overhang(document.raw[page_index], redact) if target_index == page_index else 0.0
    _check_on_page(target_page, pieces, overhang=overhang)
    before = _capture(document, target_index, verify)
    if redact:
        _redact_chars(document.raw[page_index], [span.style.chars[index] for span, index in redact])

    drawn: list[_DrawnLine] = []
    for item in pieces:
        end = draw_styled_text(
            target_page,
            text=item.text,
            origin=item.origin,
            font_size=item.pen.font_size,
            color=item.pen.color,
            text_state=item.pen.text_state,
            rotation_degrees=item.pen.rotation_degrees,
            resolution=item.pen.resolution,
        )
        drawn.append((item.origin, end, item.pen.font_size))
    end_point = drawn[-1][1] if drawn else redact[0][0].style.chars[redact[0][1]].origin

    removed, kept, kept_glyphs = _leftover_runs(redact) if target_index == page_index else ([], [], [])
    verification = None
    if before is not None:
        verification = _verify_edit(
            document,
            target_index,
            before,
            expected_text,
            removed,
            drawn + kept,
            allow_overlap=allow_overlap,
            kept=kept_glyphs,
        )

    resolutions = [item.pen.resolution for item in pieces]
    weakest = max(resolutions, key=lambda r: (_TIER_RANK.get(r.tier, 2), -r.confidence), default=None)
    return EditResult(
        tier=weakest.tier if weakest else TIER_EXACT,
        confidence=weakest.confidence if weakest else 1.0,
        requires_approval=requires_approval or any(r.requires_approval for r in resolutions),
        note=((weakest.note if weakest else "") + note).lstrip("; "),
        end_point=end_point,
        verification=verification,
    )


class _Pens:
    """One font resolution per span, made on first use (always before the page changes),
    and one pen per span and color."""

    def __init__(self, document: Document, page_index: int, font_index: list[FontCandidate]) -> None:
        self._document = document
        self._page_index = page_index
        self._font_index = font_index
        self._resolutions: dict[int, FontResolution] = {}
        self._pens: dict[tuple[int, tuple[float, float, float] | None], _Pen] = {}

    def resolution(self, span: SpanTrace) -> FontResolution:
        index = span.style.span_index
        if index not in self._resolutions:
            # The whole span's text: every glyph of it that gets redrawn is covered by one resolution.
            self._resolutions[index] = resolve_font_for_span(
                self._document, self._page_index, span, span.style.text, font_index=self._font_index
            )
        return self._resolutions[index]

    def pen(self, span: SpanTrace, color: tuple[float, float, float] | None = None) -> _Pen:
        index = span.style.span_index
        if (index, color) not in self._pens:
            self._pens[index, color] = _pen_for(span, self.resolution(span), color=color)
        return self._pens[index, color]


def move_glyph_ranges(
    document: Document,
    page_index: int,
    ranges: list[GlyphRange],
    *,
    dx: float = 0.0,
    dy: float = 0.0,
    keep_original: bool = False,
    target_page_index: int | None = None,
    font_index: list[FontCandidate],
    verify: bool = True,
) -> EditResult:
    """EDT-18: move the glyphs of `ranges` (a word or a line, across however many spans)
    by (`dx`, `dy`) page points, each glyph landing at its own origin plus that offset.
    Every other glyph on the page, the rest of the same line included, is left alone.

    With `keep_original` the glyphs are copied instead, onto `target_page_index` if given
    (paste and duplicate); a copy may overlap other text.

    FNT-20: refused outright, nothing moved, when a range holds an icon/emoji glyph
    (engine.fonts.icons) -- moving or copying redraws it at a new position, and it can't
    be reproduced exactly (most commonly a Type3 glyph, FNT-15)."""
    ranges = [(span, start, end) for span, start, end in ranges if end > start]
    if not ranges:
        raise OpValidationError("there is no text to move")
    for span, start, end in ranges:
        icon = next(
            (char.char for char in span.style.chars[start:end] if is_icon_char(char.char, span.style.font)), None
        )
        if icon is not None:
            raise OpValidationError(
                f"the icon {icon!r} can't be moved or copied yet (it can't be redrawn exactly); "
                "move the surrounding text instead, or delete the icon on its own"
            )
    target_index = page_index if target_page_index is None else target_page_index
    if target_index != page_index and not keep_original:
        raise OpValidationError("text can only be copied to another page, not moved there")

    pens = _Pens(document, page_index, font_index)
    pieces: list[_Piece] = []
    for span, start, end in ranges:
        pieces += _faithful_pieces(span, start, end, pens.pen(span), dx=dx, dy=dy)
    redact = [] if keep_original else [(span, index) for span, start, end in ranges for index in range(start, end)]
    return _change_glyphs(
        document,
        page_index,
        redact=redact,
        pieces=pieces,
        expected_text="".join(item.text for item in pieces),
        verify=verify,
        target_page_index=target_index,
        allow_overlap=keep_original,
    )


def _line_glyphs(line: TextLine, spans: list[SpanTrace]) -> list[tuple[SpanTrace, int] | None]:
    """Each character of the line's text as (span, char index); None for a synthetic space."""
    try:
        glyphs = [None if entry is None else (spans[entry[0]], entry[1]) for entry in line.glyph_map]
        if any(glyph is not None and glyph[1] >= len(glyph[0].style.chars) for glyph in glyphs):
            raise IndexError
    except IndexError as exc:
        raise OpValidationError("the line no longer matches the page; check the page again") from exc
    return glyphs


_GUTTER_RATIO = 0.8
"""A gap between two spans wider than this many font sizes separates columns within one
line unit (the same span-boundary rule engine.fonts.blocks uses): an edit on one side of it
never moves the other side."""


def _line_parts(glyphs: list[tuple[SpanTrace, int] | None], direction: tuple[float, float]) -> list[int]:
    """For each character of a line, which of the line's column parts it is in. A part ends
    at a gap between two *spans* wider than _GUTTER_RATIO x the font size; a gap inside one
    span (a TJ offset between words) never ends one."""
    parts: list[int] = []
    part = 0
    previous: tuple[SpanTrace, int] | None = None
    for glyph in glyphs:
        if glyph is not None and previous is not None and glyph[0] is not previous[0]:
            before = previous[0].style.chars[previous[1]]
            here = glyph[0].style.chars[glyph[1]]
            gap = _along(here.origin, direction) - _box_end(before, direction)
            if gap > _GUTTER_RATIO * max(glyph[0].style.size, previous[0].style.size):
                part += 1
        parts.append(part)
        if glyph is not None:
            previous = glyph
    return parts


def _line_alignment(
    document: Document,
    page_index: int,
    line: TextLine,
    spans: list[SpanTrace],
    offsets: list[int] | None = None,
) -> str:
    """detect_alignment for a whole line, whatever spans it is made of -- or, given
    `offsets`, for just those characters of it (one column part)."""
    every = _line_glyphs(line, spans)
    chosen = range(len(every)) if offsets is None else offsets
    glyphs = [glyph for offset in chosen if (glyph := every[offset]) is not None]
    first = glyphs[0][0]
    chars = [span.style.chars[index] for span, index in glyphs]
    boxes = [char.bbox for char in chars]
    bbox = (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )
    style = first.style.model_copy(
        update={
            "text": "".join(line.text[offset] for offset in chosen),
            "bbox": bbox,
            "chars": chars,
            "rotation_degrees": 0.0 if abs(line.rotation_degrees) < 0.05 else line.rotation_degrees,
        }
    )
    trace = first.model_copy(update={"style": style})
    return detect_alignment(trace, spans, page_bounds(document.raw[page_index]).width)


_Color = tuple[float, float, float]
_OUT_OF_ORDER_NOTE = (
    "; the unchanged part of the line could not be redrawn exactly, so it was left as it is: "
    "copied or extracted text may read this line out of order"
)


def _relayout_line(
    document: Document,
    page_index: int,
    line: TextLine,
    spans: list[SpanTrace],
    *,
    removed: list[int],
    shifts: dict[int, float],
    new_pieces: list[tuple[int, _Piece]],
    recolor: dict[int, _Color] | None = None,
    font_index: list[FontCandidate],
    verify: bool,
    note: str,
    requires_approval: bool = False,
) -> EditResult:
    """Remove the characters at the `removed` offsets of the line's text, slide each
    character in `shifts` along the baseline by its distance, give those in `recolor` a
    new color, and draw each of `new_pieces` (which reads at its offset of the line).

    Reading order. New drawing is appended to the page's content, and plain text
    extraction (``Page.get_text()``, copy and paste in any viewer) reads content in the
    order it was drawn: a line left half in place and half redrawn read as "Alpha Brav",
    two other lines, then "o Charlie". So when anything on the line is drawn, the whole
    line is drawn again, left to right. The characters that are not changing are redrawn
    in exactly the same place (_EXACT_DRIFT_PT) in exactly the same font -- the same
    pixels. When that can't be promised (their font only resolves to a look-alike, or
    their text is unknown), they are not touched, the changed parts are still drawn in
    reading order, and the result's note says extraction may read out of order.

    FNT-20: refused outright, nothing changed, if an icon/emoji glyph (engine.fonts.icons)
    not being removed would have to shift -- a glyph that can't be redrawn exactly (most
    commonly Type3, FNT-15) would be lost if something tried to slide it to a new spot."""
    glyphs = _line_glyphs(line, spans)
    direction = _direction(line.rotation_degrees)
    pens = _Pens(document, page_index, font_index)
    gone = set(removed)
    colors = recolor or {}
    icons = icon_members(line.icon_ranges)
    moved_icon = next((offset for offset in icons if offset not in gone and abs(shifts.get(offset, 0.0)) > 1e-6), None)
    if moved_icon is not None:
        raise OpValidationError(
            f"the icon {line.text[moved_icon]!r} can't be shifted yet (it can't be redrawn exactly); "
            "try a change that doesn't move text past it"
        )
    kept = [(offset, glyph) for offset, glyph in enumerate(glyphs) if glyph is not None and offset not in gone]

    def changes(offset: int) -> bool:
        return abs(shifts.get(offset, 0.0)) > 1e-6 or offset in colors

    # Consecutive characters of one span that change the same way are one run.
    Run = tuple[int, SpanTrace, int, int, float, _Color | None]
    runs: list[Run] = []
    for offset, (span, index) in kept:
        shift = shifts.get(offset, 0.0) if changes(offset) else 0.0
        color = colors.get(offset)
        last = runs[-1] if runs else None
        if last and last[1] is span and last[3] == index and last[4] == shift and last[5] == color:
            runs[-1] = (last[0], span, last[2], index + 1, shift, color)
        else:
            runs.append((offset, span, index, index + 1, shift, color))

    def is_changed(run: Run) -> bool:
        return abs(run[4]) > 1e-6 or run[5] is not None

    draws = bool(new_pieces) or any(is_changed(run) for run in runs)
    if draws and not all(is_changed(run) or _redraws_exactly(pens, run[1], run[2], run[3]) for run in runs):
        runs = [run for run in runs if is_changed(run)]  # leave the unchanged characters alone
        note += _OUT_OF_ORDER_NOTE
    elif not draws:
        runs = []  # nothing is drawn: what stays is already in order, in place

    pieces: list[_Piece] = []
    waiting = sorted(new_pieces, key=lambda item: item[0])
    for offset, span, first, last_index, shift, color in runs:
        while waiting and waiting[0][0] <= offset:
            pieces.append(waiting.pop(0)[1])
        pieces += _faithful_pieces(
            span,
            first,
            last_index,
            pens.pen(span, color),
            dx=shift * direction[0],
            dy=shift * direction[1],
            drift=_PIECE_DRIFT_PT if abs(shift) > 1e-6 else _EXACT_DRIFT_PT,
        )
    pieces += [piece for _at, piece in waiting]

    redact = [glyph for offset in sorted(gone) if (glyph := glyphs[offset]) is not None]
    redact += [(span, index) for _o, span, first, last_index, _s, _c in runs for index in range(first, last_index)]
    if not redact and not pieces:
        raise OpValidationError("nothing on this line would change")
    return _change_glyphs(
        document,
        page_index,
        redact=redact,
        pieces=pieces,
        # Everything drawn, in the order drawn: the whole line when it was redrawn in order.
        expected_text="".join(piece.text for piece in pieces),
        verify=verify,
        note=note,
        requires_approval=requires_approval,
    )


def _redraws_exactly(pens: _Pens, span: SpanTrace, start: int, end: int) -> bool:
    """Whether characters ``[start, end)`` of `span` can be drawn again as the very same
    glyphs: their text is known, and their font resolves to the document's own."""
    if any(char.char == _UNKNOWN_CHAR for char in span.style.chars[start:end]):
        return False
    try:
        return pens.resolution(span).tier == TIER_EXACT
    except (FontResourceNotFoundError, OpValidationError):
        return False


@dataclass(frozen=True)
class LineRestyle:
    """EDT-17: how rewrite_line_hunks restyles what it draws. Each hunk is restyled from its
    *own* run's style, so a line or word made of several runs keeps them distinct."""

    size_scale: float | None = None
    """Multiply every run's size by this (a superscript stays proportionally smaller, and
    keeps its raised baseline). None keeps each run's size."""
    color: _Color | None = None
    font_for: Callable[[SpanTrace, str], FontResolution | None] | None = None
    """The font to draw a hunk's text in, given the span whose style it takes (so a
    weight or slant change toggles each run within its own family). None, or a None
    answer, keeps that run's own font."""


def _style_key(span: SpanTrace) -> tuple[str, float, _Color]:
    return span.style.font, round(span.style.size, 2), span.style.color


def _is_icon_glyph(glyph: tuple[SpanTrace, int] | None) -> bool:
    if glyph is None:
        return False
    span, index = glyph
    return is_icon_char(span.style.chars[index].char, span.style.font)


def _style_anchor(glyphs: list[tuple[SpanTrace, int] | None], start: int, end: int) -> tuple[SpanTrace, int] | None:
    """The character whose style a hunk's new text takes: its first character; for an
    insertion, the character it follows (appending to a bold word stays bold), unless that
    is a space or an icon/emoji glyph (FNT-20, engine.fonts.icons) -- text inserted at a
    word's start, or right after an icon, takes the following text's style instead, never
    the icon's own (it can't be reproduced, most commonly Type3, FNT-15)."""
    if start == end:
        previous = glyphs[start - 1] if start > 0 else None
        following = glyphs[start] if start < len(glyphs) else None
        if previous is not None and not previous[0].style.chars[previous[1]].char.isspace():
            if not _is_icon_glyph(previous):
                return previous
            return following or previous
        return following or previous
    real = [glyph for glyph in glyphs[start:end] if glyph is not None]
    if real:
        return real[0]
    previous = glyphs[start - 1] if start > 0 else None
    return previous or (glyphs[end] if end < len(glyphs) else None)


def _hunk_origin(
    glyphs: list[tuple[SpanTrace, int] | None],
    start: int,
    direction: tuple[float, float],
    *,
    after_previous: bool = False,
) -> tuple[float, float] | None:
    """Where text replacing or inserted at `start` begins: that character's own origin, or,
    at a gap with no glyph or at the line's end, just past the character before it. Text
    appended to a character (`after_previous`) starts just past it, on its baseline -- text
    added to a superscript stays raised."""
    here = glyphs[start] if start < len(glyphs) else None
    if here is not None and not after_previous:
        return here[0].style.chars[here[1]].origin
    previous = glyphs[start - 1] if start > 0 else None
    if previous is None:
        return None
    box = previous[0].style.chars[previous[1]]
    advance = _box_end(box, direction) - _along(box.origin, direction)
    return (box.origin[0] + advance * direction[0], box.origin[1] + advance * direction[1])


def rewrite_line_hunks(
    document: Document,
    page_index: int,
    line: TextLine,
    hunks: list[tuple[int, int, str]],
    *,
    restyle: LineRestyle | None = None,
    font_index: list[FontCandidate],
    verify: bool = True,
) -> EditResult:
    """EDT-17: replace each hunk ``(start, end, text)`` -- characters ``[start, end)`` of
    `line`'s text -- with its text, as one change. Empty text deletes; ``start == end``
    inserts. Each hunk's new text is drawn in its own run's style (see _style_anchor), with
    `restyle` applied, starting where the characters it replaces started.

    Only the hunks change. Where their new text is as wide as the old, every other glyph on
    the page stays where it is. Where it is wider or narrower, the rest of the *same line*
    slides by the difference, following the line's alignment (detect_alignment): on a
    left-aligned line what follows a hunk moves, on a right-aligned one what precedes it
    (and the hunk), on a centered one both by half. Every moved glyph keeps its position
    relative to its neighbours (_faithful_pieces), and the line's other characters are
    drawn again where they are, so the line still reads in order (see _relayout_line).

    A hunk whose old characters come from runs of different styles is drawn in the first
    one's: the result's note says so and asks for approval, rather than flattening them
    silently."""
    ordered = sorted(hunks)
    if not ordered:
        raise OpValidationError("there is nothing to change on this line")
    previous_end = 0
    for start, end, _text in ordered:
        if not previous_end <= start <= end <= len(line.text):
            raise OpValidationError(f"characters {start}-{end} are outside the line, or overlap another change")
        previous_end = end
    spans = extract_page_spans(document.raw, page_index)
    glyphs = _line_glyphs(line, spans)
    direction = _direction(line.rotation_degrees)
    restyle = restyle or LineRestyle()
    icons = icon_members(line.icon_ranges)

    planned: list[tuple[int, int, tuple[float, float], _Pen | None, float]] = []
    removed: list[int] = []
    mixed = False
    for start, end, text in ordered:
        anchor = _style_anchor(glyphs, start, end)
        appended = start == end and start > 0 and anchor is not None and anchor == glyphs[start - 1]
        origin = _hunk_origin(glyphs, start, direction, after_previous=appended)
        if origin is None or (text and anchor is None):
            raise OpValidationError("an edit has to start at a character of the line")
        real = [offset for offset in range(start, end) if glyphs[offset] is not None]
        # FNT-20: deleting an icon (text == "") is fine -- nothing is drawn through its font.
        # Drawing new text at or over one would be: it can't be reproduced exactly (most
        # commonly a Type3 glyph, FNT-15), so that specific change is refused, nothing else.
        if text and any(offset in icons for offset in range(start, end)):
            raise OpValidationError(
                f"this change would need to redraw the icon {line.text[start:end]!r}, which can't be "
                "reproduced exactly; edit only the text around it, or delete the icon on its own"
            )
        removed += real
        pen = None
        if text:
            assert anchor is not None  # nosec B101 -- type narrowing: refused just above when None
            span = anchor[0]
            chosen = restyle.font_for(span, text) if restyle.font_for is not None else None
            resolution = chosen or resolve_font_for_span(document, page_index, span, text, font_index=font_index)
            size = _drawing_metrics(span)[0] * restyle.size_scale if restyle.size_scale is not None else None
            pen = _pen_for(span, resolution, size=size, color=restyle.color)
            styles = {_style_key(glyph[0]) for offset in real if (glyph := glyphs[offset]) is not None}
            mixed = mixed or len(styles) > 1
        follower = glyphs[end] if end < len(glyphs) else None
        if follower is not None:
            old_width = _along(follower[0].style.chars[follower[1]].origin, direction) - _along(origin, direction)
        elif real:
            last = glyphs[real[-1]]
            assert last is not None  # nosec B101 -- type narrowing: `real` holds only real glyphs
            old_width = _box_end(last[0].style.chars[last[1]], direction) - _along(origin, direction)
        else:
            old_width = 0.0
        new_width = pen.width(text) if pen is not None else 0.0
        planned.append((start, end, origin, pen, new_width - old_width))

    deltas = [delta for *_rest, delta in planned]
    parts = _line_parts(glyphs, direction)

    def part_of(start: int) -> int:
        return parts[min(start, len(parts) - 1)] if parts else 0

    hunk_parts = [part_of(start) for start, *_rest in planned]
    alignments: dict[int, str] = {}
    note = ""
    for part in sorted(set(hunk_parts)):
        changed = any(abs(d) > _SAME_WIDTH_PT for d, hp in zip(deltas, hunk_parts, strict=True) if hp == part)
        members = [offset for offset, p in enumerate(parts) if p == part]
        alignment = _line_alignment(document, page_index, line, spans, members) if changed else "left"
        alignments[part] = alignment
        if alignment != "left" and f"{alignment} alignment" not in note:
            note += f"; kept the line's {alignment} alignment"

    def shift(part: int, before: float, after: float) -> float:
        alignment = alignments.get(part, "left")
        if alignment == "right":
            return -after
        if alignment == "center":
            return (before - after) / 2
        return before

    def in_part(part: int, pairs: list[tuple[int, float]]) -> float:
        return sum(delta for hunk_part, delta in pairs if hunk_part == part)

    shifts: dict[int, float] = {}
    gone = set(removed)
    for offset, glyph in enumerate(glyphs):
        if glyph is None or offset in gone:
            continue
        # Only changes in this character's own column part move it.
        part = parts[offset]
        before = [(hp, d) for (_s, end, *_r), d, hp in zip(planned, deltas, hunk_parts, strict=True) if end <= offset]
        after = [(hp, d) for (start, *_r), d, hp in zip(planned, deltas, hunk_parts, strict=True) if start > offset]
        shifts[offset] = shift(part, in_part(part, before), in_part(part, after))
    new_pieces: list[tuple[int, _Piece]] = []
    pairs = list(zip(hunk_parts, deltas, strict=True))
    for k, ((start, _end, origin, pen, _delta), (_s, _e, text)) in enumerate(zip(planned, ordered, strict=True)):
        if pen is None:
            continue
        part = hunk_parts[k]
        distance = shift(part, in_part(part, pairs[:k]), in_part(part, pairs[k:]))
        placed = (origin[0] + distance * direction[0], origin[1] + distance * direction[1])
        new_pieces.append((start, _Piece(text=text, origin=placed, pen=pen)))
    if mixed:
        note += "; new text that replaced differently styled runs was drawn in the first run's style"
    anchor_spans = [glyph[0] for start, end, _t in ordered if (glyph := _style_anchor(glyphs, start, end))]
    if anchor_spans:
        note = _text_state_note(anchor_spans[0]) + note
    return _relayout_line(
        document,
        page_index,
        line,
        spans,
        removed=removed,
        shifts=shifts,
        new_pieces=new_pieces,
        font_index=font_index,
        verify=verify,
        note=note,
        requires_approval=mixed,
    )


def rewrite_line_range(
    document: Document,
    page_index: int,
    line: TextLine,
    start: int,
    end: int,
    new_text: str,
    *,
    override_size: float | None = None,
    override_color: tuple[float, float, float] | None = None,
    override_font: FontResolution | None = None,
    font_index: list[FontCandidate],
    verify: bool = True,
) -> EditResult:
    """EDT-17: replace characters ``[start, end)`` of `line`'s text with `new_text`, as one
    hunk of rewrite_line_hunks; `override_size` is the size the hunk's own run is drawn at.
    Empty `new_text` is refused: delete the unit instead."""
    if not new_text:
        raise OpValidationError("the new text is empty; delete the text instead")
    if not 0 <= start <= end <= len(line.text):
        raise OpValidationError(f"characters {start}-{end} are outside the line ({len(line.text)} characters)")
    scale = None
    if override_size is not None:
        anchor = _style_anchor(_line_glyphs(line, extract_page_spans(document.raw, page_index)), start, end)
        if anchor is None:
            raise OpValidationError("an edit has to start at a character of the line")
        scale = override_size / _drawing_metrics(anchor[0])[0]
    restyle = LineRestyle(
        size_scale=scale,
        color=override_color,
        font_for=(lambda _span, _text: override_font) if override_font is not None else None,
    )
    return rewrite_line_hunks(
        document, page_index, line, [(start, end, new_text)], restyle=restyle, font_index=font_index, verify=verify
    )


def recolor_line_range(
    document: Document,
    page_index: int,
    line: TextLine,
    start: int,
    end: int,
    color: tuple[float, float, float],
    *,
    font_index: list[FontCandidate],
    verify: bool = True,
) -> EditResult:
    """EDT-17: give characters ``[start, end)`` of `line`'s text a new color. Each glyph is
    drawn again exactly where it is, in its own font and size, so nothing on the line moves.

    FNT-20: an icon/emoji glyph (engine.fonts.icons) in the range keeps its own color --
    recoloring would need to redraw it, and it can't be reproduced exactly (most commonly a
    Type3 glyph, FNT-15). Silent, not refused: the rest of the range still recolors."""
    if not 0 <= start < end <= len(line.text):
        raise OpValidationError(f"characters {start}-{end} are outside the line ({len(line.text)} characters)")
    spans = extract_page_spans(document.raw, page_index)
    glyphs = _line_glyphs(line, spans)
    recolor = {
        offset: color
        for offset in range(start, end)
        if (glyph := glyphs[offset]) is not None and not is_icon_char(line.text[offset], glyph[0].style.font)
    }
    if not recolor:
        raise OpValidationError("this text is all icon glyphs, which keep their own color; nothing was changed")
    return _relayout_line(
        document,
        page_index,
        line,
        spans,
        removed=[],
        shifts={},
        new_pieces=[],
        recolor=recolor,
        font_index=font_index,
        verify=verify,
        note="",
    )


def delete_line_words(
    document: Document,
    page_index: int,
    line: TextLine,
    words: list[TextWord],
    *,
    close_gap: bool = True,
    font_index: list[FontCandidate],
    verify: bool = True,
) -> EditResult:
    """EDT-18: delete `words` (all on `line`), each with one adjacent space -- the one
    after it, or the one before it for the line's last word.

    With `close_gap`, the rest of the line closes up according to its alignment: on a
    left-aligned line what follows a deleted word moves left, on a right-aligned line what
    precedes it moves right, and on a centered line both move half way. Without it, nothing
    else on the line moves. Other lines are never touched."""
    if not words:
        raise OpValidationError("there are no words to delete")
    if any(word.line_index != line.index for word in words):
        raise OpValidationError("the words to delete are not all on the same line")
    spans = extract_page_spans(document.raw, page_index)
    glyphs = _line_glyphs(line, spans)
    text = line.text
    direction = _direction(line.rotation_degrees)

    ranges: list[list[int]] = []
    for word in sorted(words, key=lambda w: w.line_start):
        a, b = word.line_start, word.line_end
        if b < len(text) and text[b].isspace():
            b += 1
        elif a > 0 and text[a - 1].isspace() and not (ranges and ranges[-1][1] >= a):
            a -= 1
        if ranges and a <= ranges[-1][1]:
            ranges[-1][1] = max(ranges[-1][1], b)
        else:
            ranges.append([a, b])
    gone = {offset for a, b in ranges for offset in range(a, b)}
    removed = sorted(offset for offset in gone if glyphs[offset] is not None)
    kept = [offset for offset in range(len(glyphs)) if offset not in gone and glyphs[offset] is not None]

    def box(offset: int) -> CharBox:
        glyph = glyphs[offset]
        assert glyph is not None  # nosec B101 -- type narrowing: only real glyph offsets are passed
        return glyph[0].style.chars[glyph[1]]

    shifts: dict[int, float] = {}
    note = f"deleted {len(words)} word(s)"
    if close_gap and kept:
        # A gap closes only within its own column part (see _line_parts): the other column of
        # a line unit that spans a narrow gutter never moves.
        parts = _line_parts(glyphs, direction)
        widths: list[float] = []
        range_parts: list[int] = []
        for a, b in ranges:
            real = [offset for offset in range(a, b) if glyphs[offset] is not None]
            part = parts[real[0]] if real else parts[min(a, len(parts) - 1)]
            range_parts.append(part)
            before = [offset for offset in kept if offset < a and parts[offset] == part]
            after = [offset for offset in kept if offset >= b and parts[offset] == part]
            if real and after:
                widths.append(_along(box(after[0]).origin, direction) - _along(box(real[0]).origin, direction))
            elif real and before:
                widths.append(_box_end(box(real[-1]), direction) - _box_end(box(before[-1]), direction))
            else:
                widths.append(0.0)
        alignments = {
            part: _line_alignment(document, page_index, line, spans, [o for o, p in enumerate(parts) if p == part])
            for part in set(range_parts)
        }
        for offset in kept:
            part = parts[offset]
            alignment = alignments.get(part, "left")
            same = [(a, b, w) for (a, b), w, rp in zip(ranges, widths, range_parts, strict=True) if rp == part]
            closed_before = sum(w for _a, b, w in same if b <= offset)
            closed_after = sum(w for a, _b, w in same if a > offset)
            if alignment == "right":
                shifts[offset] = closed_after
            elif alignment == "center":
                shifts[offset] = (closed_after - closed_before) / 2
            else:
                shifts[offset] = -closed_before
        if any(abs(shift) > 1e-6 for shift in shifts.values()):
            names = sorted(set(alignments.values()))
            note += f"; closed the gap ({'/'.join(names)}-aligned line)"
    return _relayout_line(
        document,
        page_index,
        line,
        spans,
        removed=removed,
        shifts=shifts,
        new_pieces=[],
        font_index=font_index,
        verify=verify,
        note="; " + note,
    )


def delete_glyph_ranges(
    document: Document, page_index: int, ranges: list[GlyphRange], *, verify: bool = True
) -> EditResult:
    """EDT-18: remove the glyphs of `ranges` (a whole line, say) and nothing else;
    no other glyph moves."""
    redact = [(span, index) for span, start, end in ranges for index in range(start, end)]
    if not redact:
        raise OpValidationError("there is no text to delete")
    return _change_glyphs(
        document,
        page_index,
        redact=redact,
        pieces=[],
        expected_text="",
        verify=verify,
        note=f"deleted {len(redact)} character(s)",
    )
