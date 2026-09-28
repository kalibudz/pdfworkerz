"""FNT-13: recover readable text when a span's font lacks (or has a broken)
ToUnicode CMap.

Confirmed empirically: stripping ``/ToUnicode`` from an embedded Identity-H
composite font makes MuPDF's own text extraction return the Unicode
replacement character (U+FFFD) for every glyph, even though the *same*
embedded font still carries its own, perfectly usable ``cmap`` table --
MuPDF does not fall back to a CID-keyed font's own cmap for this. It can be
recovered: for an Identity-H/V font with the (very common) Identity
CIDToGIDMap, the raw content-stream code *is* the glyph ID directly, so
reversing the font's own cmap (Unicode -> glyph name -> glyph ID) into
GID -> Unicode and looking up each glyph's raw code there reconstructs the
original text exactly (verified against a real embedded font: "Hello",
stripped of its ToUnicode CMap, round-trips correctly through this path).
"""

from __future__ import annotations

import io

import pikepdf
import pymupdf
from fontTools.ttLib import TTFont

from engine.fonts.match import normalize_font_name
from engine.fonts.style import CharBox, SpanTrace, walk_raw_glyph_codes
from engine.pdfbytes import plain_bytes

REPLACEMENT_CHAR = "�"


def build_gid_to_unicode(font_bytes: bytes) -> dict[int, str] | None:
    """Reverse a font's own cmap into glyph ID -> single Unicode character.

    Meaningful only for a font used as Identity-H/V with an Identity
    CIDToGIDMap (PyMuPDF's and most modern tools' default for embedded
    composite fonts), where the content stream's raw code equals the GID.
    Returns None if the font can't be parsed or has no usable cmap.
    """
    try:
        tt = TTFont(io.BytesIO(font_bytes), lazy=True, fontNumber=0)
        cmap = tt.getBestCmap()
        if not cmap:
            return None
        glyph_order = tt.getGlyphOrder()
    except Exception:
        return None

    name_to_gid = {name: index for index, name in enumerate(glyph_order)}
    result: dict[int, str] = {}
    for codepoint, glyph_name in cmap.items():
        gid = name_to_gid.get(glyph_name)
        if gid is not None and gid not in result:
            result[gid] = chr(codepoint)
    return result or None


def _needs_recovery(text: str) -> bool:
    return REPLACEMENT_CHAR in text


def _rewrap_chars(chars: list[CharBox], new_text: str) -> list[CharBox]:
    """The same positions and boxes, with each character's identity updated."""
    return [box.model_copy(update={"char": ch}) for box, ch in zip(chars, new_text, strict=True)]


def recover_broken_spans(doc: pymupdf.Document, page_index: int, spans: list[SpanTrace]) -> list[SpanTrace]:
    """Re-resolve the text of any span texttrace could not read (its text
    contains U+FFFD). Spans that don't need it, and spans that can't be
    recovered (no font bytes, no usable cmap, or the raw-code count doesn't
    line up with texttrace's character count), are returned unchanged --
    this never invents a character it isn't confident about.
    """
    if not any(_needs_recovery(trace.style.text) for trace in spans):
        return spans

    page = doc[page_index]
    # Keyed by normalized name, not the raw BaseFont: texttrace reports a full
    # (non-subset) embedded font's name differently from Page.get_fonts() for
    # the same font (its PostScript name vs. its full name) -- see
    # engine.edit._find_font_entry's docstring for the confirmed example.
    font_xrefs: dict[str, int] = {}
    for xref, _ext, _font_type, basefont, *_rest in page.get_fonts(full=True):
        font_xrefs.setdefault(normalize_font_name(basefont), xref)

    gid_maps: dict[str, dict[int, str] | None] = {}

    def gid_map_for(basefont: str) -> dict[int, str] | None:
        key = normalize_font_name(basefont)
        if key not in gid_maps:
            xref = font_xrefs.get(key)
            font_bytes = doc.extract_font(xref)[3] if xref is not None else b""
            gid_maps[key] = build_gid_to_unicode(font_bytes) if font_bytes else None
        return gid_maps[key]

    with pikepdf.open(io.BytesIO(plain_bytes(doc))) as pikepdf_doc:
        raw_codes = walk_raw_glyph_codes(pikepdf_doc.pages[page_index])

    total_chars = sum(len(trace.style.chars) for trace in spans)
    if len(raw_codes) != total_chars:
        return spans

    recovered: list[SpanTrace] = []
    code_index = 0
    for trace in spans:
        count = len(trace.style.chars)
        if _needs_recovery(trace.style.text):
            gid_map = gid_map_for(trace.style.font)
            if gid_map is not None:
                codes = raw_codes[code_index : code_index + count]
                chars = [gid_map.get(code) for code in codes]
                if all(char is not None for char in chars):
                    new_text = "".join(chars)  # type: ignore[arg-type]
                    new_style = trace.style.model_copy(
                        update={"text": new_text, "chars": _rewrap_chars(trace.style.chars, new_text)}
                    )
                    trace = SpanTrace(style=new_style, text_state=trace.text_state)
        recovered.append(trace)
        code_index += count
    return recovered
