"""FNT-20: an icon/emoji glyph is its own unit, and an edit never redraws it away.

Found from live use: a real document's headings and table labels each open with an
emoji drawn in its own Type3 font. Word-splitting glued the icon to the word after it
("✅OBJECTIVES"), so bolding the heading, or inserting text next to it, redrew the
icon too -- and a Type3 glyph can't actually be redrawn (FNT-15), so it silently fell
back to a standard font that has no such glyph and came out blank. `corpus.icon_labels`
mirrors the real document's pattern: two Type3 icon fonts (one with a ToUnicode CMap to
a real emoji codepoint, including one outside the BMP; one with none at all, which
MuPDF reports as U+FFFD), plus two ordinary text symbols (c) and a bullet that must
never be treated as icons.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from engine.document import Document
from engine.errors import OpValidationError
from engine.fonts.icons import is_icon_char
from engine.fonts.style import extract_page_spans
from engine.fonts.units import group_lines, split_words, text_units
from engine.ops.objects import DuplicateObjectsOp, MoveObjectsOp, ObjectRef
from engine.ops.text import EditTextUnitOp
from tests.corpus.build_corpus import Corpus


@pytest.fixture
def doc(corpus: Corpus, work_dir: Path) -> Iterator[Document]:
    source = work_dir / "icon_labels.pdf"
    shutil.copy(corpus.icon_labels, source)
    document = Document.open(source)
    yield document
    document.close()


# -- classification (engine.fonts.icons) ------------------------------------------------


@pytest.mark.feature("FNT-20")
@pytest.mark.parametrize(
    ("char", "font", "expected"),
    [
        ("✅", "Helvetica", True),  # a real emoji codepoint, in any font
        ("\U0001f4c6", "Helvetica", True),  # astral-plane emoji (surrogate pair in UTF-16)
        ("�", "Type3 (14 0 R)", True),  # the "unknown glyph" placeholder, in a Type3 font
        ("�", "Helvetica", False),  # the same placeholder in an ordinary font is not an icon
        ("©", "Helvetica", False),  # (c)
        ("®", "Helvetica", False),  # (r)
        ("™", "Helvetica", False),  # (tm)
        ("°", "Helvetica", False),  # degree sign
        ("•", "Helvetica", False),  # bullet
        ("→", "Helvetica", False),  # a plain arrow
        ("A", "Wingdings", True),  # an ordinary-looking code in a dingbat font is an icon
        ("A", "Webdings", True),
        ("A", "ZapfDingbats", True),
        ("A", "Segoe UI Emoji", True),
        ("A", "Helvetica", False),
    ],
)
def test_is_icon_char(char: str, font: str, expected: bool) -> None:
    assert is_icon_char(char, font) is expected


# -- word splitting (engine.fonts.units) -------------------------------------------------


@pytest.mark.feature("FNT-20")
def test_an_icon_glued_to_a_word_splits_into_two_words(doc: Document) -> None:
    spans = extract_page_spans(doc.raw, 0)
    words = text_units(spans, "word")
    texts = [(w.text, w.icon) for w in words]
    assert ("✅", True) in texts
    assert ("OBJECTIVES", False) in texts
    assert "✅OBJECTIVES" not in [w.text for w in words]


@pytest.mark.feature("FNT-20")
def test_an_astral_plane_icon_also_splits_from_its_word(doc: Document) -> None:
    spans = extract_page_spans(doc.raw, 0)
    words = text_units(spans, "word")
    texts = [(w.text, w.icon) for w in words]
    assert ("\U0001f4c6", True) in texts
    assert ("Date:", False) in texts


@pytest.mark.feature("FNT-20")
def test_a_type3_icon_with_no_tounicode_still_splits_as_an_icon(doc: Document) -> None:
    spans = extract_page_spans(doc.raw, 0)
    words = text_units(spans, "word")
    texts = [(w.text, w.icon) for w in words]
    assert ("�", True) in texts
    assert ("NoName", False) in texts


@pytest.mark.feature("FNT-20")
def test_ordinary_text_symbols_are_not_split_out(doc: Document) -> None:
    spans = extract_page_spans(doc.raw, 0)
    words = text_units(spans, "word")
    assert all(not w.icon for w in words if w.text in ("©2026", "•"))
    lines = group_lines(spans)
    copyright_line = next(ln for ln in lines if ln.text.startswith("©"))
    bullet_line = next(ln for ln in lines if ln.text.startswith("•"))
    assert copyright_line.icon_ranges == ()
    assert bullet_line.icon_ranges == ()


@pytest.mark.feature("FNT-20")
def test_every_character_belongs_to_exactly_one_word(doc: Document) -> None:
    spans = extract_page_spans(doc.raw, 0)
    lines = group_lines(spans)
    words = split_words(lines)
    for line in lines:
        covered = sorted(
            offset
            for word in words
            if word.line_index == line.index
            for offset in range(word.line_start, word.line_end)
        )
        expected = [offset for offset in range(len(line.text)) if not line.text[offset].isspace()]
        assert covered == expected


# -- restyle and recolor skip the icon, silently ------------------------------------------


def _line_index(doc: Document, starts_with: str) -> int:
    lines = group_lines(extract_page_spans(doc.raw, 0))
    return next(ln.index for ln in lines if ln.text.startswith(starts_with))


def _line_text(doc: Document, starts_with: str) -> str:
    lines = group_lines(extract_page_spans(doc.raw, 0))
    return next(ln.text for ln in lines if ln.text.startswith(starts_with))


@pytest.mark.feature("FNT-20")
def test_bolding_a_line_leaves_its_icon_in_its_own_font(doc: Document) -> None:
    text = _line_text(doc, "✅")
    index = _line_index(doc, "✅")
    EditTextUnitOp(page_index=0, unit="line", index=index, expect_text=text, bold=True, require_tier="fallback").apply(
        doc
    )

    after = group_lines(extract_page_spans(doc.raw, 0))
    heading = next(ln for ln in after if "OBJECTIVES" in ln.text)
    assert heading.text == text  # wording unchanged
    icon_entry = heading.glyph_map[heading.icon_ranges[0][0]]
    assert icon_entry is not None
    icon_span = extract_page_spans(doc.raw, 0)[icon_entry[0]]
    assert icon_span.style.font.startswith("Type3")  # still its own Type3 glyph, not redrawn


@pytest.mark.feature("FNT-20")
def test_recoloring_a_line_leaves_its_icon_the_original_color(doc: Document) -> None:
    text = _line_text(doc, "✅")
    index = _line_index(doc, "✅")
    icon_before = next(
        s for s in extract_page_spans(doc.raw, 0) if s.style.font.startswith("Type3") and "✅" in s.style.text
    )
    original_color = icon_before.style.color

    EditTextUnitOp(page_index=0, unit="line", index=index, expect_text=text, color=(1.0, 0.0, 0.0)).apply(doc)

    after_spans = extract_page_spans(doc.raw, 0)
    icon_after = next(s for s in after_spans if s.style.font.startswith("Type3") and "✅" in s.style.text)
    assert icon_after.style.color == original_color
    red_text = next(s for s in after_spans if s.style.color == (1.0, 0.0, 0.0))
    assert "OBJECTIVES" in red_text.style.text


@pytest.mark.feature("FNT-20")
def test_recoloring_an_icon_word_alone_is_refused(doc: Document) -> None:
    words = text_units(extract_page_spans(doc.raw, 0), "word")
    icon_word = next(w for w in words if w.icon and w.text == "✅")
    with pytest.raises(OpValidationError, match="icon glyphs"):
        EditTextUnitOp(
            page_index=0, unit="word", index=icon_word.index, expect_text=icon_word.text, color=(0.0, 1.0, 0.0)
        ).apply(doc)


# -- text edits that would redraw the icon are refused, nothing changed -------------------


@pytest.mark.feature("FNT-20")
def test_retyping_the_icon_itself_is_refused(doc: Document) -> None:
    text = _line_text(doc, "✅")
    index = _line_index(doc, "✅")
    with pytest.raises(OpValidationError, match="redraw the icon"):
        EditTextUnitOp(
            page_index=0,
            unit="line",
            index=index,
            expect_text=text,
            new_text=text.replace("✅", "✔"),
        ).apply(doc)
    assert _line_text(doc, "✅") == text  # nothing changed


@pytest.mark.feature("FNT-20")
def test_inserting_text_right_after_an_icon_takes_the_followers_style_not_the_icons(doc: Document) -> None:
    text = _line_text(doc, "✅")
    index = _line_index(doc, "✅")
    EditTextUnitOp(page_index=0, unit="line", index=index, bold=True, expect_text=text).apply(doc)

    bold_text = _line_text(doc, "✅")
    index2 = _line_index(doc, "✅")
    EditTextUnitOp(
        page_index=0,
        unit="line",
        index=index2,
        expect_text=bold_text,
        new_text=bold_text[:1] + "ALL " + bold_text[1:],
    ).apply(doc)

    after = extract_page_spans(doc.raw, 0)
    inserted = next(s for s in after if "ALL" in s.style.text)
    assert "Bold" in inserted.style.font  # the heading's own (now bold) font, not the icon's Type3


@pytest.mark.feature("FNT-20")
def test_emptying_an_icon_containing_line_via_delete_objects_still_works(doc: Document) -> None:
    from engine.ops.objects import DeleteObjectsOp

    words = text_units(extract_page_spans(doc.raw, 0), "word")
    icon_word = next(w for w in words if w.icon and w.text == "✅")
    DeleteObjectsOp(
        items=[ObjectRef(kind="text", page_index=0, index=icon_word.index, unit="word", expect_text="✅")]
    ).apply(doc)
    lines = group_lines(extract_page_spans(doc.raw, 0))
    assert not any(ln.icon_ranges and "✅" in "".join(ln.text[a:b] for a, b in ln.icon_ranges) for ln in lines)
    assert any("OBJECTIVES" in ln.text for ln in lines)  # the rest of the line survives


# -- moving and duplicating an icon is refused --------------------------------------------


@pytest.mark.feature("FNT-20")
def test_moving_just_the_icon_word_is_refused(doc: Document) -> None:
    words = text_units(extract_page_spans(doc.raw, 0), "word")
    icon_word = next(w for w in words if w.icon and w.text == "✅")
    with pytest.raises(OpValidationError, match="can't be moved or copied"):
        MoveObjectsOp(
            items=[ObjectRef(kind="text", page_index=0, index=icon_word.index, unit="word", expect_text="✅")],
            dx=20.0,
            dy=0.0,
        ).apply(doc)


@pytest.mark.feature("FNT-20")
def test_duplicating_just_the_icon_word_is_refused(doc: Document) -> None:
    words = text_units(extract_page_spans(doc.raw, 0), "word")
    icon_word = next(w for w in words if w.icon and w.text == "✅")
    with pytest.raises(OpValidationError, match="can't be moved or copied"):
        DuplicateObjectsOp(
            items=[ObjectRef(kind="text", page_index=0, index=icon_word.index, unit="word", expect_text="✅")],
            dx=0.0,
            dy=20.0,
        ).apply(doc)


# -- a block re-wrap containing an icon is refused -----------------------------------------


@pytest.mark.feature("FNT-20")
def test_re_wrapping_a_block_with_an_icon_is_refused(doc: Document) -> None:
    blocks = text_units(extract_page_spans(doc.raw, 0), "block")
    block = next(b for b in blocks if "OBJECTIVES" in b.text)
    before = _line_text(doc, "✅")
    with pytest.raises(OpValidationError, match="re-wrap can't keep"):
        EditTextUnitOp(
            page_index=0,
            unit="block",
            index=block.index,
            expect_text=block.text,
            new_text="Something else entirely, much longer than before",
        ).apply(doc)
    assert _line_text(doc, "✅") == before


# -- the real document's exact shape: insert + bold together, as the owner tried -----------


@pytest.mark.feature("FNT-20")
def test_the_owners_exact_sequence_keeps_the_icon_throughout(doc: Document) -> None:
    """Bold the heading, then insert "ALL " after the icon -- the two steps from the
    live bug report, chained -- and the icon must still be there at the end."""
    text = _line_text(doc, "✅")
    index = _line_index(doc, "✅")
    EditTextUnitOp(page_index=0, unit="line", index=index, bold=True, expect_text=text).apply(doc)

    bold_text = _line_text(doc, "✅")
    index2 = _line_index(doc, "✅")
    EditTextUnitOp(
        page_index=0, unit="line", index=index2, expect_text=bold_text, new_text=bold_text[:1] + "ALL " + bold_text[1:]
    ).apply(doc)

    final = next(ln for ln in group_lines(extract_page_spans(doc.raw, 0)) if "OBJECTIVES" in ln.text)
    assert final.text.startswith("✅ALL")
    assert final.icon_ranges == ((0, 1),)
