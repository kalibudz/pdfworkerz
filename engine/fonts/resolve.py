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
full font -- whenever that font can be found. The first place looked is the
document's own embedded program: when it still has a usable ``cmap`` covering
every character needed (a fully embedded font, as bank-statement generators
often write, or a subset that happens to cover the edit), the fresh subset is
cut from those bytes directly, with no external font file involved. The result is still the
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

import dataclasses
import io
from dataclasses import dataclass

from fontTools.pens.boundsPen import BoundsPen
from fontTools.ttLib import TTFont

from engine.fonts.classify import FontClassification, fingerprint_style, infer_style_from_name, split_subset_tag
from engine.fonts.coverage import check_coverage
from engine.fonts.match import (
    FontCandidate,
    extract_metrics,
    find_by_name,
    normalize_font_name,
    rank_by_metrics,
    same_family,
)
from engine.fonts.merge import build_merged_subset

TIER_EXACT = "exact"
TIER_APPROXIMATE = "approximate"
TIER_FALLBACK = "fallback"

_FALLBACK_CONFIDENCE = 0.2
_METRIC_COMPATIBLE_CONFIDENCE = 0.8

# normalize_font_name() forms of the 14 standard PDF fonts (ISO 32000-1 9.6.2.2).
_STANDARD_14 = frozenset(
    {
        "helvetica",
        "helveticabold",
        "helveticaoblique",
        "helveticaboldoblique",
        "timesroman",
        "timesbold",
        "timesitalic",
        "timesbolditalic",
        "courier",
        "courierbold",
        "courieroblique",
        "courierboldoblique",
    }
)
_SYMBOLIC_BUILTINS = {"symbol": "symb", "zapfdingbats": "zadb"}
# Widely used fonts whose widths match a standard-14 font (Arial/Helvetica and so on).
_METRIC_COMPATIBLE = frozenset(
    {
        "arial",
        "arialmt",
        "arialbold",
        "arialboldmt",
        "arialitalic",
        "arialitalicmt",
        "arialbolditalic",
        "arialbolditalicmt",
        "timesnewroman",
        "timesnewromanpsmt",
        "timesnewromanbold",
        "timesnewromanpsboldmt",
        "timesnewromanitalic",
        "timesnewromanpsitalicmt",
        "timesnewromanbolditalic",
        "timesnewromanpsbolditalicmt",
        "couriernew",
        "couriernewpsmt",
        "couriernewbold",
        "couriernewpsboldmt",
    }
)

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
    postscript_name: str | None = None
    """The name to register `font_bytes` under on the page (its /BaseFont), so text drawn
    with it can be found by that name later -- by a move, restyle or format paint. None
    for a built-in standard font (`fontname`)."""


FSTYPE_RESTRICTED = 0x0002
"""OS/2 fsType bit 1: "Restricted License embedding" -- the font's vendor forbids
embedding it in any document, so its program is never reused for new text."""


def embedded_program_covers(font_bytes: bytes, characters: str) -> bool:
    """Whether a document's own font program can draw `characters` itself: it loads,
    its license permits embedding, its ``cmap`` maps every character, and every
    visible character's glyph has an outline. A typical subset has no ``cmap`` at all
    (see the module docstring); some subsetters keep the whole ``cmap`` but blank the
    outlines of unused glyphs (found in a real statement's Roboto subsets, where "Q"
    and "z" were mapped but empty) -- both answer False rather than draw nothing."""
    try:
        tt = TTFont(io.BytesIO(font_bytes), lazy=True, fontNumber=0)
        os2 = tt.get("OS/2", None)
        if os2 is not None and os2.fsType & FSTYPE_RESTRICTED:
            return False
        cmap = tt.getBestCmap() or {}
        if not cmap:
            return False
        glyph_set = tt.getGlyphSet()
        for char in set(characters):
            if char in "\t\n\r":
                continue
            glyph_name = cmap.get(ord(char))
            if glyph_name is None or glyph_name not in glyph_set:
                return False
            if char.isspace():
                continue  # a space has no outline by design
            pen = BoundsPen(glyph_set)
            glyph_set[glyph_name].draw(pen)
            if pen.bounds is None:
                return False
    except Exception:
        return False
    return True


def usable_font_name(name: str | None) -> str | None:
    """`name` if it can identify a font, else None. PyMuPDF writes the literal "(null)"
    as the BaseFont of a font program that has no name table."""
    if not name or name.strip() in ("(null)", "null"):
        return None
    return name


def _standard_fallback_name(bold: bool, italic: bool, family_class: str) -> str:
    table = {"serif": _SERIF_FALLBACK, "monospace": _MONO_FALLBACK}.get(family_class, _SANS_FALLBACK)
    return table[(bold, italic)]


def _with_coverage_note(resolution: FontResolution, needed_text: str) -> FontResolution:
    """Flag it when `needed_text` has characters a PyMuPDF standard-14 built-in can't
    actually draw -- confirmed empirically: those 14 fonts cover plain Latin-1 only
    (not full WinAnsiEncoding), and PyMuPDF silently draws a replacement-glyph mark for
    anything else instead of refusing, which otherwise reached the page with no warning
    at all, sometimes (the "standard-14, not embedded" case) at the *exact* tier, which
    needs no approval and so was never seen before the edit landed. Only applies to a
    resolution naming a built-in font (`fontname` set, never `font_bytes`); the project's
    own symbolic fonts (Symbol, ZapfDingbats) use a different encoding entirely and are
    never routed through this check."""
    coverage = check_coverage(
        font_type="TrueType", embedded=False, font_bytes=None, already_rendered_text="", characters=needed_text
    )
    if coverage.fully_covered:
        return resolution
    missing = "".join(sorted(coverage.missing))
    note = (
        f"{resolution.note}; {len(coverage.missing)} character(s) ({missing!r}) have no glyph in "
        f"{resolution.fontname} and would not be drawn correctly"
    )
    return dataclasses.replace(resolution, requires_approval=True, note=note)


def _resolve_non_embedded(
    classification: FontClassification, already_rendered_text: str, needed_text: str, font_index: list[FontCandidate]
) -> FontResolution:
    """A font the PDF only names. Only the standard 14 are drawn "exactly" by PyMuPDF's
    built-ins (Symbol and ZapfDingbats as themselves, never as Helvetica); any other
    name is exact only if that same font is installed, and otherwise a substitute that
    needs approval -- it used to be reported as exact and drawn in Helvetica regardless."""
    base = split_subset_tag(classification.base_font)[1]
    key = normalize_font_name(base)
    fingerprint = infer_style_from_name(classification.base_font)
    standard = _standard_fallback_name(fingerprint.bold, fingerprint.italic, fingerprint.family_class)

    symbolic = _SYMBOLIC_BUILTINS.get(key)
    if symbolic is not None:
        return FontResolution(
            tier=TIER_EXACT,
            confidence=1.0,
            fontname=symbolic,
            font_bytes=None,
            requires_approval=False,
            note="standard-14 font (not embedded), drawn with PyMuPDF's built-in copy",
        )
    if key in _STANDARD_14:
        return _with_coverage_note(
            FontResolution(
                tier=TIER_EXACT,
                confidence=1.0,
                fontname=standard,
                font_bytes=None,
                requires_approval=False,
                note="standard-14 font (not embedded), drawn with PyMuPDF's built-in copy",
            ),
            needed_text,
        )

    installed = find_by_name(font_index, classification.base_font)
    if installed is not None:
        characters = "".join(set(already_rendered_text) | set(needed_text))
        return FontResolution(
            tier=TIER_EXACT,
            confidence=1.0,
            fontname=None,
            font_bytes=build_merged_subset(installed.path, characters),
            requires_approval=False,
            note=f"font not embedded in the PDF; the same font was found at {installed.path} and embedded",
            postscript_name=installed.postscript_name or base,
        )

    if key in _METRIC_COMPATIBLE:
        return _with_coverage_note(
            FontResolution(
                tier=TIER_APPROXIMATE,
                confidence=_METRIC_COMPATIBLE_CONFIDENCE,
                fontname=standard,
                font_bytes=None,
                requires_approval=True,
                note=f"{base} is not embedded or installed; drawn with its metric-compatible standard font",
            ),
            needed_text,
        )
    return _with_coverage_note(
        FontResolution(
            tier=TIER_FALLBACK,
            confidence=_FALLBACK_CONFIDENCE,
            fontname=standard,
            font_bytes=None,
            requires_approval=True,
            note=f"{base} is not embedded or installed; using a standard-font fallback",
        ),
        needed_text,
    )


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
        return _with_coverage_note(
            FontResolution(
                tier=TIER_FALLBACK,
                confidence=_FALLBACK_CONFIDENCE,
                fontname=name,
                font_bytes=None,
                requires_approval=True,
                note=(
                    "Type3 font (FNT-15): its glyph procedures can't be drawn through "
                    "PyMuPDF's text API, so a standard-font fallback is used instead"
                ),
            ),
            needed_text,
        )

    if not classification.embedded:
        return _resolve_non_embedded(classification, already_rendered_text, needed_text, font_index)

    characters = "".join(set(already_rendered_text) | set(needed_text))
    if original_font_bytes and embedded_program_covers(original_font_bytes, characters):
        return FontResolution(
            tier=TIER_EXACT,
            confidence=1.0,
            fontname=None,
            font_bytes=build_merged_subset(original_font_bytes, characters),
            requires_approval=False,
            note="the document's own embedded font, re-subset to cover the edit",
            # The PDF's own name for it: the program itself may carry no name at all.
            postscript_name=usable_font_name(split_subset_tag(classification.base_font)[1]),
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
            postscript_name=exact_match.postscript_name or None,
        )

    fingerprint = fingerprint_style(classification)
    target_metrics = extract_metrics(original_font_bytes) if original_font_bytes else None
    if target_metrics is None:
        name = _standard_fallback_name(fingerprint.bold, fingerprint.italic, fingerprint.family_class)
        return _with_coverage_note(
            FontResolution(
                tier=TIER_FALLBACK,
                confidence=_FALLBACK_CONFIDENCE,
                fontname=name,
                font_bytes=None,
                requires_approval=True,
                note="no matching or measurable font found; using a standard-font fallback",
            ),
            needed_text,
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
            # Its own name, never the original's: a look-alike must not be found later
            # as if it were the original font.
            postscript_name=best.candidate.postscript_name or None,
        )

    name = _standard_fallback_name(fingerprint.bold, fingerprint.italic, fingerprint.family_class)
    return _with_coverage_note(
        FontResolution(
            tier=TIER_FALLBACK,
            confidence=_FALLBACK_CONFIDENCE,
            fontname=name,
            font_bytes=None,
            requires_approval=True,
            note="no font covers the needed characters; using a standard-font fallback",
        ),
        needed_text,
    )
