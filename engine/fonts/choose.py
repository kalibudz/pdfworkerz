"""Fonts chosen by the user by family and style (EDT-03 explicit style, EDT-06 change font
or weight), as opposed to engine.fonts.resolve, which finds the font a span already uses.

A choice is either one of PyMuPDF's built-in standard fonts (Helvetica, Times, Courier) or a
font file from the font index (bundled OFL fonts plus the fonts installed on this machine),
embedded as a subset covering the text. Asking for a family or style that isn't available
is an error that lists what is -- never a silent substitute.
"""

from __future__ import annotations

from engine.errors import OpValidationError
from engine.fonts.classify import infer_style_from_name, split_subset_tag
from engine.fonts.match import FontCandidate, find_by_name, normalize_font_name
from engine.fonts.merge import build_merged_subset
from engine.fonts.resolve import TIER_EXACT, FontResolution

# Built-in standard families: (bold, italic) -> PyMuPDF short name.
_BUILTINS: dict[str, dict[tuple[bool, bool], str]] = {
    "helvetica": {(False, False): "helv", (True, False): "hebo", (False, True): "heit", (True, True): "hebi"},
    "times": {(False, False): "tiro", (True, False): "tibo", (False, True): "tiit", (True, True): "tibi"},
    "courier": {(False, False): "cour", (True, False): "cobo", (False, True): "coit", (True, True): "cobi"},
}
_BUILTIN_LABELS = {"helvetica": "Helvetica", "times": "Times", "courier": "Courier"}


def _style_of(candidate: FontCandidate) -> tuple[bool, bool]:
    sub = candidate.subfamily_name.lower()
    return ("bold" in sub or "black" in sub or "heavy" in sub, "italic" in sub or "oblique" in sub)


def available_families(font_index: list[FontCandidate]) -> list[str]:
    """Every family a user can choose, standard families first, then the index's, alphabetically."""
    indexed = sorted({c.family_name for c in font_index if c.family_name}, key=str.lower)
    builtins = list(_BUILTIN_LABELS.values())
    return builtins + [name for name in indexed if normalize_font_name(name) not in _BUILTINS]


def family_of(base_font: str, font_index: list[FontCandidate]) -> str:
    """The family a span's font belongs to, for "make this bold" without naming a family."""
    found = find_by_name(font_index, base_font)
    if found is not None:
        return found.family_name
    fingerprint = infer_style_from_name(base_font)
    return {"serif": "Times", "monospace": "Courier"}.get(fingerprint.family_class, "Helvetica")


def is_bold_italic(base_font: str, font_index: list[FontCandidate]) -> tuple[bool, bool]:
    found = find_by_name(font_index, base_font)
    if found is not None:
        return _style_of(found)
    fingerprint = infer_style_from_name(split_subset_tag(base_font)[1])
    return fingerprint.bold, fingerprint.italic


def resolve_chosen_font(
    family: str, *, bold: bool, italic: bool, text: str, font_index: list[FontCandidate]
) -> FontResolution:
    """The FontResolution for drawing `text` in `family` at the given weight and slant."""
    key = normalize_font_name(family)
    if key in _BUILTINS:
        return FontResolution(
            tier=TIER_EXACT,
            confidence=1.0,
            fontname=_BUILTINS[key][(bold, italic)],
            font_bytes=None,
            requires_approval=False,
            note=f"{_BUILTIN_LABELS[key]} chosen by the user (a built-in standard font)",
        )

    members = [c for c in font_index if normalize_font_name(c.family_name) == key]
    if not members:
        raise OpValidationError(
            f"no font family named {family!r} is available; choose one of: {', '.join(available_families(font_index))}"
        )
    exact = [c for c in members if _style_of(c) == (bold, italic)]
    if not exact:
        styles = sorted({c.subfamily_name for c in members})
        wanted = " ".join(word for word, on in (("bold", bold), ("italic", italic)) if on) or "regular"
        raise OpValidationError(f"{family} has no {wanted} style here; available: {', '.join(styles)}")
    chosen = sorted(exact, key=lambda c: (c.source != "bundled", len(c.subfamily_name)))[0]
    return FontResolution(
        tier=TIER_EXACT,
        confidence=1.0,
        fontname=None,
        font_bytes=build_merged_subset(chosen.path, text),
        requires_approval=False,
        note=f"{chosen.family_name} {chosen.subfamily_name} chosen by the user ({chosen.path.name})",
        postscript_name=chosen.postscript_name or None,
    )
