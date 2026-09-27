"""FNT-06 (full-font lookup) and FNT-07 (metric-similarity matching with confidence).

Used when FNT-05 finds a document's own font can't show a piece of
replacement text (Tier 1 fails, SPEC.md section 5.3). Two tiers follow:

- **Tier 2, name lookup (FNT-06):** search the system's installed fonts and
  PDFWorkerz's own bundled fallback family (``assets/fonts/``) for a font
  whose own name matches the document's BaseFont, subset tag stripped.
  When found, it's the *same* font, just not embedded in this PDF --
  an exact style match.
- **Tier 3, metric match (FNT-07):** when no exact name match exists, rank
  every candidate that covers the needed characters by how closely its
  measured metrics (cap height, x-height, average advance width,
  ascender/descender, weight, italic angle, fixed-pitch) resemble the
  original font's, and report a confidence score. SPEC.md requires the
  user to approve a Tier 3 match before it's used (never applied silently).

All metrics are read directly from each font's own tables (``head``,
``hhea``, ``OS/2``, ``post``, ``hmtx``) via fontTools, falling back to
measuring glyph outlines directly (``H`` for cap height, ``x`` for
x-height) when a font's ``OS/2`` table leaves those fields unset -- both
paths verified against real font files before being relied on here.
"""

from __future__ import annotations

import io
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from fontTools.pens.boundsPen import BoundsPen
from fontTools.ttLib import TTFont

from engine.fonts.classify import split_subset_tag
from engine.fonts.coverage import check_coverage

BUNDLED_FONTS_DIR = Path(__file__).resolve().parent.parent.parent / "assets" / "fonts"
_FONT_EXTENSIONS = ("*.ttf", "*.otf", "*.ttc")
_SAMPLE_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


@dataclass(frozen=True)
class FontCandidate:
    """One font file found by FNT-06, with the names read from its own tables."""

    path: Path
    family_name: str
    subfamily_name: str
    postscript_name: str
    source: str
    """"bundled" | "system"."""


@dataclass(frozen=True)
class FontMetrics:
    """FNT-07's comparable, unitsPerEm-normalized shape metrics for one font."""

    cap_height_ratio: float
    x_height_ratio: float
    average_width_ratio: float
    ascender_ratio: float
    descender_ratio: float
    italic_angle: float
    is_fixed_pitch: bool
    weight_class: int


@dataclass(frozen=True)
class MatchCandidate:
    candidate: FontCandidate
    confidence: float
    """0.0-1.0; SPEC.md section 5.3 requires user approval below the "high" tier."""


def _system_font_dirs() -> list[Path]:
    import sys

    if sys.platform == "win32":
        import os

        return [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"]
    if sys.platform == "darwin":
        return [Path("/System/Library/Fonts"), Path("/Library/Fonts"), Path.home() / "Library/Fonts"]
    return [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), Path.home() / ".local/share/fonts"]


def _iter_font_files(directory: Path) -> Iterator[Path]:
    if not directory.is_dir():
        return
    for pattern in _FONT_EXTENSIONS:
        yield from directory.rglob(pattern)


def _read_font_names(path: Path) -> tuple[str, str, str] | None:
    try:
        tt = TTFont(path, lazy=True, fontNumber=0)
        name_table = tt["name"]
        family = name_table.getDebugName(16) or name_table.getDebugName(1) or ""
        subfamily = name_table.getDebugName(17) or name_table.getDebugName(2) or ""
        postscript = name_table.getDebugName(6) or ""
    except Exception:
        return None
    if not (family or postscript):
        return None
    return family, subfamily, postscript


def build_font_index(*, include_system: bool = True, extra_dirs: list[Path] | None = None) -> list[FontCandidate]:
    """FNT-06: scan the bundled fallback family, the system's fonts, and any extra
    directories, returning every readable font found. This does real filesystem and
    font-parsing work; callers should build it once and reuse it, not call it per edit.
    """
    directories = [BUNDLED_FONTS_DIR, *(extra_dirs or [])]
    if include_system:
        directories.extend(_system_font_dirs())

    candidates: list[FontCandidate] = []
    for directory in directories:
        source = "bundled" if directory == BUNDLED_FONTS_DIR else "system"
        for path in _iter_font_files(directory):
            names = _read_font_names(path)
            if names is None:
                continue
            family, subfamily, postscript = names
            candidates.append(
                FontCandidate(
                    path=path,
                    family_name=family,
                    subfamily_name=subfamily,
                    postscript_name=postscript,
                    source=source,
                )
            )
    return candidates


def normalize_font_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def find_by_name(index: list[FontCandidate], base_font: str) -> FontCandidate | None:
    """FNT-06: an exact (normalized) name match for `base_font` (its subset tag, if any,
    is ignored -- see engine.fonts.classify.SUBSET_TAG_PATTERN)."""
    _, plain_name = split_subset_tag(base_font)
    target = normalize_font_name(plain_name)
    if not target:
        return None
    for candidate in index:
        if normalize_font_name(candidate.postscript_name) == target:
            return candidate
        if normalize_font_name(candidate.family_name + candidate.subfamily_name) == target:
            return candidate
    return None


def _bbox_top_ratio(glyph_set: object, cmap: dict[int, str], char: str, units_per_em: float) -> float | None:
    glyph_name = cmap.get(ord(char))
    if glyph_name is None or glyph_name not in glyph_set:  # type: ignore[operator]
        return None
    pen = BoundsPen(glyph_set)
    glyph_set[glyph_name].draw(pen)  # type: ignore[index]
    return None if pen.bounds is None else pen.bounds[3] / units_per_em


def _os2_ratio(os2: object, field: str, units_per_em: float) -> float | None:
    value = getattr(os2, field, 0)
    return value / units_per_em if value else None


def extract_metrics(source: Path | bytes) -> FontMetrics | None:
    """FNT-07: read (or measure) a font's comparable shape metrics.

    `source` is a font file path, or the font program's own bytes (as
    extracted from a PDF via ``pymupdf.Document.extract_font``).
    """
    # `head`/`hhea`/`hmtx` are required for a font to render at all, so a missing one
    # means a genuinely broken font program -- return None. `OS/2`, `post` and `cmap`
    # are all optional and, confirmed empirically, are exactly what PyMuPDF's
    # subsetting drops (it only keeps what its own renderer needs): read each with a
    # graceful fallback instead of letting a KeyError abort the whole extraction.
    try:
        tt = TTFont(io.BytesIO(source) if isinstance(source, bytes) else source, lazy=True, fontNumber=0)
        units_per_em = float(tt["head"].unitsPerEm)
        hhea = tt["hhea"]
        hmtx = tt["hmtx"]
    except Exception:
        return None

    post = tt.get("post", None)
    os2 = tt.get("OS/2", None)
    try:
        cmap = tt.getBestCmap() or {}
    except Exception:
        cmap = {}
    glyph_set = tt.getGlyphSet()

    cap_height = _os2_ratio(os2, "sCapHeight", units_per_em) or _bbox_top_ratio(glyph_set, cmap, "H", units_per_em)
    x_height = _os2_ratio(os2, "sxHeight", units_per_em) or _bbox_top_ratio(glyph_set, cmap, "x", units_per_em)

    widths = []
    for char in _SAMPLE_LETTERS:
        glyph_name = cmap.get(ord(char))
        if glyph_name is not None and glyph_name in hmtx.metrics:
            widths.append(hmtx[glyph_name][0])

    return FontMetrics(
        cap_height_ratio=cap_height if cap_height is not None else 0.7,
        x_height_ratio=x_height if x_height is not None else 0.5,
        average_width_ratio=(sum(widths) / len(widths) / units_per_em) if widths else 0.5,
        ascender_ratio=hhea.ascender / units_per_em,
        descender_ratio=hhea.descender / units_per_em,
        italic_angle=float(post.italicAngle) if post is not None else 0.0,
        is_fixed_pitch=bool(post.isFixedPitch) if post is not None else False,
        weight_class=int(os2.usWeightClass) if os2 is not None else 400,
    )


def metric_distance(target: FontMetrics, candidate: FontMetrics) -> float:
    """A lower-is-more-similar score. Weights favor the metrics that most affect
    whether replacement text looks like it belongs: cap/x-height and average
    width (how wide the text block runs) count for more than weight or angle."""
    distance = 0.0
    distance += abs(target.cap_height_ratio - candidate.cap_height_ratio) * 4
    distance += abs(target.x_height_ratio - candidate.x_height_ratio) * 4
    distance += abs(target.average_width_ratio - candidate.average_width_ratio) * 4
    distance += abs(target.ascender_ratio - candidate.ascender_ratio) * 1
    distance += abs(target.descender_ratio - candidate.descender_ratio) * 1
    distance += (abs(target.weight_class - candidate.weight_class) / 800) * 2
    distance += 0 if target.is_fixed_pitch == candidate.is_fixed_pitch else 2
    distance += (min(abs(target.italic_angle - candidate.italic_angle), 45) / 45) * 1
    return distance


def _confidence_from_distance(distance: float) -> float:
    """0 distance -> 1.0; confidence decays to 0 by distance 6 (empirically, a
    same-family regular-vs-bold comparison scores well under 1; an unrelated
    serif-vs-monospace pairing scores well over 3)."""
    return max(0.0, 1.0 - distance / 6.0)


def rank_by_metrics(
    target: FontMetrics, index: list[FontCandidate], *, covering: frozenset[str] | None = None
) -> list[MatchCandidate]:
    """FNT-07: every candidate, ranked most-to-least similar to `target`.

    When `covering` is given, candidates that don't cover every character in
    it are dropped entirely -- a close metric match is useless if it can't
    draw the text.
    """
    ranked = []
    for candidate in index:
        metrics = extract_metrics(candidate.path)
        if metrics is None:
            continue
        if covering:
            coverage = check_coverage(
                font_type="TrueType",
                embedded=True,
                font_bytes=candidate.path.read_bytes(),
                already_rendered_text="",
                characters="".join(covering),
            )
            if not coverage.fully_covered:
                continue
        distance = metric_distance(target, metrics)
        ranked.append(MatchCandidate(candidate=candidate, confidence=_confidence_from_distance(distance)))
    ranked.sort(key=lambda m: m.confidence, reverse=True)
    return ranked
