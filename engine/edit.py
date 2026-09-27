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
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass

import pikepdf
import pymupdf

from engine.document import Document
from engine.fonts.classify import classify_font, split_subset_tag
from engine.fonts.match import FontCandidate
from engine.fonts.resolve import FontResolution, resolve_font
from engine.fonts.style import SpanTrace, TextState, extract_page_spans

_EDIT_FONT_RESOURCE = "PDFWorkerzEdit"
# Verified at runtime (pymupdf 1.28.2); missing from pymupdf's stub like PDF_ENCRYPT_KEEP
# (see engine/document.py's note on the same class of gap).
_REDACT_KWARGS: dict[str, int] = {
    "images": pymupdf.PDF_REDACT_IMAGE_NONE,  # type: ignore[attr-defined]
    "graphics": pymupdf.PDF_REDACT_LINE_ART_NONE,  # type: ignore[attr-defined]
    "text": pymupdf.PDF_REDACT_TEXT_REMOVE,  # type: ignore[attr-defined]
}


@dataclass(frozen=True)
class EditResult:
    """What a text edit did, and how much confidence backs the font it used."""

    tier: str
    confidence: float
    requires_approval: bool
    note: str
    end_point: tuple[float, float]
    """Where the next character after the drawn text would start (baseline)."""


def _advance(base_width: float, char: str, text_state: TextState) -> float:
    """ISO 32000-1 9.4.3: tx = (w0 + Tc + Tw) * Th, Tw only for the space character."""
    word_spacing = text_state.word_spacing if char == " " else 0.0
    return (base_width + text_state.char_spacing + word_spacing) * (text_state.horizontal_scale / 100.0)


def _collect_font_usage(spans: list[SpanTrace], font_name: str) -> str:
    """Every character already shown anywhere on the page with the given font."""
    return "".join(trace.style.text for trace in spans if trace.style.font == font_name)


def _resolve_font_resource(page: pymupdf.Page, resolution: FontResolution) -> str:
    """Register the resolved font on the page (if needed) and return its Tf resource name."""
    if resolution.fontname is not None:
        return resolution.fontname
    if resolution.font_bytes is None:
        raise ValueError("FontResolution has neither fontname nor font_bytes set")
    page.insert_font(fontname=_EDIT_FONT_RESOURCE, fontbuffer=resolution.font_bytes)
    return _EDIT_FONT_RESOURCE


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
    font = (
        pymupdf.Font(resolution.fontname)
        if resolution.fontname is not None
        else pymupdf.Font(fontbuffer=resolution.font_bytes)
    )

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
        advance = _advance(base_width, char, text_state)
        x += advance * cos_a
        y += advance * sin_a
    return (x, y)


def _find_font_entry(page: pymupdf.Page, basefont: str) -> tuple[int, str] | None:
    """The (xref, Tf resource name) of the page's font whose BaseFont matches, if any.

    Compared with the subset tag stripped from both sides: PyMuPDF's texttrace
    reports a subset font's name without its "ABCDEF+" prefix (confirmed
    empirically), while ``Page.get_fonts()`` reports the BaseFont as-is, prefix
    included.
    """
    _, target = split_subset_tag(basefont)
    for entry in page.get_fonts(full=True):
        xref, _ext, _font_type, entry_basefont, resource_name, *_rest = entry
        _, entry_plain = split_subset_tag(entry_basefont)
        if entry_plain == target:
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
) -> EditResult:
    """EDT-01/EDT-02/EDT-04/EDT-06: replace one span's text with `new_text`
    (empty text deletes it; the same text with an override restyles it),
    matching its original style as closely as the resolved font allows.
    """
    page = document.raw[page_index]
    style = span.style
    text_state = span.text_state or TextState()

    resolution = resolve_font_for_span(document, page_index, span, new_text, font_index=font_index)

    page.add_redact_annot(pymupdf.Rect(style.bbox))
    page.apply_redactions(**_REDACT_KWARGS)

    font_size = override_size if override_size is not None else (text_state.font_size or style.size)
    color = override_color if override_color is not None else style.color
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

    return EditResult(
        tier=resolution.tier,
        confidence=resolution.confidence,
        requires_approval=resolution.requires_approval,
        note=resolution.note,
        end_point=end_point,
    )


def insert_text_near(
    document: Document,
    page_index: int,
    reference_span: SpanTrace,
    text: str,
    origin: tuple[float, float],
    *,
    font_index: list[FontCandidate],
) -> EditResult:
    """EDT-03: draw new `text` at `origin`, matching `reference_span`'s style.

    Nothing is redacted -- this adds text near an existing span without
    touching it, for example a caption or a value beside a label.
    """
    page = document.raw[page_index]
    style = reference_span.style
    text_state = reference_span.text_state or TextState()

    resolution = resolve_font_for_span(document, page_index, reference_span, text, font_index=font_index)

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

    return EditResult(
        tier=resolution.tier,
        confidence=resolution.confidence,
        requires_approval=resolution.requires_approval,
        note=resolution.note,
        end_point=end_point,
    )
