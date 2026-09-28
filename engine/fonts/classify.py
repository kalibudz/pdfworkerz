"""FNT-03 (font classification) and FNT-04 (style fingerprint).

FNT-03 reads a font's own PDF dictionary -- type, embedding, subset tag,
encoding, ToUnicode -- directly and exactly; nothing here is inferred.
FNT-04 layers inference on top, because PDF gives no explicit "is this
bold" field: standard (non-embedded) fonts carry no FontDescriptor at all,
and even embedded fonts' descriptor flags are unreliable for weight
(ForceBold means "synthesize bold", not "this font is bold"; see
SPEC.md section 5.2). The reliable signal, confirmed against real output
from PyMuPDF/pikepdf, is the BaseFont name itself (`Arial-BoldMT`,
`Helvetica-Oblique`, ...), corroborated by ItalicAngle and StemV when a
descriptor is present.
"""

from __future__ import annotations

import re
from typing import Any

import pikepdf
from pydantic import BaseModel, ConfigDict

SUBSET_TAG_PATTERN = re.compile(r"^([A-Z]{6})\+(.+)$")

# ISO 32000-1 Table 123, the bits this module actually uses.
_FLAG_SERIF = 1 << 1
_FLAG_SYMBOLIC = 1 << 2
_FLAG_ITALIC = 1 << 6
_FLAG_FORCE_BOLD = 1 << 18

_BOLD_NAME_TOKENS = ("bold", "black", "heavy", "semibold", "demibold")
_ITALIC_NAME_TOKENS = ("italic", "oblique")
_MONOSPACE_NAME_TOKENS = ("mono", "courier", "consolas", "console")
_SERIF_NAME_TOKENS = ("times", "georgia", "garamond", "serif", "minion", "cambria", "book")
_SANS_NAME_TOKENS = ("arial", "helvetica", "verdana", "calibri", "sans", "segoe", "tahoma")


class FontClassification(BaseModel):
    """FNT-03: what a font's own PDF dictionary says about it."""

    model_config = ConfigDict(frozen=True)

    resource_name: str
    base_font: str
    subset_tag: str | None
    font_type: str
    """"Type1" | "TrueType" | "Type0" | "Type3" | "MMType1" | the raw /Subtype otherwise."""
    is_composite: bool
    descendant_type: str | None
    """"CIDFontType0" | "CIDFontType2", only set when is_composite."""
    embedded: bool
    embedded_format: str | None
    """"FontFile" (Type1) | "FontFile2" (TrueType) | "FontFile3" (CFF/OpenType), when embedded."""
    encoding: str | None
    has_to_unicode: bool
    flags: int | None
    italic_angle: float | None
    stem_v: float | None
    font_weight: float | None


class StyleFingerprint(BaseModel):
    """FNT-04: inferred visual style, for matching a replacement font (FNT-06/07)."""

    model_config = ConfigDict(frozen=True)

    bold: bool
    italic: bool
    family_class: str
    """"serif" | "sans" | "monospace" | "unknown"."""
    weight: int
    """CSS-style weight: 400 normal, 700 bold."""


def split_subset_tag(base_font: str) -> tuple[str | None, str]:
    match = SUBSET_TAG_PATTERN.match(base_font)
    if match:
        return match.group(1), match.group(2)
    return None, base_font


def _descriptor_for(font_dict: pikepdf.Object) -> tuple[pikepdf.Object | None, str | None]:
    """Return (FontDescriptor, descendant /Subtype); Type0 fonts nest both under DescendantFonts[0]."""
    if str(font_dict.get("/Subtype")) == "/Type0":
        descendants = font_dict.get("/DescendantFonts")
        if descendants:
            descendant = descendants[0]
            descendant_type = str(descendant.get("/Subtype", "")).lstrip("/") or None
            return descendant.get("/FontDescriptor"), descendant_type
    return font_dict.get("/FontDescriptor"), None


def _embedded_format(descriptor: pikepdf.Object | None) -> str | None:
    if descriptor is None:
        return None
    for key in ("/FontFile", "/FontFile2", "/FontFile3"):
        if key in descriptor:
            return key.lstrip("/")
    return None


def _descriptor_number(descriptor: pikepdf.Object | None, key: str) -> float | None:
    value = descriptor.get(key) if descriptor is not None else None
    return None if value is None else float(value)


def _encoding_name(font_dict: pikepdf.Object) -> str | None:
    encoding_obj = font_dict.get("/Encoding")
    if encoding_obj is None:
        return None
    if isinstance(encoding_obj, pikepdf.Dictionary):
        return str(encoding_obj.get("/BaseEncoding")) if "/BaseEncoding" in encoding_obj else None
    return str(encoding_obj)


def classify_font(pdf: pikepdf.Pdf, page_index: int, resource_name: str) -> FontClassification:
    """FNT-03: classify the font a page refers to by its Tf resource name (e.g. "F1").

    Only sees the page's own /Resources/Font. Text drawn inside a Form XObject
    uses the XObject's resources, where the same name can mean a different
    font -- use :func:`classify_font_xref` whenever the font's xref is known.
    """
    return _classify_font_dict(pdf.pages[page_index].Resources.Font[f"/{resource_name}"], resource_name)


def classify_font_xref(pdf: pikepdf.Pdf, xref: int, resource_name: str) -> FontClassification:
    """FNT-03 by object number: unambiguous for fonts inside Form XObjects, where
    a resource name like "F1" can collide with a different page-level font."""
    return _classify_font_dict(pdf.get_object((xref, 0)), resource_name)


def _classify_font_dict(font_dict: pikepdf.Object, resource_name: str) -> FontClassification:
    base_font = str(font_dict.get("/BaseFont", "")).lstrip("/")
    subset_tag, _ = split_subset_tag(base_font)
    subtype = str(font_dict.get("/Subtype", "")).lstrip("/")
    descriptor, descendant_type = _descriptor_for(font_dict)

    raw_flags = descriptor.get("/Flags") if descriptor is not None else None
    flags = None if raw_flags is None else int(raw_flags)
    italic_angle = _descriptor_number(descriptor, "/ItalicAngle")
    stem_v = _descriptor_number(descriptor, "/StemV")
    font_weight = _descriptor_number(descriptor, "/FontWeight")
    encoding = _encoding_name(font_dict)

    return FontClassification(
        resource_name=resource_name,
        base_font=base_font,
        subset_tag=subset_tag,
        font_type=subtype,
        is_composite=subtype == "Type0",
        descendant_type=descendant_type,
        embedded=_embedded_format(descriptor) is not None,
        embedded_format=_embedded_format(descriptor),
        encoding=encoding,
        has_to_unicode="/ToUnicode" in font_dict,
        flags=flags,
        italic_angle=italic_angle,
        stem_v=stem_v,
        font_weight=font_weight,
    )


def _matches_any(name: str, tokens: tuple[str, ...]) -> bool:
    lowered = name.lower()
    return any(token in lowered for token in tokens)


def infer_style_from_name(base_font: str) -> StyleFingerprint:
    """The name-tokens-only part of FNT-04: what a BaseFont's own name says about
    its weight, slant and family, with no descriptor to corroborate it. Used
    directly for non-embedded (no-descriptor) fonts; fingerprint_style layers
    descriptor evidence on top of this for embedded ones."""
    _, plain_name = split_subset_tag(base_font)

    bold = _matches_any(plain_name, _BOLD_NAME_TOKENS)
    italic = _matches_any(plain_name, _ITALIC_NAME_TOKENS)

    if _matches_any(plain_name, _MONOSPACE_NAME_TOKENS):
        family_class = "monospace"
    elif _matches_any(plain_name, _SERIF_NAME_TOKENS):
        family_class = "serif"
    elif _matches_any(plain_name, _SANS_NAME_TOKENS):
        family_class = "sans"
    else:
        family_class = "unknown"

    return StyleFingerprint(bold=bold, italic=italic, family_class=family_class, weight=700 if bold else 400)


def fingerprint_style(classification: FontClassification) -> StyleFingerprint:
    """FNT-04: infer bold/italic/family from name tokens, corroborated by descriptor fields."""
    from_name = infer_style_from_name(classification.base_font)

    descriptor_says_bold = (classification.flags is not None and bool(classification.flags & _FLAG_FORCE_BOLD)) or (
        classification.font_weight is not None and classification.font_weight >= 600
    )
    bold = from_name.bold or descriptor_says_bold

    descriptor_says_italic = (classification.italic_angle is not None and abs(classification.italic_angle) >= 1.0) or (
        classification.flags is not None and bool(classification.flags & _FLAG_ITALIC)
    )
    italic = from_name.italic or descriptor_says_italic

    if from_name.family_class != "unknown":
        family_class = from_name.family_class
    elif classification.flags is not None and bool(classification.flags & _FLAG_SERIF):
        family_class = "serif"
    else:
        family_class = "unknown"

    weight = 700 if bold else 400
    if classification.font_weight is not None:
        weight = int(classification.font_weight)

    return StyleFingerprint(bold=bold, italic=italic, family_class=family_class, weight=weight)


def list_page_fonts(doc: Any, page_index: int) -> list[str]:
    """The Tf resource names (without the leading '/') a page's fonts are registered under."""
    return [name for _xref, _ext, _type, _basefont, name, *_rest in doc[page_index].get_fonts(full=True)]
