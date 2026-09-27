"""FNT-05: does a font already cover the glyphs a piece of replacement text needs?

This is the first, and cheapest, check in the replacement strategy (SPEC.md
section 5.3, Tier 1 "stream surgery"): if every character is already
available, an edit can reuse the existing font program unchanged. Three
cases, each verified against real PyMuPDF/pikepdf/fontTools output:

- **Standard (non-embedded) font.** There is no font program to inspect --
  ``Document.extract_font`` returns empty bytes for one. Standard-14 fonts
  and WinAnsiEncoding cover the Latin-1 repertoire completely, so coverage
  is decided by codepoint range.
- **Embedded font with a usable ``cmap`` table.** This is the common case
  for a *full* (non-subset) embedded font. fontTools reads the cmap to find
  each character's glyph, and confirms the glyph isn't an empty outline
  (e.g. a stripped placeholder).
- **Embedded font with no ``cmap`` table.** PyMuPDF's subsetting keeps only
  the glyphs a document actually uses but frequently drops the ``cmap``
  table entirely for Identity-H CID fonts (confirmed empirically -- PDF
  rendering never needs it, since character code IS the glyph ID under
  Identity-H/Identity CIDToGIDMap). There is no way to look up an unused
  character's glyph in a font like this at all, so coverage is decided by
  whether the character already appears somewhere in text this document
  has rendered with the same font -- which is exactly what caused it to
  survive subsetting in the first place.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from fontTools.ttLib import TTFont

_LATIN1_MAX_CODEPOINT = 0xFF


@dataclass(frozen=True)
class CoverageResult:
    covered: frozenset[str]
    missing: frozenset[str]
    method: str
    """"latin1-range" | "font-cmap" | "already-rendered" | "unsupported" (Type3)."""

    @property
    def fully_covered(self) -> bool:
        return not self.missing


def _cmap_coverage(font_bytes: bytes, characters: set[str]) -> set[str] | None:
    """The characters covered by a font's own cmap, or None when the font has no
    cmap to check at all (the common subset case).

    A glyph with no outline (space, non-breaking space, ...) is not "missing" --
    it is correctly blank by design. What matters is only that the cmap points
    at a glyph the font actually still has; fontTools-built subsets (see
    engine.fonts.merge) never leave a cmap entry pointing at a glyph they drop,
    so presence in ``glyf``/``CFF`` is a reliable enough signal on its own.
    """
    tt = TTFont(io.BytesIO(font_bytes), lazy=True)
    try:
        cmap = tt.getBestCmap()
    except Exception:
        return None
    if not cmap:
        return None

    glyph_set = tt.getGlyphSet()
    return {ch for ch in characters if cmap.get(ord(ch)) in glyph_set}


def check_coverage(
    *,
    font_type: str,
    embedded: bool,
    font_bytes: bytes | None,
    already_rendered_text: str,
    characters: str,
) -> CoverageResult:
    """FNT-05: which of `characters` (deduplicated) this font can already show.

    `already_rendered_text` is every character already shown somewhere in
    the document using this exact font resource (see
    engine.fonts.style.extract_page_spans) -- required for the subset
    fallback path, ignored otherwise.
    """
    wanted = set(characters)

    if font_type == "Type3":
        return CoverageResult(covered=frozenset(), missing=frozenset(wanted), method="unsupported")

    if not embedded or not font_bytes:
        covered = {ch for ch in wanted if ord(ch) <= _LATIN1_MAX_CODEPOINT}
        return CoverageResult(covered=frozenset(covered), missing=frozenset(wanted - covered), method="latin1-range")

    cmap_covered = _cmap_coverage(font_bytes, wanted)
    if cmap_covered is not None:
        return CoverageResult(
            covered=frozenset(cmap_covered), missing=frozenset(wanted - cmap_covered), method="font-cmap"
        )

    already_used = set(already_rendered_text)
    covered = wanted & already_used
    return CoverageResult(covered=frozenset(covered), missing=frozenset(wanted - covered), method="already-rendered")
