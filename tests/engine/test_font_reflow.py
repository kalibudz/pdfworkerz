"""FNT-11 (reflow half): engine.fonts.reflow.wrap_text."""

from __future__ import annotations

import pymupdf
import pytest

from engine.fonts.reflow import wrap_text


@pytest.fixture
def font() -> pymupdf.Font:
    return pymupdf.Font("helv")


@pytest.mark.feature("FNT-11")
def test_empty_text_wraps_to_one_empty_line(font: pymupdf.Font) -> None:
    assert wrap_text("", font, 12, 200) == [""]


@pytest.mark.feature("FNT-11")
def test_short_text_that_fits_stays_on_one_line(font: pymupdf.Font) -> None:
    assert wrap_text("Hello world", font, 12, 500) == ["Hello world"]


@pytest.mark.feature("FNT-11")
def test_long_text_wraps_across_multiple_lines_at_word_boundaries(font: pymupdf.Font) -> None:
    text = "This is a longer sentence that should wrap across several lines of text"
    max_width = font.text_length("This is a longer", fontsize=12) + 2
    lines = wrap_text(text, font, 12, max_width)

    assert len(lines) > 1
    # every line fits within max_width
    for line in lines:
        assert font.text_length(line, fontsize=12) <= max_width
    # no words were lost or reordered
    assert " ".join(lines).split(" ") == text.split(" ")


@pytest.mark.feature("FNT-11")
def test_a_single_overlong_word_falls_back_to_character_wrapping(font: pymupdf.Font) -> None:
    # with no space to break on, a too-wide "word" is wrapped character-by-character
    # (the same fallback path CJK text without spaces uses) rather than left overflowing
    text = "Supercalifragilisticexpialidocious"
    max_width = font.text_length("Super", fontsize=12) + 1
    lines = wrap_text(text, font, 12, max_width)

    assert len(lines) > 1
    assert "".join(lines) == text
    for line in lines:
        assert font.text_length(line, fontsize=12) <= max_width


@pytest.mark.feature("FNT-11")
def test_a_single_short_word_that_fits_stays_on_one_line(font: pymupdf.Font) -> None:
    assert wrap_text("Hello", font, 12, 200) == ["Hello"]


@pytest.mark.feature("FNT-11")
def test_cjk_text_without_spaces_wraps_character_by_character(font: pymupdf.Font) -> None:
    cjk_font = pymupdf.Font("china-s")
    text = "一二三四五六七八九十"
    max_width = cjk_font.text_length(text[:3], fontsize=12) + 1
    lines = wrap_text(text, cjk_font, 12, max_width)

    assert len(lines) > 1
    assert "".join(lines) == text
    for line in lines:
        assert cjk_font.text_length(line, fontsize=12) <= max_width


@pytest.mark.feature("FNT-11")
def test_wrapping_never_drops_or_duplicates_characters(font: pymupdf.Font) -> None:
    text = "one two three four five six seven eight nine ten"
    max_width = font.text_length("one two three", fontsize=10)
    lines = wrap_text(text, font, 10, max_width)
    assert " ".join(lines) == text
