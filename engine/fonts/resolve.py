"""Font resolution for text edits: which font program to draw replacement text
with, and how sure PDFWorkerz is that it's a true match (SPEC.md section 5.3).

This ties FNT-05 through FNT-08 together into the actual decision an edit
needs. One deliberate, verified departure from the literal tier
description in SPEC.md: PyMuPDF's text-insertion API cannot draw new text
through a font program that has no ``cmap`` table, which is exactly what a
document's own *subset* embedded font usually has (confirmed empirically:
inserting through the raw extracted subset bytes drew glyphs, but the
result could not be read back as the text it was supposed to be, because
PyMuPDF had no way to know which glyph index the requested characters
should use). So rather than literal in-place stream surgery, "exact" match
here always goes through FNT-08's merged subset -- built from the same,
full font -- whenever that font can be found. The result is still the
*same* font (identical outlines, weight and metrics); only the embedding
mechanism differs from the literal description, and that is why the tier
is still reported as "exact", not "approximate".

FNT-15: a Type3 font's "glyphs" are content-stream drawing procedures, not
a font program PyMuPDF can load through ``Font()``/``insert_text()`` --
there is no file or buffer to hand it. Reusing the font's own procedures
for a character they already define (SPEC.md section 5.5's first
preference) would need a different drawing path entirely, emitting `Tf`/
`Tj` against the document's own Type3 resource directly; that is not
implemented, so a Type3 font is always routed to the fallback tier here,
explicitly and with its own note, rather than silently (and wrongly)
falling into the standard-font path that a font with no embedded program
would otherwise take.
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.fonts.classify import FontClassification, fingerprint_style, infer_style_from_name
from engine.fonts.match import FontCandidate, extract_metrics, find_by_name, rank_by_metrics, same_family
from engine.fonts.merge import build_merged_subset

TIER_EXACT = "exact"
TIER_APPROXIMATE = "approximate"
TIER_FALLBACK = "fallback"

_FALLBACK_CONFIDENCE = 0.2

# (bold, italic) -> PyMuPDF standard-14 short name, per family class.
_SANS_FALLBACK = {(False, False): "helv", (True, False): "hebo", (False, True): "heit", (True, True): "hebi"}
_SERIF_FALLBACK = {(False, False): "tiro", (True, False): "tibo", (False, True): "tiit", (True, True): "tibi"}
_MONO_FALLBACK = {(False, False): "cour", (True, False): "cobo", (False, True): "coit", (True, True): "cobi"}


@dataclass(frozen=True)
class FontResolution:
    """What to draw replacement text with, and how much to trust it.

    Exactly one of (``fontname``, ``font_bytes``) is set: ``fontname`` for
    a PyMuPDF standard-14 short name, ``font_bytes`` for a font program to
    register with ``Page.insert_font(fontbuffer=...)``.
    """

    tier: str
    confidence: float
    fontname: str | None
    font_bytes: bytes | None
    requires_approval: bool
    note: str


def _standard_fallback_name(bold: bool, italic: bool, family_class: str) -> str:
    table = {"serif": _SERIF_FALLBACK, "monospace": _MONO_FALLBACK}.get(family_class, _SANS_FALLBACK)
    return table[(bold, italic)]


def resolve_font(
    classification: FontClassification,
    *,
    original_font_bytes: bytes | None,
    already_rendered_text: str,
    needed_text: str,
    font_index: list[FontCandidate],
) -> FontResolution:
    """Decide which font program an edit should draw `needed_text` with.

    `original_font_bytes` is the document's own font program (from
    ``pymupdf.Document.extract_font``), used only to measure Tier 3 target
    metrics when no exact name match is found -- a subset font with no
    ``cmap`` yields a weaker (but not useless) set of metrics this way, see
    engine.fonts.match.extract_metrics. `already_rendered_text` is every
    character already shown somewhere in the document with this font (see
    engine.fonts.style), unioned with `needed_text` when a full font is
    found, so the merged subset covers both. `font_index` is a pre-built
    engine.fonts.match.build_font_index() result; building it is
    expensive, so callers should build it once.
    """
    if classification.font_type == "Type3":
        fingerprint = infer_style_from_name(classification.base_font)
        name = _standard_fallback_name(fingerprint.bold, fingerprint.italic, fingerprint.family_class)
        return FontResolution(
            tier=TIER_FALLBACK,
            confidence=_FALLBACK_CONFIDENCE,
            fontname=name,
            font_bytes=None,
            requires_approval=True,
            note=(
                "Type3 font (FNT-15): its glyph procedures can't be drawn through "
                "PyMuPDF's text API, so a standard-font fallback is used instead"
            ),
        )

    if not classification.embedded:
        fingerprint = infer_style_from_name(classification.base_font)
        name = _standard_fallback_name(fingerprint.bold, fingerprint.italic, fingerprint.family_class)
        return FontResolution(
            tier=TIER_EXACT,
            confidence=1.0,
            fontname=name,
            font_bytes=None,
            requires_approval=False,
            note="standard (non-embedded) font, drawn with PyMuPDF's own encoding",
        )

    exact_match = find_by_name(font_index, classification.base_font)
    if exact_match is not None:
        characters = "".join(set(already_rendered_text) | set(needed_text))
        merged = build_merged_subset(exact_match.path, characters)
        return FontResolution(
            tier=TIER_EXACT,
            confidence=1.0,
            fontname=None,
            font_bytes=merged,
            requires_approval=False,
            note=f"same font found at {exact_match.path} and re-subset to cover the edit",
        )

    fingerprint = fingerprint_style(classification)
    target_metrics = extract_metrics(original_font_bytes) if original_font_bytes else None
    if target_metrics is None:
        name = _standard_fallback_name(fingerprint.bold, fingerprint.italic, fingerprint.family_class)
        return FontResolution(
            tier=TIER_FALLBACK,
            confidence=_FALLBACK_CONFIDENCE,
            fontname=name,
            font_bytes=None,
            requires_approval=True,
            note="no matching or measurable font found; using a standard-font fallback",
        )

    ranked = rank_by_metrics(
        target_metrics, font_index, covering=frozenset(needed_text), target_name=classification.base_font
    )
    if ranked:
        best = ranked[0]
        characters = "".join(set(already_rendered_text) | set(needed_text))
        merged = build_merged_subset(best.candidate.path, characters)
        return FontResolution(
            tier=TIER_APPROXIMATE,
            confidence=best.confidence,
            fontname=None,
            font_bytes=merged,
            requires_approval=True,
            note=f"closest metric match: {best.candidate.path.name}"
            + ("" if same_family(classification.base_font, best.candidate) else " (a different font family)"),
        )

    name = _standard_fallback_name(fingerprint.bold, fingerprint.italic, fingerprint.family_class)
    return FontResolution(
        tier=TIER_FALLBACK,
        confidence=_FALLBACK_CONFIDENCE,
        fontname=name,
        font_bytes=None,
        requires_approval=True,
        note="no font covers the needed characters; using a standard-font fallback",
    )
