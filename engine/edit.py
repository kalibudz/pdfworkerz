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
import math
import re
from dataclasses import dataclass

import numpy as np
import pikepdf
import pymupdf
from numpy.typing import NDArray

from engine.document import Document
from engine.errors import FontResourceNotFoundError, OpValidationError
from engine.fonts import research as font_research
from engine.fonts.blocks import TextBlock, detect_alignment, detect_blocks
from engine.fonts.classify import classify_font_xref, split_subset_tag
from engine.fonts.fit import fit_to_width
from engine.fonts.kerning import build_kern_pairs
from engine.fonts.match import FontCandidate, loose_font_key, normalize_font_name
from engine.fonts.merge import program_postscript_name, with_postscript_name
from engine.fonts.reflow import wrap_text
from engine.fonts.resolve import TIER_EXACT, FontResolution, embedded_program_covers, resolve_font
from engine.fonts.style import SpanTrace, TextState, advance_for_char, dedupe_texttrace, extract_page_spans
from engine.fonts.tounicode import has_cmap, unicode_to_glyph_ids, with_cmap
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


def _span_keys(page: pymupdf.Page) -> frozenset[tuple[str, tuple[float, ...]]]:
    return frozenset(
        ("".join(chr(c[0]) for c in span["chars"]), tuple(round(v, 2) for v in span["bbox"]))
        for span in dedupe_texttrace(page.get_texttrace())
    )


def _capture(document: Document, page_index: int, verify: bool) -> _BeforeEdit | None:
    if not verify:
        return None
    return _BeforeEdit(
        render=render_to_array(document.raw, page_index, dpi=VERIFY_DPI),
        span_keys=_span_keys(document.raw[page_index]),
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
    text_matches = expected_text in flat_text if expected_text else True

    removed_keys = {(span.style.text, tuple(round(v, 2) for v in span.style.bbox)) for span in removed}
    untouched = [pymupdf.Rect(key[1]) for key in before.span_keys if key not in removed_keys]
    new_boxes = [pymupdf.Rect(key[1]) for key in _span_keys(page) if key not in before.span_keys]
    overlaps = any(_overlap(box, other) for box in new_boxes for other in untouched)
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


def _redact_spans(page: pymupdf.Page, spans: list[SpanTrace]) -> None:
    """Remove exactly the glyphs of `spans`, and nothing else.

    A redaction rectangle removes every glyph whose box touches it, and a span's
    bbox runs from ascender to descender -- so with ordinary leading it reached
    into the lines above and below and deleted them too. A tiny rectangle at
    each glyph's own centre touches only that glyph (rotated text included).
    The glyph count is then checked: if anything besides these spans vanished,
    or some of their glyphs survived, the edit is refused rather than kept."""
    targets = [char for span in spans for char in span.style.chars]
    if not targets:
        return
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
    if len(block.lines) >= 2:
        return block.lines[1].style.chars[0].origin[1] - block.lines[0].style.chars[0].origin[1]
    leading = _drawing_metrics(block.lines[0])[1].leading
    return leading if leading > 0 else font_size * 1.2


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
    """
    if not block.lines:
        return []
    target_index = page_index if target_page_index is None else target_page_index
    if target_index != page_index and not keep_original:
        raise OpValidationError("a text block can only be copied to another page, not moved there")
    page = document.raw[target_index]
    reference = block.lines[0]
    font_size, text_state = _drawing_metrics(reference)
    resolution = resolve_font_for_span(document, page_index, reference, block.text, font_index=font_index)

    first_x, first_y = reference.style.chars[0].origin
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
            (text, (first_x + dx, first_y + dy + i * pitch), block.lines[min(i, len(block.lines) - 1)])
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
        note=f"deleted {len(block.lines)} line(s)",
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
    """
    if not block.lines:
        return []
    if align not in ("left", "justify"):
        raise OpValidationError(f"unknown alignment {align!r}: use left or justify")
    if align == "justify" or grow:
        return reflow_paragraph(
            document,
            page_index,
            block,
            new_text,
            font_index=font_index,
            verify=verify,
            justify=align == "justify",
            grow=grow,
        )

    reference = block.lines[0]
    font_size, _text_state = _drawing_metrics(reference)
    resolution = resolve_font_for_span(document, page_index, reference, new_text, font_index=font_index)
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
) -> list[EditResult]:
    """FNT-17: re-wrap `new_text` across the block's width, optionally justified, and, with
    `grow`, on as many lines as it needs: the extra lines continue at the block's own line
    spacing, and every text block below it in the same column moves down by the same amount.

    Refused (nothing changes) when the moved text would run off the page, or when images,
    drawings or links sit in the area that would move -- they'd be left behind, misaligned.
    Without `grow`, text beyond the block's lines is reported as an overflow, as in FNT-11."""
    page = document.raw[page_index]
    reference = block.lines[0]
    if any(line.style.rotation_degrees != 0.0 for line in block.lines):
        raise OpValidationError("justifying or growing a rotated paragraph is not supported")
    font_size, text_state = _drawing_metrics(reference)
    resolution = resolve_font_for_span(document, page_index, reference, new_text, font_index=font_index)
    font = _load_font(resolution)
    left = reference.style.chars[0].origin[0]
    width = max(line.style.bbox[2] - line.style.bbox[0] for line in block.lines)
    wrapped = wrap_text(" ".join(new_text.split()), font, font_size, width)
    pitch = _line_pitch(block, font_size)
    last_baseline = block.lines[-1].style.chars[0].origin[1]
    extra = max(0, len(wrapped) - len(block.lines))
    drawn_lines = wrapped if grow else wrapped[: len(block.lines)]
    baselines = [line.style.chars[0].origin[1] for line in block.lines]
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
        line = block.lines[min(i, len(block.lines) - 1)]
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
                color=line.style.color,
                text_state=state,
                resolution=resolution,
            )
        else:
            end_point = draw_styled_text(
                page,
                text=text,
                origin=origin,
                font_size=font_size,
                color=line.style.color,
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
        note = (
            f"{results[-1].note}; overflow: {extra} more line(s) needed than "
            f"this block has ({len(block.lines)}), not drawn"
        )
        results[-1] = dataclasses.replace(results[-1], requires_approval=True, note=note)
    elif moved_note:
        results[-1] = dataclasses.replace(results[-1], note=results[-1].note + moved_note)
    return results
