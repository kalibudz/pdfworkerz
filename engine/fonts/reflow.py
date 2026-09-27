"""FNT-11 (reflow half): re-wrap replacement text across a block's own lines.

Paired with engine.fonts.blocks (block detection). Deliberately bounded to
stay safe: a reflowed block never grows past the number of lines it
already had, so a reflow can never push unrelated content below it down
the page -- SPEC.md section 5.4 describes shifting later content, which
this does not attempt; text that needs more lines than the block has is
reported as an overflow instead (the same "stop and show the overflow"
outcome SPEC.md specifies when nothing else fits).
"""

from __future__ import annotations

import pymupdf


def wrap_text(text: str, font: pymupdf.Font, font_size: float, max_width: float) -> list[str]:
    """Greedily wrap `text` into lines that each fit within `max_width` at
    `font_size`. Falls back to character-by-character wrapping when the text
    has no word-break spaces to begin with (CJK text, which doesn't rely on
    spaces between words -- confirmed working end to end in
    tests/engine/test_font_reflow.py).
    """
    if not text:
        return [""]

    words = text.split(" ")
    word_wrapping = len(words) > 1
    units = words if word_wrapping else list(text)
    joiner = " " if word_wrapping else ""

    lines: list[str] = []
    current = ""
    for unit in units:
        candidate = f"{current}{joiner}{unit}" if current else unit
        if not current or font.text_length(candidate, fontsize=font_size) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = unit
    if current:
        lines.append(current)
    return lines
