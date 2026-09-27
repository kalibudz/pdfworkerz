"""FNT-09: kerning-pair adjustments from a font's own kern table.

Confirmed empirically: PyMuPDF's text-insertion API (``Font.char_lengths``,
``Page.insert_text``) never applies kerning at all -- a rendered "AV"
measures exactly the naive sum of "A" and "V"'s individual advance widths,
whether drawn in one call or character by character (see
tests/engine/test_font_kerning.py). Pairs are read from a font's legacy
``kern`` table (format 0, the common case for the fonts this project
bundles and tests against); a GPOS pair-adjustment lookup, more common in
newer fonts, is not read here -- a documented gap, not a silent one.

Ligature substitution (GSUB, e.g. "fi" becoming one glyph) is a separate,
larger problem: it would mean the drawn *glyph count* no longer matches
the *character count*, which the whole per-character drawing path in
engine.edit assumes. Not attempted here.
"""

from __future__ import annotations

import io

from fontTools.ttLib import TTFont


def build_kern_pairs(font_bytes: bytes) -> dict[tuple[str, str], float]:
    """(char1, char2) -> kerning adjustment, as a fraction of the em square
    (multiply by the font size in points to get the adjustment in points).
    Empty when the font has no legacy ``kern`` table (including a font that
    only has GPOS pair positioning) or can't be parsed.
    """
    try:
        tt = TTFont(io.BytesIO(font_bytes), lazy=True, fontNumber=0)
        if "kern" not in tt:
            return {}
        units_per_em = float(tt["head"].unitsPerEm)
        cmap = tt.getBestCmap()
        if not cmap or not units_per_em:
            return {}

        glyph_name_to_char: dict[str, str] = {}
        for codepoint, glyph_name in cmap.items():
            glyph_name_to_char.setdefault(glyph_name, chr(codepoint))

        pairs: dict[tuple[str, str], float] = {}
        for subtable in tt["kern"].kernTables:
            table = getattr(subtable, "kernTable", None)
            if not table:
                continue
            for (left_name, right_name), value in table.items():
                left_char = glyph_name_to_char.get(left_name)
                right_char = glyph_name_to_char.get(right_name)
                if left_char is not None and right_char is not None:
                    pairs[(left_char, right_char)] = value / units_per_em
    except Exception:
        return {}
    return pairs
