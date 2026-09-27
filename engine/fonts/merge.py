"""FNT-08: glyph-borrow merge into a fresh subset font.

When FNT-05 finds a document's embedded subset is missing glyphs a piece of
replacement text needs, and FNT-06 finds the *same* font available in full
(system or bundled) rather than only as this document's partial subset,
Tier 2 (SPEC.md section 5.3) does not patch the existing subset's tables in
place -- that's fragile, and not how real subsetting tools work either.
Instead it cuts a **fresh** subset from the full font, covering the union
of every character the document already shows with that font plus the
characters the edit needs, using fontTools' own subsetter (verified: a
15-character subset of a 65KB TTF produces a valid ~5KB font with exactly
those glyphs). That new font program replaces the page's font resource.
"""

from __future__ import annotations

import io
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont

from engine.fonts.match import FontCandidate, find_by_name


def build_merged_subset(full_font_path: Path, characters: str) -> bytes:
    """A fresh, valid subset font program covering exactly `characters`, cut from
    the full font at `full_font_path`. Always includes a .notdef glyph."""
    tt = TTFont(full_font_path)
    options = subset.Options()
    options.glyph_names = True
    options.notdef_glyph = True
    options.notdef_outline = True
    options.recalc_bounds = True
    subsetter = subset.Subsetter(options=options)
    subsetter.populate(text=characters)
    subsetter.subset(tt)

    buffer = io.BytesIO()
    tt.save(buffer)
    return buffer.getvalue()


def borrow_glyphs_for(
    base_font: str, already_rendered_text: str, needed_text: str, index: list[FontCandidate]
) -> bytes | None:
    """FNT-08: find the full version of `base_font` and cut a merged subset
    covering both what the document already shows and what the edit needs.

    Returns None when no full font by that name is available (the caller
    should fall back to Tier 3, engine.fonts.match.rank_by_metrics).
    """
    candidate = find_by_name(index, base_font)
    if candidate is None:
        return None
    characters = set(already_rendered_text) | set(needed_text)
    return build_merged_subset(candidate.path, "".join(characters))
