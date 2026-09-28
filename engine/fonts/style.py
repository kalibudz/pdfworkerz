"""FNT-01 (per-span style extraction) and FNT-02 (per-character text-state trace).

Two complementary sources are combined:

- **texttrace** (``Page.get_texttrace()``) gives the ground truth of what
  was actually rendered: font, size (as rendered -- see below), color,
  opacity, per-character origin/bbox and direction. This is exact and
  needs no interpretation.
- **the content stream** (walked with pikepdf) gives the text-state
  operators texttrace does not expose at all: character spacing (Tc),
  word spacing (Tw), horizontal scaling (Tz), leading (TL), rise (Ts) and
  render mode (Tr).

These are correlated at the **character** level, not the span level:
adjacent text-showing operators with no rendering-relevant change between
them (for example two `Tj` calls that only differ in `Tc`) are routinely
folded into a single texttrace span, so operator count and span count
often disagree even on simple pages. Instead, each text-showing operator
is expanded into one text-state snapshot per glyph it shows (using the
current font's byte width -- 1 for a simple font, 2 for the common
Identity-H/V composite case), producing a flat, per-glyph list that is
zipped against texttrace's flattened, per-glyph character list in the
same document order. A render mode that paints both a fill and a stroke
pass (Tr 2, 4, 5 or 6) makes texttrace emit one entry per pass with
identical geometry; those duplicates are collapsed before correlation.
Each span is then attributed the text state of its first glyph. If the
flattened counts still don't match -- a content-stream shape this
correlation doesn't cover -- every span's ``text_state`` is left ``None``
rather than guessing.
"""

from __future__ import annotations

import io
import math
from collections.abc import Iterator
from typing import Any

import pikepdf
import pymupdf
from pydantic import BaseModel, ConfigDict

from engine.pdfbytes import plain_bytes

RENDER_MODE_NAMES = {
    0: "fill",
    1: "stroke",
    2: "fill_stroke",
    3: "invisible",
    4: "fill_clip",
    5: "stroke_clip",
    6: "fill_stroke_clip",
    7: "clip",
}

_SHOWING_OPS = frozenset({"Tj", "TJ", "'", '"'})


class CharBox(BaseModel):
    """One glyph's position, as rendered."""

    model_config = ConfigDict(frozen=True)

    char: str
    origin: tuple[float, float]
    bbox: tuple[float, float, float, float]


class SpanStyle(BaseModel):
    """FNT-01: one run of same-styled text, exactly as MuPDF rendered it."""

    model_config = ConfigDict(frozen=True)

    page_index: int
    span_index: int
    text: str
    font: str
    """The font's PostScript name as MuPDF reports it (its BaseFont, subset tag included)."""
    size: float
    """The rendered glyph size. When Tz (horizontal scaling) is not 100%, this already
    includes that scaling -- see TextState.horizontal_scale for the separate Tz value
    and TextState.font_size for the plain Tf-declared size."""
    color: tuple[float, float, float]
    opacity: float
    bbox: tuple[float, float, float, float]
    rotation_degrees: float
    ascender: float
    descender: float
    chars: list[CharBox]


class TextState(BaseModel):
    """FNT-02: the content-stream text state active when a span's text was shown."""

    model_config = ConfigDict(frozen=True)

    char_spacing: float = 0.0
    """Tc, in unscaled text space units."""
    word_spacing: float = 0.0
    """Tw, in unscaled text space units (applies to single-byte code 32 only)."""
    horizontal_scale: float = 100.0
    """Tz, as a percentage; 100 is unscaled."""
    leading: float = 0.0
    """TL, the line-to-line spacing set for T* / TD."""
    rise: float = 0.0
    """Ts, vertical displacement from the baseline in unscaled text space units."""
    render_mode: int = 0
    """Tr: 0=fill, 1=stroke, 2=fill+stroke, 3=invisible, 4-7 add clipping."""
    font_resource: str | None = None
    """The Tf operand's resource name (e.g. "F1"), for looking up the exact font dict."""
    font_size: float | None = None
    """The plain Tf-declared point size, independent of horizontal_scale."""

    @property
    def render_mode_name(self) -> str:
        return RENDER_MODE_NAMES.get(self.render_mode, "unknown")


def advance_for_char(base_width: float, char: str, text_state: TextState) -> float:
    """ISO 32000-1 9.4.3: tx = (w0 + Tc + Tw) * Th, Tw only for the space character.

    `base_width` is the glyph's plain advance at the target font size (for
    example from ``pymupdf.Font.char_lengths``), with no Tc/Tw/Tz applied.
    Shared by engine.edit (drawing) and engine.fonts.fit (FNT-10).
    """
    word_spacing = text_state.word_spacing if char == " " else 0.0
    return (base_width + text_state.char_spacing + word_spacing) * (text_state.horizontal_scale / 100.0)


class SpanTrace(BaseModel):
    """FNT-01 + FNT-02 combined for one span."""

    model_config = ConfigDict(frozen=True)

    style: SpanStyle
    text_state: TextState | None
    """None when the content stream's text-showing operators didn't correlate
    1:1 with texttrace's spans; ``style`` is always populated regardless."""


def dedupe_texttrace(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse fill+stroke pairs (identical geometry, different paint `type`) to one entry."""
    seen: set[tuple[Any, ...]] = set()
    deduped = []
    for span in spans:
        key = (tuple(span["bbox"]), tuple(c[2] for c in span["chars"]))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(span)
    return deduped


def _rotation_degrees(direction: tuple[float, float]) -> float:
    return math.degrees(math.atan2(direction[1], direction[0]))


def _span_style_from_texttrace(page_index: int, span_index: int, span: dict[str, Any]) -> SpanStyle:
    chars = [CharBox(char=chr(c[0]), origin=tuple(c[2]), bbox=tuple(c[3])) for c in span["chars"]]
    return SpanStyle(
        page_index=page_index,
        span_index=span_index,
        text="".join(box.char for box in chars),
        font=span["font"],
        size=span["size"],
        color=tuple(span["color"]),
        opacity=span["opacity"],
        bbox=tuple(span["bbox"]),
        rotation_degrees=_rotation_degrees(tuple(span["dir"])),
        ascender=span["ascender"],
        descender=span["descender"],
        chars=chars,
    )


def _page_resources(pdf_page: pikepdf.Page) -> pikepdf.Object:
    try:
        return pdf_page.Resources
    except AttributeError:
        return pikepdf.Dictionary()


def _walk_content(
    content: pikepdf.Object | pikepdf.Page, resources: pikepdf.Object, seen: frozenset[tuple[int, int]]
) -> Iterator[tuple[str, Any, pikepdf.Object]]:
    """Yield (operator, operands, active /Resources) for a content stream,
    following ``Do`` into Form XObjects in place -- where texttrace reports
    their glyphs too -- wrapped in the implicit q/Q the PDF spec gives every
    Form XObject (ISO 32000-1 8.10.1), so text state set inside a form never
    leaks back out. A form without its own /Resources uses its parent's.
    ``seen`` stops a (malformed) form that invokes itself from recursing forever.
    """
    for instr in pikepdf.parse_content_stream(content):
        op = str(instr.operator)
        if op == "Do":
            xobject = resources.get("/XObject", pikepdf.Dictionary()).get(str(instr.operands[0]))
            if xobject is not None and xobject.get("/Subtype") == "/Form" and xobject.objgen not in seen:
                yield "q", [], resources
                yield from _walk_content(xobject, xobject.get("/Resources", resources), seen | {xobject.objgen})
                yield "Q", [], resources
            continue
        yield op, instr.operands, resources


def _bytes_per_glyph(resources: pikepdf.Object, resource_name: str | None) -> int:
    """1 for a simple font, 2 for the common Identity-H/V composite case.

    Composite fonts with an embedded (non-Identity) CMap can use other byte
    widths; those aren't decoded here, so a font of that kind can produce an
    inaccurate glyph count and fall back to the graceful "no correlation"
    path below, rather than a silently wrong count.
    """
    if resource_name is None:
        return 1
    font_dict = resources.get("/Font", pikepdf.Dictionary()).get(f"/{resource_name}")
    if font_dict is None:
        return 1
    if str(font_dict.get("/Subtype")) != "/Type0":
        return 1
    # Identity-H/V (2 bytes per CID) is what PyMuPDF and most modern tools emit for
    # embedded composite fonts. A font using some other, embedded CMap could use a
    # different width; that's not decoded here, so it falls back to this same guess.
    return 2


def _string_glyph_count(value: pikepdf.Object, bytes_per_glyph: int) -> int:
    length = len(bytes(value))
    return length // bytes_per_glyph if bytes_per_glyph else length


def _walk_glyph_states(pdf_page: pikepdf.Page) -> list[TextState]:
    """Replay a page's content stream(s), including any Form XObjects it
    draws; return one TextState per glyph shown, in document order. `q`/`Q`
    save and restore the whole text state, matching the PDF graphics-state
    model (ISO 32000-1 8.4)."""
    stack: list[TextState] = []
    current = TextState()
    per_glyph: list[TextState] = []

    for op, operands, resources in _walk_content(pdf_page, _page_resources(pdf_page), frozenset()):
        if op == "q":
            stack.append(current)
        elif op == "Q":
            if stack:
                current = stack.pop()
        elif op == "Tc":
            current = current.model_copy(update={"char_spacing": float(operands[0])})
        elif op == "Tw":
            current = current.model_copy(update={"word_spacing": float(operands[0])})
        elif op == "Tz":
            current = current.model_copy(update={"horizontal_scale": float(operands[0])})
        elif op == "TL":
            current = current.model_copy(update={"leading": float(operands[0])})
        elif op == "Ts":
            current = current.model_copy(update={"rise": float(operands[0])})
        elif op == "Tr":
            current = current.model_copy(update={"render_mode": int(operands[0])})
        elif op == "Tf":
            current = current.model_copy(
                update={"font_resource": str(operands[0]).lstrip("/"), "font_size": float(operands[1])}
            )
        elif op == "Tj" or op == "'":
            width = _bytes_per_glyph(resources, current.font_resource)
            per_glyph.extend([current] * _string_glyph_count(operands[0], width))
        elif op == '"':
            current = current.model_copy(
                update={"word_spacing": float(operands[0]), "char_spacing": float(operands[1])}
            )
            width = _bytes_per_glyph(resources, current.font_resource)
            per_glyph.extend([current] * _string_glyph_count(operands[2], width))
        elif op == "TJ":
            width = _bytes_per_glyph(resources, current.font_resource)
            for item in operands[0]:
                if isinstance(item, pikepdf.String):
                    per_glyph.extend([current] * _string_glyph_count(item, width))
    return per_glyph


def _split_codes(value: pikepdf.Object, bytes_per_glyph: int) -> list[int]:
    raw = bytes(value)
    step = bytes_per_glyph or 1
    return [int.from_bytes(raw[i : i + step], "big") for i in range(0, len(raw), step)]


def walk_raw_glyph_codes(pdf_page: pikepdf.Page) -> list[int]:
    """Every glyph's raw character code (its Tj/TJ byte value, decoded at the
    active font's byte width), in the same document order as texttrace's
    flattened character list. Used only for ToUnicode recovery (FNT-13,
    engine.fonts.tounicode) -- a code is meaningful chiefly for an
    Identity-H/V composite font, where it equals the glyph's GID directly.
    """
    codes: list[int] = []
    font_stack: list[str | None] = []
    current_font_resource: str | None = None

    for op, operands, resources in _walk_content(pdf_page, _page_resources(pdf_page), frozenset()):
        width = _bytes_per_glyph(resources, current_font_resource)
        if op == "q":
            font_stack.append(current_font_resource)
        elif op == "Q":
            if font_stack:
                current_font_resource = font_stack.pop()
        elif op == "Tf":
            current_font_resource = str(operands[0]).lstrip("/")
        elif op == "Tj" or op == "'":
            codes.extend(_split_codes(operands[0], width))
        elif op == '"':
            codes.extend(_split_codes(operands[2], width))
        elif op == "TJ":
            for item in operands[0]:
                if isinstance(item, pikepdf.String):
                    codes.extend(_split_codes(item, width))
    return codes


def extract_page_spans(doc: pymupdf.Document, page_index: int) -> list[SpanTrace]:
    """FNT-01 + FNT-02: every text span on a page, with its style and (when the
    content stream correlates cleanly) its text state, as of its first glyph."""
    raw_spans = dedupe_texttrace(doc[page_index].get_texttrace())
    styles = [_span_style_from_texttrace(page_index, i, span) for i, span in enumerate(raw_spans)]

    with pikepdf.open(io.BytesIO(plain_bytes(doc))) as pikepdf_doc:
        glyph_states = _walk_glyph_states(pikepdf_doc.pages[page_index])

    total_chars = sum(len(span.chars) for span in styles)
    if len(glyph_states) != total_chars:
        return [SpanTrace(style=style, text_state=None) for style in styles]

    traces = []
    glyph_index = 0
    for style in styles:
        traces.append(SpanTrace(style=style, text_state=glyph_states[glyph_index]))
        glyph_index += len(style.chars)
    return traces
