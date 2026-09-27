"""EDT-01/02/03/04/06: style-faithful text editing.

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

import io
import math
from dataclasses import dataclass

import numpy as np
import pikepdf
import pymupdf
from numpy.typing import NDArray

from engine.document import Document
from engine.fonts.classify import classify_font, split_subset_tag
from engine.fonts.fit import fit_to_width
from engine.fonts.match import FontCandidate
from engine.fonts.resolve import FontResolution, resolve_font
from engine.fonts.style import SpanTrace, TextState, advance_for_char, extract_page_spans
from engine.verify import DiffResult, pixel_diff, render_to_array

VERIFY_DPI = 150

_EDIT_FONT_RESOURCE = "PDFWorkerzEdit"
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

    @property
    def looks_right(self) -> bool:
        return self.text_matches and self.diff.changed_fraction > 0.0


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
    document: Document, page_index: int, before: NDArray[np.uint8], expected_text: str
) -> VerificationResult:
    """FNT-12: confirm `expected_text` is now really on the page, and measure how
    much of the page changed. `before` is a render_to_array() result from
    just before the edit."""
    after = render_to_array(document.raw, page_index, dpi=VERIFY_DPI)
    diff = pixel_diff(before, after)
    flat_text = "".join(span.style.text for span in extract_page_spans(document.raw, page_index))
    text_matches = expected_text in flat_text if expected_text else True
    return VerificationResult(text_matches=text_matches, diff=diff)


def _resolve_font_resource(page: pymupdf.Page, resolution: FontResolution) -> str:
    """Register the resolved font on the page (if needed) and return its Tf resource name."""
    if resolution.fontname is not None:
        return resolution.fontname
    if resolution.font_bytes is None:
        raise ValueError("FontResolution has neither fontname nor font_bytes set")
    page.insert_font(fontname=_EDIT_FONT_RESOURCE, fontbuffer=resolution.font_bytes)
    return _EDIT_FONT_RESOURCE


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

    Text with default spacing (Tc=0, Tw=0, Tz=100%) and no rotation is drawn
    with a single call, so it stays one contiguous span for any edit that
    follows (confirmed: per-character calls fragment texttrace's spans one
    character each, which then only a single-character search can find).
    Non-default spacing or rotation still needs per-character control, and
    accepts that fragmentation as a known trade-off.
    """
    if not text:
        return origin

    fontname = _resolve_font_resource(page, resolution)
    font = _load_font(resolution)

    if rotation_degrees == 0.0 and text_state.rise == 0.0 and _is_default_spacing(text_state):
        page.insert_text(
            origin, text, fontsize=font_size, fontname=fontname, color=color, render_mode=text_state.render_mode
        )
        return (origin[0] + font.text_length(text, fontsize=font_size), origin[1])

    widths = font.char_lengths(text, fontsize=font_size)

    angle = math.radians(rotation_degrees)
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    rise_dx, rise_dy = -text_state.rise * sin_a, -text_state.rise * cos_a

    x, y = origin
    for char, base_width in zip(text, widths, strict=True):
        page.insert_text(
            (x + rise_dx, y + rise_dy),
            char,
            fontsize=font_size,
            fontname=fontname,
            color=color,
            render_mode=text_state.render_mode,
        )
        advance = advance_for_char(base_width, char, text_state)
        x += advance * cos_a
        y += advance * sin_a
    return (x, y)


def _find_font_entry(page: pymupdf.Page, basefont: str) -> tuple[int, str] | None:
    """The (xref, Tf resource name) of the page's font whose BaseFont matches, if any.

    Compared with the subset tag stripped from both sides: PyMuPDF's texttrace
    reports a subset font's name without its "ABCDEF+" prefix (confirmed
    empirically), while ``Page.get_fonts()`` reports the BaseFont as-is, prefix
    included. A font with no real BaseFont at all (Type3 commonly has none,
    FNT-15) gets a synthesized "<FontType> (<ref>)" name from texttrace
    instead; matched here by font type against get_fonts's empty entry.
    """
    _, target = split_subset_tag(basefont)
    for entry in page.get_fonts(full=True):
        xref, _ext, font_type, entry_basefont, resource_name, *_rest = entry
        _, entry_plain = split_subset_tag(entry_basefont)
        if entry_plain == target:
            return xref, resource_name
        if not entry_basefont and target.startswith(f"{font_type} ("):
            return xref, resource_name
    return None


def resolve_font_for_span(
    document: Document, page_index: int, span: SpanTrace, needed_text: str, *, font_index: list[FontCandidate]
) -> FontResolution:
    """The FontResolution (engine.fonts.resolve) for drawing `needed_text` in the
    same style as `span`. Shared by replace, restyle and insert-near-reference.
    """
    page = document.raw[page_index]
    style = span.style

    found = _find_font_entry(page, style.font)
    if found is None:
        raise ValueError(f"could not find the page's own font resource for {style.font!r}")
    xref, resource_name = found

    with pikepdf.open(io.BytesIO(document.to_bytes())) as pikepdf_doc:
        classification = classify_font(pikepdf_doc, page_index, resource_name)

    original_bytes = document.raw.extract_font(xref)[3] or None
    already_used = _collect_font_usage(extract_page_spans(document.raw, page_index), style.font)

    return resolve_font(
        classification,
        original_font_bytes=original_bytes,
        already_rendered_text=already_used,
        needed_text=needed_text,
        font_index=font_index,
    )


def replace_span_text(
    document: Document,
    page_index: int,
    span: SpanTrace,
    new_text: str,
    *,
    font_index: list[FontCandidate],
    override_size: float | None = None,
    override_color: tuple[float, float, float] | None = None,
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
    text_state = span.text_state or TextState()

    resolution = resolve_font_for_span(document, page_index, span, new_text, font_index=font_index)
    before = render_to_array(document.raw, page_index, dpi=VERIFY_DPI) if verify else None

    font_size = override_size if override_size is not None else (text_state.font_size or style.size)
    color = override_color if override_color is not None else style.color

    fit_result = None
    if fit and new_text and override_size is None:
        target_width = style.bbox[2] - style.bbox[0]
        fit_result = fit_to_width(_load_font(resolution), new_text, font_size, text_state, target_width)
        text_state = fit_result.text_state

    page.add_redact_annot(pymupdf.Rect(style.bbox))
    page.apply_redactions(**_REDACT_KWARGS)

    end_point = draw_styled_text(
        page,
        text=new_text,
        origin=style.chars[0].origin,
        font_size=font_size,
        color=color,
        text_state=text_state,
        rotation_degrees=style.rotation_degrees,
        resolution=resolution,
    )

    verification = _verify_edit(document, page_index, before, new_text) if before is not None else None
    note = resolution.note
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


def insert_text_near(
    document: Document,
    page_index: int,
    reference_span: SpanTrace,
    text: str,
    origin: tuple[float, float],
    *,
    font_index: list[FontCandidate],
    verify: bool = True,
) -> EditResult:
    """EDT-03: draw new `text` at `origin`, matching `reference_span`'s style.

    Nothing is redacted -- this adds text near an existing span without
    touching it, for example a caption or a value beside a label.
    """
    page = document.raw[page_index]
    style = reference_span.style
    text_state = reference_span.text_state or TextState()

    resolution = resolve_font_for_span(document, page_index, reference_span, text, font_index=font_index)
    before = render_to_array(document.raw, page_index, dpi=VERIFY_DPI) if verify else None

    end_point = draw_styled_text(
        page,
        text=text,
        origin=origin,
        font_size=text_state.font_size or style.size,
        color=style.color,
        text_state=text_state,
        rotation_degrees=style.rotation_degrees,
        resolution=resolution,
    )

    verification = _verify_edit(document, page_index, before, text) if before is not None else None
    return EditResult(
        tier=resolution.tier,
        confidence=resolution.confidence,
        requires_approval=resolution.requires_approval,
        note=resolution.note,
        end_point=end_point,
        verification=verification,
    )
