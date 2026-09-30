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
from engine.fonts.research import data_dir

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
    """"bundled" | "user" (engine.fonts.library) | "system"."""


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


def user_fonts_dir() -> Path:
    """The user's own font library folder (engine.fonts.library)."""
    return data_dir() / "fonts"


def scan_font_directory(directory: Path, source: str) -> list[FontCandidate]:
    """Every readable font file in `directory` (recursively)."""
    candidates: list[FontCandidate] = []
    for path in _iter_font_files(directory):
        names = _read_font_names(path)
        if names is None:
            continue
        family, subfamily, postscript = names
        candidates.append(
            FontCandidate(
                path=path, family_name=family, subfamily_name=subfamily, postscript_name=postscript, source=source
            )
        )
    return candidates


def build_font_index(
    *, include_system: bool = True, include_user: bool = True, extra_dirs: list[Path] | None = None
) -> list[FontCandidate]:
    """FNT-06: scan the user's own font library, the bundled fallback family, any extra
    directories and the system's fonts, returning every readable font found. This does
    real filesystem and font-parsing work; callers should build it once and reuse it,
    not call it per edit (engine.ops.text caches the slow part and rescans only the
    small user library when it changes).
    """
    # The user's library first: it holds exactly the fonts added for their documents.
    candidates = scan_font_directory(user_fonts_dir(), "user") if include_user else []
    candidates += scan_font_directory(BUNDLED_FONTS_DIR, "bundled")
    for directory in extra_dirs or []:
        candidates += scan_font_directory(directory, "system")
    if include_system:
        for directory in _system_font_dirs():
            candidates += scan_font_directory(directory, "system")
    return candidates


def normalize_font_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


_REGULAR_SUBFAMILIES = frozenset({"regular", "normal", "book", "roman", "plain"})
_VENDOR_SUFFIXES = ("psmt", "mt")
"""Monotype's PostScript-name suffixes: a PDF may call Windows' Arial "Arial" or
"ArialMT", and its bold "Arial-BoldMT" or "Arial,Bold" -- all the same fonts."""


def _without_vendor_suffix(key: str) -> str:
    for suffix in _VENDOR_SUFFIXES:
        if key.endswith(suffix) and len(key) > len(suffix) + 2:
            return key[: -len(suffix)]
    return key


_STYLE_WORD = re.compile(r"(regular|normal|book|roman|plain)$")


def loose_font_key(name: str) -> str:
    """A font name with case, punctuation, a trailing regular-like style word and
    Monotype's MT/PSMT suffix removed: "ArialMT", "Arial Regular" and "Arial" all give
    "arial". Used to connect the two names one font goes by inside a PDF."""
    key = _without_vendor_suffix(normalize_font_name(name))
    stripped = _STYLE_WORD.sub("", key)
    return stripped if len(stripped) >= 3 else key


def _name_keys(candidate: FontCandidate) -> list[str]:
    """Every normalized name `candidate` answers to, most specific first."""
    keys = [normalize_font_name(candidate.postscript_name)]
    family = normalize_font_name(candidate.family_name)
    subfamily = normalize_font_name(candidate.subfamily_name)
    keys.append(family + subfamily)
    if subfamily in _REGULAR_SUBFAMILIES or not subfamily:
        keys.append(family)  # "Arial" names Arial Regular, never Arial Bold or Arial Narrow
    return [_without_vendor_suffix(key) for key in keys]


def find_by_name(index: list[FontCandidate], base_font: str) -> FontCandidate | None:
    """FNT-06: an exact (normalized) name match for `base_font` (its subset tag, if any,
    is ignored -- see engine.fonts.classify.SUBSET_TAG_PATTERN). "Arial", "ArialMT" and
    "DWHDKR+Arial" all find Arial Regular; a regular-like style name ("Regular",
    "Book", ...) counts as no style name at all."""
    _, plain_name = split_subset_tag(base_font)
    target = _without_vendor_suffix(normalize_font_name(plain_name))
    if not target:
        return None
    # PostScript names first across the whole index: they identify one font exactly,
    # where a family name alone might also match an unrelated file's loose naming.
    for depth in range(3):
        for candidate in index:
            keys = _name_keys(candidate)
            if depth < len(keys) and keys[depth] and keys[depth] == target:
                return candidate
    return None


_MIN_FAMILY_PREFIX = 4
CROSS_FAMILY_FACTOR = 0.75
"""Metric confidence is multiplied by this when the candidate is a different
family from the document's font. Metric ratios alone differ little between
unrelated sans-serifs: on a real statement, Delta-Book -> Maiandra GD and
Roboto -> Trebuchet both scored 0.98, overstating how alike they look."""


def same_family(base_font: str, candidate: FontCandidate) -> bool:
    """Whether `candidate` is plausibly the same family as a PDF font named
    `base_font` ("ABCDEF+Roboto-Light" vs family "Roboto"; "ArialMT" vs
    "Arial"): one normalized family name is a prefix of the other's."""
    _, plain = split_subset_tag(base_font)
    target = normalize_font_name(re.split(r"[-,]", plain, maxsplit=1)[0])
    family = normalize_font_name(candidate.family_name)
    if len(target) < _MIN_FAMILY_PREFIX or len(family) < _MIN_FAMILY_PREFIX:
        return target == family and bool(target)
    return target.startswith(family) or family.startswith(target)


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
    try:
        # A font with neither a `glyf` nor a `CFF `/`CFF2` table (a bitmap-only or
        # color-bitmap font, such as some system emoji fonts) has no outlines at all --
        # confirmed via a real one found among a CI runner's installed fonts. It can't
        # be used to draw or shape-compare text, so it's not a usable Tier 3 candidate;
        # treat it the same as any other unreadable font rather than crashing ranking.
        glyph_set = tt.getGlyphSet()
    except Exception:
        return None

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
    target: FontMetrics,
    index: list[FontCandidate],
    *,
    covering: frozenset[str] | None = None,
    target_name: str | None = None,
) -> list[MatchCandidate]:
    """FNT-07: every candidate, ranked most-to-least similar to `target`.

    When `covering` is given, candidates that don't cover every character in
    it are dropped entirely -- a close metric match is useless if it can't
    draw the text. When `target_name` (the PDF font's BaseFont) is given, a
    candidate from a different family has its confidence scaled by
    CROSS_FAMILY_FACTOR, so a same-family variant outranks a stranger with
    similar proportions.
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
        confidence = _confidence_from_distance(metric_distance(target, metrics))
        if target_name is not None and not same_family(target_name, candidate):
            confidence *= CROSS_FAMILY_FACTOR
        ranked.append(MatchCandidate(candidate=candidate, confidence=confidence))
    ranked.sort(key=lambda m: m.confidence, reverse=True)
    return ranked
