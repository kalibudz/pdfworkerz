"""FNT-20: recognising an icon or emoji glyph, so an edit never redraws it away.

Found from live use: a real document's headings and table labels each open with an
emoji drawn in its own Type3 font (how Google Docs and Chrome export emoji -- confirmed
against the file itself, not assumed). ``engine.fonts.units.split_words`` glued the icon
to the word after it ("✅OBJECTIVES"), so bolding the heading, or inserting text next to
it, redrew the icon too -- and a Type3 glyph can't actually be redrawn (FNT-15), so it
silently fell back to a standard font that has no such glyph and came out blank.

An icon glyph is one of:

- a character in the common emoji ranges: U+1F000-1FFFF (the astral-plane emoji
  blocks: emoticons, transport/map symbols, supplemental symbols and pictographs,
  pictographs extended-A, playing cards, regional-indicator flag letters, skin-tone
  modifiers, ...), U+2600-27BF (misc symbols and dingbats -- ✅ is here), or
  U+2300-23FF (misc technical -- hourglass, watch, ...);
- a private-use character (Unicode category ``Co``), the usual encoding for a custom
  icon font's own glyphs;
- ``U+FFFD`` (the "unknown character" placeholder texttrace reports for a glyph with
  no recorded Unicode meaning) specifically in a Type3 font, where it really does mean
  "some icon glyph", not "this text is corrupt";
- any glyph in a font named like a dingbat/icon/emoji font (Wingdings, Webdings,
  ZapfDingbats, "Segoe UI Emoji", ...), since such fonts commonly map an icon to an
  otherwise ordinary-looking character code.

Deliberately **not** icons: (c) (r) (tm) (degree sign) and other ordinary text
symbols, a bullet (`*`) drawn in a text font, and plain arrows (U+2190-21FF) -- all of
these are everyday punctuation a person edits like any other character, not a picture
glued to a word.

A multi-codepoint emoji (a flag, a skin-toned gesture, a keycap) is one cluster: a
joiner (ZWJ, a variation selector, the combining enclosing keycap mark) immediately
after an icon character stays part of the same cluster. A joiner elsewhere is left
alone -- it never turns ordinary text into an icon by itself. The one real sequence
this doesn't yet cover is a keycap emoji's leading ASCII digit (very rare and not seen
on any document reviewed so far), which is a known, documented gap, not a silent one.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engine.fonts.style import SpanTrace

_EMOJI_RANGES: tuple[tuple[int, int], ...] = (
    (0x1F000, 0x1FFFF),
    (0x2600, 0x27BF),
    (0x2300, 0x23FF),
)
_JOINERS = frozenset({"‍", "︎", "️", "⃣"})
_ICON_FONT_NAME_TOKENS = ("wingding", "webding", "dingbat", "emoji")
_TYPE3_UNKNOWN_PREFIX = "Type3 ("
_UNKNOWN_CHAR = "�"


def is_icon_char(char: str, font: str) -> bool:
    """Whether `char`, drawn in `font` (``SpanStyle.font``), is an icon glyph rather
    than ordinary text."""
    if any(lo <= ord(char) <= hi for lo, hi in _EMOJI_RANGES):
        return True
    if unicodedata.category(char) == "Co":
        return True
    if char == _UNKNOWN_CHAR and font.startswith(_TYPE3_UNKNOWN_PREFIX):
        return True
    lowered = font.lower()
    return any(token in lowered for token in _ICON_FONT_NAME_TOKENS)


def icon_ranges(
    text: str, glyph_map: Sequence[tuple[int, int] | None], spans: Sequence[SpanTrace]
) -> tuple[tuple[int, int], ...]:
    """Maximal runs `[start, end)` of `text` that are one icon cluster, in order.

    `glyph_map` is a ``TextLine``'s own: one entry per character of `text`, the
    `(span_index, char_index)` that drew it, or ``None`` for a synthetic space with no
    real glyph (never an icon). `spans` is the page's full span list, indexed by
    `span_index` -- the same convention ``engine.edit._line_glyphs`` already relies on.
    """
    ranges: list[tuple[int, int]] = []
    start: int | None = None
    for offset, char in enumerate(text):
        entry = glyph_map[offset] if offset < len(glyph_map) else None
        font = spans[entry[0]].style.font if entry is not None else None
        icon = font is not None and is_icon_char(char, font)
        joined = char in _JOINERS and start is not None
        if icon or joined:
            if start is None:
                start = offset
        elif start is not None:
            ranges.append((start, offset))
            start = None
    if start is not None:
        ranges.append((start, len(text)))
    return tuple(ranges)


def icon_members(ranges: Sequence[tuple[int, int]]) -> frozenset[int]:
    """Every character offset `icon_ranges` covers, for an O(1) "is this offset part
    of an icon" check."""
    return frozenset(i for start, end in ranges for i in range(start, end))
