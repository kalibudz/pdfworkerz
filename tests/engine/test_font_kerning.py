"""FNT-09: kerning-pair adjustments."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from engine.edit import draw_styled_text
from engine.fonts.kerning import build_kern_pairs
from engine.fonts.resolve import FontResolution
from engine.fonts.style import TextState

VERA_PATH = Path(__file__).resolve().parents[2] / "assets" / "fonts" / "Vera.ttf"


@pytest.fixture(scope="module")
def vera_bytes() -> bytes:
    return VERA_PATH.read_bytes()


@pytest.mark.feature("FNT-09")
def test_pymupdf_applies_no_kerning_at_all(vera_bytes: bytes) -> None:
    """Confirms the premise this feature works around: without our own
    adjustment, "AV" measures exactly the naive sum of its two advances,
    whether inserted in one call or measured directly -- so kerning support
    has to come from reading the font's own table, not from PyMuPDF."""
    font = pymupdf.Font(fontbuffer=vera_bytes)
    naive_sum = sum(font.char_lengths("AV", fontsize=24))
    assert font.text_length("AV", fontsize=24) == pytest.approx(naive_sum)


@pytest.mark.feature("FNT-09")
def test_build_kern_pairs_reads_a_known_pair(vera_bytes: bytes) -> None:
    pairs = build_kern_pairs(vera_bytes)
    assert ("A", "V") in pairs
    assert pairs[("A", "V")] < 0  # A and V tuck closer together, a very standard pair


@pytest.mark.feature("FNT-09")
def test_build_kern_pairs_value_matches_the_font_file_directly(vera_bytes: bytes) -> None:
    from fontTools.ttLib import TTFont

    tt = TTFont(VERA_PATH)
    raw = tt["kern"].kernTables[0].kernTable[("A", "V")]
    units_per_em = tt["head"].unitsPerEm

    pairs = build_kern_pairs(vera_bytes)
    assert pairs[("A", "V")] == pytest.approx(raw / units_per_em)


@pytest.mark.feature("FNT-09")
def test_build_kern_pairs_empty_for_unparseable_bytes() -> None:
    assert build_kern_pairs(b"not a font") == {}


@pytest.mark.feature("FNT-09")
def test_build_kern_pairs_is_sparse_not_exhaustive(vera_bytes: bytes) -> None:
    """Kerning is defined for specific pairs, not every combination of glyphs
    the font has -- a genuinely unusual pair like digit-then-punctuation
    should have no entry at all."""
    pairs = build_kern_pairs(vera_bytes)
    assert ("0", "@") not in pairs


@pytest.mark.feature("FNT-09")
def test_draw_styled_text_applies_kerning_when_using_the_per_character_path(vera_bytes: bytes) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    resolution = FontResolution(
        tier="exact", confidence=1.0, fontname=None, font_bytes=vera_bytes, requires_approval=False, note="test"
    )
    # A nonzero rise forces the per-character drawing path (see draw_styled_text).
    text_state = TextState(rise=0.0001)

    end_point = draw_styled_text(
        page,
        text="AV",
        origin=(72, 700),
        font_size=24,
        color=(0, 0, 0),
        text_state=text_state,
        rotation_degrees=0.0,
        resolution=resolution,
    )

    font = pymupdf.Font(fontbuffer=vera_bytes)
    naive_end_x = 72 + font.text_length("AV", fontsize=24)
    expected_kern = (-131 / 2048) * 24  # the real ("A","V") pair, verified against the font file directly
    assert end_point[0] == pytest.approx(naive_end_x + expected_kern)
    doc.close()


@pytest.mark.feature("FNT-09")
def test_draw_styled_text_kerning_is_skipped_for_a_pair_with_no_entry(vera_bytes: bytes) -> None:
    """A pair the font doesn't kern must draw at exactly the naive width, not
    some default/guessed adjustment."""
    doc = pymupdf.open()
    page = doc.new_page()
    resolution = FontResolution(
        tier="exact", confidence=1.0, fontname=None, font_bytes=vera_bytes, requires_approval=False, note="test"
    )
    text_state = TextState(rise=0.0001)

    end_point = draw_styled_text(
        page,
        text="mn",  # an ordinary lowercase pair, unlikely to be in the kern table
        origin=(72, 700),
        font_size=24,
        color=(0, 0, 0),
        text_state=text_state,
        rotation_degrees=0.0,
        resolution=resolution,
    )

    font = pymupdf.Font(fontbuffer=vera_bytes)
    naive_end_x = 72 + font.text_length("mn", fontsize=24)
    assert end_point[0] == pytest.approx(naive_end_x)
    doc.close()


@pytest.mark.feature("FNT-09")
def test_draw_styled_text_standard_font_has_no_kerning_data_but_still_draws(work_dir: Path) -> None:
    """A standard (non-embedded) font has no font_bytes to read a kern table
    from; drawing must still work, simply without kerning."""
    doc = pymupdf.open()
    page = doc.new_page()
    resolution = FontResolution(
        tier="exact", confidence=1.0, fontname="helv", font_bytes=None, requires_approval=False, note="test"
    )
    text_state = TextState(rise=0.0001)

    end_point = draw_styled_text(
        page,
        text="AV",
        origin=(72, 700),
        font_size=24,
        color=(0, 0, 0),
        text_state=text_state,
        rotation_degrees=0.0,
        resolution=resolution,
    )
    assert end_point[0] > 72
    doc.close()
