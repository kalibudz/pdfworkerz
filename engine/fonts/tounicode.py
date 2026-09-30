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
import re

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


# -- The other direction: a character map for a subset that has none --

_HEX = re.compile(r"<([0-9A-Fa-f]+)>")


def _utf16(hex_text: str) -> str | None:
    try:
        return bytes.fromhex(hex_text).decode("utf-16-be")
    except (ValueError, UnicodeDecodeError):
        return None


def _parse_tounicode(data: str) -> dict[int, str]:
    """A ToUnicode CMap's bfchar and bfrange entries as code -> text."""
    mapping: dict[int, str] = {}
    for block in re.findall(r"beginbfchar(.*?)endbfchar", data, re.S):
        pairs = _HEX.findall(block)
        for code, text in zip(pairs[0::2], pairs[1::2], strict=False):
            value = _utf16(text)
            if value:
                mapping[int(code, 16)] = value
    for block in re.findall(r"beginbfrange(.*?)endbfrange", data, re.S):
        for line in block.strip().splitlines():
            line = line.strip()
            array = re.match(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*\[(.*)\]", line)
            if array:
                start, end = int(array.group(1), 16), int(array.group(2), 16)
                for offset, text in enumerate(_HEX.findall(array.group(3))):
                    value = _utf16(text)
                    if value and start + offset <= end:
                        mapping[start + offset] = value
                continue
            parts = _HEX.findall(line)
            if len(parts) == 3:
                start, end = int(parts[0], 16), int(parts[1], 16)
                base = _utf16(parts[2])
                if base and len(base) == 1 and end - start < 0x10000:
                    for offset in range(end - start + 1):
                        mapping[start + offset] = chr(ord(base) + offset)
    return mapping


def unicode_to_glyph_ids(doc: pymupdf.Document, font_xref: int) -> dict[int, int] | None:
    """For a composite (Type0) font drawn with Identity-H/V: character -> glyph ID,
    recovered from the font's ToUnicode CMap, or None when that can't be done.

    Subsets embedded without a ``cmap`` table (PyMuPDF's own, and many generators')
    can't draw new text: nothing says which glyph is which character. With Identity
    encoding the content-stream code is the CID, and with an Identity (or absent)
    CIDToGIDMap the CID is the glyph ID -- so ToUnicode, read backwards, is exactly
    the missing map, for every character the document already shows in that font."""
    if doc.xref_get_key(font_xref, "Subtype") != ("name", "/Type0"):
        return None
    if doc.xref_get_key(font_xref, "Encoding")[1] not in ("/Identity-H", "/Identity-V"):
        return None
    descendants = doc.xref_get_key(font_xref, "DescendantFonts")
    found = re.search(r"(\d+) 0 R", descendants[1]) if descendants[0] == "array" else None
    if not found:
        return None
    cid_to_gid = doc.xref_get_key(int(found.group(1)), "CIDToGIDMap")
    if cid_to_gid[0] not in ("null", "name") or (cid_to_gid[0] == "name" and cid_to_gid[1] != "/Identity"):
        return None  # an explicit CIDToGIDMap stream: not handled
    tounicode = doc.xref_get_key(font_xref, "ToUnicode")
    if tounicode[0] != "xref":
        return None
    data = doc.xref_stream(int(tounicode[1].split()[0]))
    if not data:
        return None
    result: dict[int, int] = {}
    for code, text in _parse_tounicode(data.decode("latin-1")).items():
        if len(text) == 1:
            result.setdefault(ord(text), code)
    return result or None


def with_cmap(program: bytes, char_to_glyph_id: dict[int, int]) -> bytes | None:
    """`program` with a ``cmap`` table built from `char_to_glyph_id`, or None if
    the program can't take one. Glyph IDs outside the font are left out."""
    from fontTools.ttLib import newTable
    from fontTools.ttLib.tables._c_m_a_p import cmap_format_4, cmap_format_12

    try:
        tt = TTFont(io.BytesIO(program))
        order = tt.getGlyphOrder()
    except Exception:
        return None
    mapping = {char: order[gid] for char, gid in char_to_glyph_id.items() if 0 <= gid < len(order)}
    if not mapping:
        return None
    table = newTable("cmap")
    table.tableVersion = 0
    table.tables = []
    bmp = cmap_format_4(4)
    bmp.platformID, bmp.platEncID, bmp.language = 3, 1, 0
    bmp.cmap = {char: name for char, name in mapping.items() if char <= 0xFFFF}
    table.tables.append(bmp)
    if any(char > 0xFFFF for char in mapping):
        full = cmap_format_12(12)
        full.platformID, full.platEncID, full.language = 3, 10, 0
        full.format, full.reserved, full.length, full.nGroups = 12, 0, 0, 0
        full.cmap = dict(mapping)
        table.tables.append(full)
    tt["cmap"] = table
    buffer = io.BytesIO()
    try:
        tt.save(buffer)
    except Exception:
        return None
    return buffer.getvalue()


def has_cmap(program: bytes) -> bool:
    try:
        tt = TTFont(io.BytesIO(program), lazy=True, fontNumber=0)
        return "cmap" in tt and bool(tt.getBestCmap())
    except Exception:
        return False
