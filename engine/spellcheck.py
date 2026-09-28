"""EDT-11: offline spell-check against bundled Hunspell dictionaries.

Checking is read-only: it reports misspelled words with where they are
(span, character offsets and a page-space box for underlining) and
suggestions. Fixing one is an ordinary text edit -- the caller replaces the
word inside its span with ReplaceSpanTextOp, so a correction goes through
the same style-matched, verified, undoable path as any other edit.

Words are the runs of letters (with inner apostrophes and hyphens) in each
span's text. By default three kinds of token are skipped, as in most
editors' spell-check settings: anything containing a digit, ALL-CAPS words
(acronyms), and words with a capital after the first letter
(product names like "PDFWorkerz", identifiers like "getText").

Dictionaries are read with spylls (a pure-Python Hunspell), once per
language per process -- loading en_US takes about half a second, a lookup
is immediate, and suggestions for one word take ~0.1-0.3s, so they're
computed only for the first `max_suggested` misspellings on a page.
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict
from spylls.hunspell import Dictionary

from engine.document import Document
from engine.errors import OpValidationError
from engine.fonts.style import extract_page_spans

DICTIONARIES_DIR = Path(__file__).resolve().parent.parent / "assets" / "dictionaries"

_TYPOGRAPHIC_APOSTROPHE = "\u2019"
_WORD = re.compile(r"[^\W\d_](?:[^\W\d_]|['\u2019\-](?=[^\W\d_]))*")
_INNER_CAPITAL = re.compile(r".[A-Z]")


class Misspelling(BaseModel):
    """One word the dictionary doesn't know."""

    model_config = ConfigDict(frozen=True)

    span_index: int
    start: int
    """Character offset of the word within its span's text."""
    end: int
    word: str
    bbox: tuple[float, float, float, float]
    """Page-space box around just this word, for underlining it."""
    suggestions: list[str]


def available_languages() -> list[str]:
    return sorted(aff.stem for aff in DICTIONARIES_DIR.glob("*.aff") if aff.with_suffix(".dic").exists())


@cache
def _dictionary(language: str) -> Any:
    if language not in available_languages():
        known = ", ".join(available_languages()) or "(none)"
        raise OpValidationError(f"no dictionary for {language!r}; available: {known}")
    return Dictionary.from_files(str(DICTIONARIES_DIR / language))


def _skipped(word: str) -> bool:
    return (len(word) > 1 and word.isupper()) or bool(_INNER_CAPITAL.search(word))


def check_word(word: str, language: str = "en_US") -> bool:
    dictionary = _dictionary(language)
    # Typographic apostrophes aren't in the dictionary's affix rules.
    return bool(dictionary.lookup(word.replace(_TYPOGRAPHIC_APOSTROPHE, "'")))


def suggest(word: str, language: str = "en_US", *, limit: int = 5) -> list[str]:
    suggestions: list[str] = []
    for candidate in _dictionary(language).suggest(word.replace(_TYPOGRAPHIC_APOSTROPHE, "'")):
        suggestions.append(str(candidate))
        if len(suggestions) >= limit:
            break
    return suggestions


def check_page(
    document: Document,
    page_index: int,
    *,
    language: str = "en_US",
    ignore: frozenset[str] = frozenset(),
    max_suggested: int = 25,
    suggestions_per_word: int = 5,
) -> list[Misspelling]:
    """Every misspelled word on one page, in reading order (top to bottom,
    then left to right -- not content-stream order, which an edit changes by
    appending redrawn text to the end).
    `ignore` holds words to accept anyway (compared case-insensitively)."""
    if not 0 <= page_index < document.page_count:
        raise OpValidationError(f"page_index {page_index} is out of range (document has {document.page_count} pages)")
    _dictionary(language)  # fail fast on an unknown language, even on an empty page
    ignored = {word.lower() for word in ignore}
    found: list[Misspelling] = []
    spans = extract_page_spans(document.raw, page_index)
    spans.sort(key=lambda s: (round(s.style.bbox[3]), s.style.bbox[0]))
    for span in spans:
        text = span.style.text
        for match in _WORD.finditer(text):
            word = match.group()
            if _skipped(word) or word.lower() in ignored or check_word(word, language):
                continue
            boxes = [char.bbox for char in span.style.chars[match.start() : match.end()]]
            bbox = (
                min(b[0] for b in boxes),
                min(b[1] for b in boxes),
                max(b[2] for b in boxes),
                max(b[3] for b in boxes),
            )
            wanted = len(found) < max_suggested
            found.append(
                Misspelling(
                    span_index=span.style.span_index,
                    start=match.start(),
                    end=match.end(),
                    word=word,
                    bbox=bbox,
                    suggestions=suggest(word, language, limit=suggestions_per_word) if wanted else [],
                )
            )
    return found
