"""EDT-17: editing and restyling a single word, line or block in place (edit_text_unit).

Each page is built by the test. The point of every check is fidelity (SPEC.md 8.2 item 6):
only the glyphs of the unit being changed are redrawn, and the rest of its line moves only
when the unit's width changed.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pikepdf
import pymupdf
import pytest
from numpy.typing import NDArray

from engine.document import Document
from engine.edit import VERIFY_DPI, EditResult
from engine.errors import OpValidationError
from engine.fonts.style import CharBox, extract_page_spans
from engine.fonts.units import TextUnit, text_units
from engine.ops.base import parse_op
from engine.ops.journal import UndoRedoJournal
from engine.verify import changed_outside, pixel_diff, render_to_array
from tests.corpus.build_corpus import Corpus

RIGHT_EDGE = 540.0
PAGE_WIDTH = 595.0
_HELV = pymupdf.Font("helv")
_MARGIN_PX = 2


def _width(text: str, size: float = 12) -> float:
    return float(_HELV.text_length(text, fontsize=size))


def _journal() -> UndoRedoJournal:
    """A three-line paragraph, a line in two styles, two right-aligned lines and a centered one."""
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_WIDTH, height=842)
    for i, text in enumerate(["Alpha Bravo Charlie", "Delta Echo Foxtrot", "Golf Hotel India"]):
        page.insert_text((72, 100 + 16 * i), text, fontname="helv", fontsize=12)
    x = 72.0
    for text, font in (("Hello ", "helv"), ("World", "hebo"), (" again", "helv")):
        page.insert_text((x, 200), text, fontname=font, fontsize=12)
        x += float(pymupdf.Font(font).text_length(text, fontsize=12))
    for i, text in enumerate(["Total due today", "Paid in full"]):
        page.insert_text((RIGHT_EDGE - _width(text), 300 + 16 * i), text, fontname="helv", fontsize=12)
    title = "Quarterly Summary Report"
    page.insert_text((PAGE_WIDTH / 2 - _width(title) / 2, 400), title, fontname="helv", fontsize=12)
    return UndoRedoJournal(Document.from_bytes(doc.tobytes()))


def _raw_journal(stream: str) -> UndoRedoJournal:
    """A page whose content stream is written by hand (PDF space: y up), for text placed by
    TJ offsets or word spacing, which insert_text never produces."""
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_WIDTH, height=842)
    page.insert_text((72, 700), "anchor", fontname="helv", fontsize=12)
    doc.update_stream(page.get_contents()[0], stream.encode())
    return UndoRedoJournal(Document.from_bytes(doc.tobytes()))


def _unit(journal: UndoRedoJournal, granularity: str, text: str, page: int = 0) -> TextUnit:
    units = text_units(extract_page_spans(journal.document.raw, page), granularity)  # type: ignore[arg-type]
    return next(unit for unit in units if unit.text == text)


def _edit(journal: UndoRedoJournal, granularity: str, text: str, **fields: object) -> EditResult:
    unit = _unit(journal, granularity, text)
    op = {"op": "edit_text_unit", "page_index": 0, "unit": granularity, "index": unit.index, "expect_text": text}
    result = journal.record(parse_op({**op, **fields}))
    assert isinstance(result, EditResult)
    return result


def _render(journal: UndoRedoJournal, page: int = 0) -> NDArray[np.uint8]:
    return render_to_array(journal.document.raw, page, dpi=VERIFY_DPI)


def _pixels(box: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
    scale = VERIFY_DPI / 72
    return (
        math.floor(box[0] * scale) - _MARGIN_PX,
        math.floor(box[1] * scale) - _MARGIN_PX,
        math.ceil(box[2] * scale) + _MARGIN_PX,
        math.ceil(box[3] * scale) + _MARGIN_PX,
    )


def _glyphs(journal: UndoRedoJournal, page: int = 0) -> list[CharBox]:
    return [char for span in extract_page_spans(journal.document.raw, page) for char in span.style.chars]


def _line_glyphs(journal: UndoRedoJournal, baseline: float) -> list[CharBox]:
    """The visible glyphs on one baseline, left to right."""
    on_line = [g for g in _glyphs(journal) if abs(g.origin[1] - baseline) < 0.5 and not g.char.isspace()]
    return sorted(on_line, key=lambda g: g.origin[0])


def _xs(glyphs: list[CharBox]) -> list[float]:
    return [g.origin[0] for g in glyphs]


def _looks_right(result: EditResult) -> None:
    assert result.verification is not None
    assert result.verification.looks_right, result.verification


@pytest.mark.feature("EDT-17", criterion=1)
def test_a_same_width_word_edit_leaves_every_other_glyph_pixel_identical() -> None:
    journal = _journal()
    word = _unit(journal, "word", "Bravo")
    before = _render(journal)

    result = _edit(journal, "word", "Bravo", new_text="Brave")  # "o" and "e" are equally wide in Helvetica

    _looks_right(result)
    assert result.tier == "exact"
    after = _render(journal)
    assert pixel_diff(before, after).changed_fraction > 0
    assert changed_outside(before, after, [_pixels(word.bbox)]) == 0.0
    assert _unit(journal, "line", "Alpha Brave Charlie")
    assert len(journal.history) == 1
    journal.undo()
    assert pixel_diff(before, _render(journal)).matches


@pytest.mark.feature("EDT-17", criterion=1)
def test_a_wider_word_shifts_only_the_rest_of_its_line() -> None:
    journal = _journal()
    line = _unit(journal, "line", "Alpha Bravo Charlie")
    before = _render(journal)
    old = _line_glyphs(journal, 100)  # AlphaBravoCharlie
    delta = _width("Bravissimo") - _width("Bravo")

    result = _edit(journal, "word", "Bravo", new_text="Bravissimo")

    _looks_right(result)
    new = _line_glyphs(journal, 100)  # AlphaBravissimoCharlie
    assert "".join(g.char for g in new) == "AlphaBravissimoCharlie"
    assert _xs(new[:5]) == pytest.approx(_xs(old[:5]), abs=0.01)  # "Alpha" did not move
    assert _xs(new[-7:]) == pytest.approx([x + delta for x in _xs(old[-7:])], abs=0.3)  # "Charlie" slid
    # Nothing outside that line changed: its band, widened to the right for the longer text.
    band = (line.bbox[0], line.bbox[1], line.bbox[2] + delta + 2, line.bbox[3])
    assert changed_outside(before, _render(journal), [_pixels(band)]) == 0.0
    assert _unit(journal, "line", "Alpha Bravissimo Charlie")


@pytest.mark.feature("EDT-17", criterion=1)
def test_a_narrower_word_on_a_right_aligned_line_keeps_the_right_edge() -> None:
    journal = _journal()
    old = _line_glyphs(journal, 300)  # Totalduetoday
    delta = _width("now") - _width("due")

    result = _edit(journal, "word", "due", new_text="now")

    _looks_right(result)
    assert "right alignment" in result.note
    new = _line_glyphs(journal, 300)
    assert "".join(g.char for g in new) == "Totalnowtoday"
    assert _xs(new[-5:]) == pytest.approx(_xs(old[-5:]), abs=0.01)  # "today" still ends at the margin
    assert _xs(new[:5]) == pytest.approx([x - delta for x in _xs(old[:5])], abs=0.3)  # "Total" moved instead


@pytest.mark.feature("EDT-17", criterion=1)
def test_a_word_edit_on_a_tj_kerned_line_keeps_the_kerning_of_what_slides() -> None:
    journal = _raw_journal(
        "BT /helv 12 Tf 72 700 Td [(A) 60 (lpha ) -300 (B) 40 (ravo ) -250 (Ch) 30 (arlie)] TJ ET "
        "BT /helv 12 Tf 72 670 Td (Second line here) Tj ET"
    )
    baseline = _unit(journal, "line", "Alpha Bravo Charlie").origin[1]
    old = _line_glyphs(journal, baseline)

    result = _edit(journal, "word", "Bravo", new_text="Bravissimo")

    _looks_right(result)
    new = _line_glyphs(journal, baseline)
    assert _xs(new[:5]) == pytest.approx(_xs(old[:5]), abs=0.01)
    shifts = [after - before for before, after in zip(_xs(old[-7:]), _xs(new[-7:]), strict=True)]
    assert shifts[0] > 20
    assert shifts == pytest.approx([shifts[0]] * 7, abs=0.3)  # "Charlie" slid as one, its TJ offsets intact


@pytest.mark.feature("EDT-17", criterion=2)
def test_recoloring_a_word_leaves_the_glyphs_on_either_side_unmoved() -> None:
    journal = _journal()
    word = _unit(journal, "word", "Bravo")
    before = _render(journal)
    old = _line_glyphs(journal, 100)

    result = _edit(journal, "word", "Bravo", color=(1.0, 0.0, 0.0))

    _looks_right(result)
    assert changed_outside(before, _render(journal), [_pixels(word.bbox)]) == 0.0
    assert _xs(_line_glyphs(journal, 100)) == pytest.approx(_xs(old), abs=0.3)
    red = [s.style.text for s in extract_page_spans(journal.document.raw, 0) if s.style.color == (1.0, 0.0, 0.0)]
    assert red == ["Bravo"]
    assert _unit(journal, "line", "Alpha Bravo Charlie")  # still one line, reading the same


@pytest.mark.feature("EDT-17", criterion=2)
def test_resizing_a_word_leaves_what_precedes_it_and_every_other_line_alone() -> None:
    journal = _journal()
    before = _render(journal)
    old = _line_glyphs(journal, 100)
    others = [_unit(journal, "line", text).bbox for text in ("Delta Echo Foxtrot", "Golf Hotel India")]

    result = _edit(journal, "word", "Bravo", size=15)

    _looks_right(result)
    new = _line_glyphs(journal, 100)
    assert _xs(new[:5]) == pytest.approx(_xs(old[:5]), abs=0.01)
    grown = _width("Bravo", 15) - _width("Bravo", 12)
    assert _xs(new[-7:]) == pytest.approx([x + grown for x in _xs(old[-7:])], abs=0.3)
    after = _render(journal)
    top = _unit(journal, "line", "Alpha Bravo Charlie").bbox
    band = (top[0], top[1] - 6, top[2] + 2, top[3])
    assert changed_outside(before, after, [_pixels(band)]) == 0.0
    assert [_unit(journal, "line", text).bbox for text in ("Delta Echo Foxtrot", "Golf Hotel India")] == others


@pytest.mark.feature("EDT-17", criterion=3)
def test_editing_a_line_keeps_the_styles_of_the_runs_that_did_not_change() -> None:
    journal = _journal()
    line = _unit(journal, "line", "Hello World again")
    hello = _unit(journal, "word", "Hello")
    world = _unit(journal, "word", "World")
    before = _render(journal)

    result = _edit(journal, "line", "Hello World again", new_text="Hello World again soon")

    _looks_right(result)
    assert result.tier == "exact"
    after = _render(journal)
    # "Hello" and the bold "World" were not redrawn at all.
    assert not np.any(_crop(before, hello.bbox) != _crop(after, hello.bbox))
    assert not np.any(_crop(before, world.bbox) != _crop(after, world.bbox))
    fonts = {s.style.text: s.style.font for s in extract_page_spans(journal.document.raw, 0)}
    assert "Bold" in fonts["World"]
    assert "Bold" not in fonts["Hello "]
    edited = _unit(journal, "line", "Hello World again soon")
    assert edited.origin == pytest.approx(line.origin, abs=0.01)


@pytest.mark.feature("EDT-17", criterion=3)
def test_a_change_inside_the_bold_run_is_drawn_bold_and_the_rest_keeps_its_style() -> None:
    journal = _journal()
    hello = _unit(journal, "word", "Hello")
    before = _render(journal)

    result = _edit(journal, "line", "Hello World again", new_text="Hello Whirl again")

    _looks_right(result)
    after = _render(journal)
    assert not np.any(_crop(before, hello.bbox) != _crop(after, hello.bbox))
    spans = extract_page_spans(journal.document.raw, 0)
    changed = [s for s in spans if "hirl" in s.style.text or s.style.text == "hir"]
    assert changed and all("Bold" in s.style.font for s in changed)
    again = next(s for s in spans if "again" in s.style.text)
    assert "Bold" not in again.style.font
    assert _unit(journal, "line", "Hello Whirl again")


def _crop(image: NDArray[np.uint8], box: tuple[float, float, float, float]) -> NDArray[np.uint8]:
    scale = VERIFY_DPI / 72
    x0, y0, x1, y1 = (round(v * scale) for v in box)
    return image[y0:y1, x0 + 1 : x1 - 1]


@pytest.mark.feature("EDT-17", criterion=4)
def test_editing_a_block_reflows_it_by_index() -> None:
    journal = _journal()
    block = _unit(journal, "block", "Alpha Bravo Charlie Delta Echo Foxtrot Golf Hotel India")
    op = {
        "op": "edit_text_unit",
        "page_index": 0,
        "unit": "block",
        "index": block.index,
        "expect_text": block.text,
        "new_text": "One two three four five six seven eight",
    }

    results = journal.record(parse_op(op))

    assert isinstance(results, list) and len(results) == 3
    assert all(r.tier == "exact" for r in results)
    assert all(r.verification is not None and r.verification.looks_right for r in results if r.verification)
    assert len(journal.history) == 1
    reflowed = _unit(journal, "block", "One two three four five six seven eight")
    assert reflowed.origin == pytest.approx(block.origin, abs=0.01)
    # The rest of the page is as it was.
    assert _unit(journal, "line", "Hello World again")
    assert _unit(journal, "line", "Total due today")


@pytest.mark.feature("EDT-17", criterion=4)
def test_restyling_a_block_recolors_and_resizes_every_line() -> None:
    journal = _journal()
    block = _unit(journal, "block", "Alpha Bravo Charlie Delta Echo Foxtrot Golf Hotel India")
    op = {
        "op": "edit_text_unit",
        "page_index": 0,
        "unit": "block",
        "index": block.index,
        "expect_text": block.text,
        "color": (0.0, 0.0, 1.0),
        "size": 11,
    }

    results = journal.record(parse_op(op))

    assert isinstance(results, list) and len(results) == 3
    blue = [s for s in extract_page_spans(journal.document.raw, 0) if s.style.color == (0.0, 0.0, 1.0)]
    assert [s.style.text for s in blue] == ["Alpha Bravo Charlie", "Delta Echo Foxtrot", "Golf Hotel India"]
    assert all(s.style.size == pytest.approx(11) for s in blue)
    journal.undo()
    assert _unit(journal, "block", block.text)


@pytest.mark.feature("EDT-17", criterion=4)
def test_restyling_a_block_bold_does_not_falsely_report_overflow() -> None:
    """Found from live use: a single-row block restyled bold+italic (text unchanged) was
    refused with "overflow: 1 more line(s) needed than this block has", because the restyle
    went through reflow_block's re-wrap, and a bolder, wider font needed more lines at the
    block's own width than the original text did -- even though no wording changed and
    nothing needed to move to a new line. A restyle-only edit must never re-wrap."""
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_WIDTH, height=842)
    page.insert_text((72, 100), "Khaliel Howell", fontname="helv", fontsize=10)
    journal = UndoRedoJournal(Document.from_bytes(doc.tobytes()))
    block = _unit(journal, "block", "Khaliel Howell")
    op = {
        "op": "edit_text_unit",
        "page_index": 0,
        "unit": "block",
        "index": block.index,
        "expect_text": block.text,
        "bold": True,
        "italic": True,
        "require_tier": "exact",
    }

    result = journal.record(parse_op(op))

    assert isinstance(result, list) and len(result) == 1
    assert "overflow" not in result[0].note
    assert result[0].tier == "exact"
    after = extract_page_spans(journal.document.raw, 0)
    assert after[0].style.text == "Khaliel Howell"
    assert after[0].style.font == "Helvetica-BoldOblique"
    assert _unit(journal, "block", "Khaliel Howell").origin == pytest.approx(block.origin, abs=0.01)


@pytest.mark.feature("EDT-17", criterion=4)
def test_restyling_a_multiline_block_bold_keeps_its_own_line_count() -> None:
    """The same false-overflow bug, on the fixture's 3-line paragraph: bold is wide enough
    that re-wrapping it (the old, buggy path) would need a 4th line, which the block
    doesn't have."""
    journal = _journal()
    block = _unit(journal, "block", "Alpha Bravo Charlie Delta Echo Foxtrot Golf Hotel India")
    op = {
        "op": "edit_text_unit",
        "page_index": 0,
        "unit": "block",
        "index": block.index,
        "expect_text": block.text,
        "bold": True,
        "require_tier": "exact",
    }

    results = journal.record(parse_op(op))

    assert isinstance(results, list) and len(results) == 3
    assert all("overflow" not in r.note for r in results)
    bold_texts = {s.style.text for s in extract_page_spans(journal.document.raw, 0) if s.style.font == "Helvetica-Bold"}
    assert bold_texts >= {"Alpha Bravo Charlie", "Delta Echo Foxtrot", "Golf Hotel India"}
    journal.undo()
    assert _unit(journal, "block", block.text)


@pytest.mark.feature("EDT-17", criterion=4)
def test_a_block_edit_that_does_not_fit_is_refused_unless_overflow_is_allowed() -> None:
    journal = _journal()
    block = _unit(journal, "block", "Alpha Bravo Charlie Delta Echo Foxtrot Golf Hotel India")
    op = {
        "op": "edit_text_unit",
        "page_index": 0,
        "unit": "block",
        "index": block.index,
        "new_text": " ".join(["word"] * 40),
    }
    before = _render(journal)
    with pytest.raises(OpValidationError, match="overflow"):
        journal.record(parse_op(op))
    assert pixel_diff(before, _render(journal)).matches
    journal.record(parse_op({**op, "grow": True}))
    assert len(journal.history) == 1


@pytest.mark.feature("EDT-17", criterion=1)
def test_a_stale_or_empty_unit_edit_is_refused_and_changes_nothing() -> None:
    journal = _journal()
    word = _unit(journal, "word", "Bravo")
    before = _render(journal)
    base = {"op": "edit_text_unit", "page_index": 0, "unit": "word", "index": word.index}

    with pytest.raises(OpValidationError, match="is now 'Bravo'"):
        journal.record(parse_op({**base, "expect_text": "Charlie", "new_text": "X"}))
    with pytest.raises(OpValidationError, match="delete"):
        journal.record(parse_op({**base, "expect_text": "Bravo", "new_text": ""}))
    with pytest.raises(OpValidationError, match="change the text"):
        journal.record(parse_op({**base, "expect_text": "Bravo", "new_text": "Bravo"}))
    with pytest.raises(OpValidationError, match="out of range"):
        journal.record(parse_op({**base, "index": 999, "expect_text": "Bravo", "new_text": "X"}))
    with pytest.raises(OpValidationError, match='only for unit "block"'):
        journal.record(parse_op({**base, "expect_text": "Bravo", "new_text": "X", "grow": True}))
    assert pixel_diff(before, _render(journal)).matches
    assert not journal.history


@pytest.mark.feature("EDT-17", criterion=1)
def test_a_weaker_font_tier_than_required_is_refused_like_edit_span(corpus: Corpus, work_dir: Path) -> None:
    """The web approval flow tries require_tier "exact" first and reads the refusal. An
    embedded subset under a name nothing matches can't draw new letters itself."""
    path = work_dir / "unmatchable.pdf"
    with pikepdf.open(corpus.embedded_font_subset) as pdf:
        font = pdf.pages[0].Resources.Font["/EmbeddedVeraBold"]
        font["/BaseFont"] = pikepdf.Name("/XYZUNK+TotallyUnknownFontXYZ")
        font["/DescendantFonts"][0]["/BaseFont"] = pikepdf.Name("/XYZUNK+TotallyUnknownFontXYZ")
        pdf.save(path)
    journal = UndoRedoJournal(Document.open(path))
    word = text_units(extract_page_spans(journal.document.raw, 0), "word")[0]
    op = {
        "op": "edit_text_unit",
        "page_index": 0,
        "unit": "word",
        "index": word.index,
        "expect_text": word.text,
        "new_text": "world",
    }
    with pytest.raises(OpValidationError, match=r"fell back to tier '\w+'"):
        journal.record(parse_op({**op, "require_tier": "exact"}))
    assert not journal.history
    result = journal.record(parse_op({**op, "require_tier": "fallback"}))
    assert isinstance(result, EditResult) and result.tier != "exact"
    journal.document.close()


def _plain_lines(journal: UndoRedoJournal) -> list[str]:
    """What any viewer's copy-and-paste or text search sees: the page's content, in the
    order it was drawn, after a save and reload."""
    reloaded = pymupdf.open("pdf", journal.document.to_bytes())
    lines = [line.strip() for line in reloaded[0].get_text().splitlines()]
    reloaded.close()
    return lines


@pytest.mark.feature("EDT-17", criterion=1)
def test_a_width_changing_word_edit_still_reads_in_order_in_plain_extraction() -> None:
    """The edited word and the tail that slid are appended to the page's content; drawn on
    their own they read as "Alpha", two other lines, then "Bravissimo Charlie"."""
    journal = _journal()
    before = _render(journal)
    line = _unit(journal, "line", "Alpha Bravo Charlie")
    delta = _width("Bravissimo") - _width("Bravo")

    _looks_right(_edit(journal, "word", "Bravo", new_text="Bravissimo"))

    assert "Alpha Bravissimo Charlie" in _plain_lines(journal)
    assert "Delta Echo Foxtrot" in _plain_lines(journal)
    # Reading order cost nothing in fidelity: "Alpha" was drawn again on the very same pixels.
    alpha = _unit(journal, "word", "Alpha")
    after = _render(journal)
    assert not np.any(_crop(before, alpha.bbox) != _crop(after, alpha.bbox))
    band = (line.bbox[0], line.bbox[1], line.bbox[2] + delta + 2, line.bbox[3])
    assert changed_outside(before, after, [_pixels(band)]) == 0.0


@pytest.mark.feature("EDT-17", criterion=1)
def test_a_same_width_word_edit_still_reads_in_order_in_plain_extraction() -> None:
    journal = _journal()
    _looks_right(_edit(journal, "word", "Bravo", new_text="Brave"))
    assert "Alpha Brave Charlie" in _plain_lines(journal)


@pytest.mark.feature("EDT-17", criterion=3)
def test_a_line_edit_still_reads_in_order_in_plain_extraction() -> None:
    journal = _journal()
    _looks_right(_edit(journal, "line", "Hello World again", new_text="Hello, Earth again."))
    assert "Hello, Earth again." in _plain_lines(journal)
    _looks_right(_edit(journal, "line", "Total due today", new_text="Total owed today"))
    assert "Total owed today" in _plain_lines(journal)


@pytest.mark.feature("EDT-17", criterion=2)
def test_a_recolored_word_still_reads_in_order_in_plain_extraction() -> None:
    journal = _journal()
    _looks_right(_edit(journal, "word", "Echo", color=(0.0, 0.5, 0.0)))
    assert "Delta Echo Foxtrot" in _plain_lines(journal)


def _spans_on(journal: UndoRedoJournal, baseline: float) -> list[tuple[str, str, float]]:
    """(text, font, size) of every span on one baseline, left to right."""
    spans = [s for s in extract_page_spans(journal.document.raw, 0) if abs(s.style.chars[0].origin[1] - baseline) < 6]
    spans.sort(key=lambda s: s.style.chars[0].origin[0])
    return [(s.style.text, s.style.font, round(s.style.size, 2)) for s in spans]


@pytest.mark.feature("EDT-17", criterion=3)
def test_an_edit_in_two_places_keeps_the_bold_run_between_them() -> None:
    """Review F1: only the two changed letters are redrawn, each in its own run's style."""
    journal = _journal()

    result = _edit(journal, "line", "Hello World again", new_text="Jello World agaiN")

    _looks_right(result)
    assert not result.requires_approval
    fonts = {text: font for text, font, _size in _spans_on(journal, 200)}
    assert fonts["World"] == "Helvetica-Bold"
    assert all(font == "Helvetica" for text, font in fonts.items() if text != "World")
    assert "Jello World agaiN" in _plain_lines(journal)


@pytest.mark.feature("EDT-17", criterion=3)
def test_new_text_replacing_two_differently_styled_runs_is_flagged_not_silent() -> None:
    journal = _journal()

    result = _edit(journal, "line", "Hello World again", new_text="Hell-orld again")

    assert result.requires_approval
    assert "differently styled runs" in result.note
    assert "Hell-orld again" in _plain_lines(journal)


@pytest.mark.feature("EDT-17", criterion=3)
def test_appending_to_a_bold_word_stays_bold_and_removing_a_first_letter_works() -> None:
    """Review F3/F4: an insertion takes the style of the character it follows, and an edit
    that only removes leading characters is not refused as "empty"."""
    journal = _journal()
    _looks_right(_edit(journal, "word", "World", new_text="Worlds"))
    spans = _spans_on(journal, 200)
    assert any(font == "Helvetica-Bold" and text.endswith("s") for text, font, _size in spans), spans
    assert "Hello Worlds again" in _plain_lines(journal)

    old = _line_glyphs(journal, 116)  # DeltaEchoFoxtrot
    _looks_right(_edit(journal, "line", "Delta Echo Foxtrot", new_text="elta Echo Foxtrot"))
    new = _line_glyphs(journal, 116)
    assert "".join(g.char for g in new) == "eltaEchoFoxtrot"
    assert _xs(new) == pytest.approx([x - _width("D") for x in _xs(old[1:])], abs=0.3)
    _looks_right(_edit(journal, "word", "Bravo", new_text="ravo"))
    assert "Alpha ravo Charlie" in _plain_lines(journal)


@pytest.mark.feature("EDT-17", criterion=2)
def test_restyling_a_mixed_line_restyles_each_run_from_its_own_style() -> None:
    """Review F2: a size or slant change keeps the bold run bold."""
    journal = _journal()
    _looks_right(_edit(journal, "line", "Hello World again", size=14))
    assert _spans_on(journal, 200) == [
        ("Hello ", "Helvetica", 14.0),
        ("World", "Helvetica-Bold", 14.0),
        (" again", "Helvetica", 14.0),
    ]
    journal.undo()
    _looks_right(_edit(journal, "line", "Hello World again", italic=True))
    assert [font for _text, font, _size in _spans_on(journal, 200)] == [
        "Helvetica-Oblique",
        "Helvetica-BoldOblique",
        "Helvetica-Oblique",
    ]


@pytest.mark.feature("EDT-17", criterion=2)
def test_resizing_a_line_scales_a_superscript_and_keeps_it_raised() -> None:
    journal = _raw_journal(
        "BT /helv 12 Tf 72 700 Td (E=mc) Tj /helv 7 Tf 5 Ts (2) Tj 0 Ts /helv 12 Tf ( is famous) Tj ET "
        "BT /helv 12 Tf 72 670 Td (Second line here) Tj ET"
    )
    line = _unit(journal, "line", "E=mc2 is famous")
    baseline = line.origin[1]
    two = next(g for g in _glyphs(journal) if g.char == "2")

    _looks_right(_edit(journal, "line", "E=mc2 is famous", size=18))

    spans = extract_page_spans(journal.document.raw, 0)
    raised = next(s for s in spans if s.style.text == "2")
    assert raised.style.size == pytest.approx(7 * 18 / 12, abs=0.05)
    assert raised.style.chars[0].origin[1] == pytest.approx(two.origin[1], abs=0.05)  # still 5pt above
    assert baseline - raised.style.chars[0].origin[1] == pytest.approx(5, abs=0.05)
    assert "E=mc2 is famous" in _unit(journal, "line", "E=mc2 is famous").text


@pytest.mark.feature("EDT-17", criterion=2)
def test_resizing_a_line_of_tj_placed_words_keeps_the_gaps_between_them() -> None:
    journal = _raw_journal(
        "BT /helv 12 Tf 72 700 Td [(Alpha) -900 (Bravo) -900 (Charlie)] TJ ET "
        "BT /helv 12 Tf 72 670 Td (Second line here) Tj ET"
    )
    baseline = _unit(journal, "line", "Alpha Bravo Charlie").origin[1]

    def gaps() -> list[float]:
        glyphs = _line_glyphs(journal, baseline)
        starts = [0, 5, 10]
        return [glyphs[s].bbox[0] - glyphs[s - 1].bbox[2] for s in starts[1:]]

    before = gaps()
    _looks_right(_edit(journal, "line", "Alpha Bravo Charlie", size=15))
    assert gaps() == pytest.approx(before, abs=0.3)  # 10.8pt each, not collapsed or scaled away
    assert _unit(journal, "line", "Alpha Bravo Charlie")


@pytest.mark.feature("EDT-17", criterion=1)
def test_an_edit_that_would_run_off_the_page_is_refused() -> None:
    """Review F6: the end of the line is checked, not just its start."""
    journal = _journal()
    before = _render(journal)
    word = _unit(journal, "word", "Bravo")
    op = {
        "op": "edit_text_unit",
        "page_index": 0,
        "unit": "word",
        "index": word.index,
        "expect_text": "Bravo",
        "new_text": "Bravo " + "verylongword " * 12,
    }
    with pytest.raises(OpValidationError, match="run off the page"):
        journal.record(parse_op(op))
    assert pixel_diff(before, _render(journal)).matches


@pytest.mark.feature("EDT-17", criterion=1)
def test_expect_text_is_required_for_a_word_or_line_edit() -> None:
    """Review F8: a line or word index alone is too easy to go stale."""
    journal = _journal()
    word = _unit(journal, "word", "Bravo")
    with pytest.raises(OpValidationError, match="expect_text is required"):
        parse_op({"op": "edit_text_unit", "page_index": 0, "unit": "word", "index": word.index, "new_text": "X"})
    block = _unit(journal, "block", "Alpha Bravo Charlie Delta Echo Foxtrot Golf Hotel India")
    parse_op({"op": "edit_text_unit", "page_index": 0, "unit": "block", "index": block.index, "color": (1, 0, 0)})


@pytest.mark.feature("EDT-17", criterion=4)
def test_recoloring_a_mixed_block_keeps_every_runs_font() -> None:
    """Review C4: a color-only block edit recolors each line in place instead of re-wrapping
    the paragraph in one font."""
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_WIDTH, height=842)
    x = 72.0
    for text, font in (("Plain ", "helv"), ("bold", "hebo"), (" words", "helv")):
        page.insert_text((x, 100), text, fontname=font, fontsize=12)
        x += float(pymupdf.Font(font).text_length(text, fontsize=12))
    page.insert_text((72, 116), "second line of it", fontname="helv", fontsize=12)
    journal = UndoRedoJournal(Document.from_bytes(doc.tobytes()))
    before = _render(journal)
    origins = [g.origin for g in _glyphs(journal)]
    block = next(u for u in text_units(extract_page_spans(journal.document.raw, 0), "block") if "Plain" in u.text)

    results = journal.record(
        parse_op(
            {
                "op": "edit_text_unit",
                "page_index": 0,
                "unit": "block",
                "index": block.index,
                "expect_text": block.text,
                "color": (1.0, 0.0, 0.0),
            }
        )
    )

    assert isinstance(results, list) and results
    assert all(not r.requires_approval and r.verification and r.verification.looks_right for r in results)
    spans = extract_page_spans(journal.document.raw, 0)
    assert {s.style.font for s in spans if "bold" in s.style.text} == {"Helvetica-Bold"}
    assert all(s.style.color == (1.0, 0.0, 0.0) for s in spans if s.style.text.strip())
    assert sorted(g.origin for g in _glyphs(journal)) == pytest.approx(sorted(origins), abs=0.01)
    assert len(journal.history) == 1
    journal.undo()
    assert pixel_diff(before, _render(journal)).matches


@pytest.mark.feature("EDT-17", criterion=1)
def test_a_line_that_already_overhangs_the_page_can_still_be_edited() -> None:
    """Review C5: only pushing text further off the page is refused."""
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_WIDTH, height=842)
    page.insert_text((560, 100), "Edge running footer text", fontname="helv", fontsize=12)
    page.insert_text((72, 130), "Other line", fontname="helv", fontsize=12)
    data = doc.tobytes()

    journal = UndoRedoJournal(Document.from_bytes(data))
    _looks_right(_edit(journal, "word", "Edge", new_text="Edgy"))  # on a line that runs off the page
    journal = UndoRedoJournal(Document.from_bytes(data))
    _looks_right(_edit(journal, "word", "Edge", color=(1.0, 0.0, 0.0)))
    journal = UndoRedoJournal(Document.from_bytes(data))
    journal.record(parse_op({"op": "delete_objects", "items": [_word_ref(journal, "running")], "close_gap": False}))
    journal = UndoRedoJournal(Document.from_bytes(data))
    with pytest.raises(OpValidationError, match="off the page"):  # pushing it further off is still refused
        journal.record(parse_op({"op": "move_objects", "items": [_word_ref(journal, "Edge")], "dx": 100, "dy": 0}))


def _word_ref(journal: UndoRedoJournal, text: str) -> dict[str, object]:
    word = _unit(journal, "word", text)
    return {"kind": "text", "page_index": 0, "unit": "word", "index": word.index, "expect_text": text}


@pytest.mark.feature("EDT-17", criterion=2)
def test_bold_on_a_font_of_unknown_family_asks_for_approval(corpus: Corpus, work_dir: Path) -> None:
    """Review C6: the family had to be guessed, so the typeface changes: approximate."""
    path = work_dir / "unknown-family.pdf"
    with pikepdf.open(corpus.embedded_font_subset) as pdf:
        font = pdf.pages[0].Resources.Font["/EmbeddedVeraBold"]
        font["/BaseFont"] = pikepdf.Name("/XYZUNK+TotallyUnknownFontXYZ")
        font["/DescendantFonts"][0]["/BaseFont"] = pikepdf.Name("/XYZUNK+TotallyUnknownFontXYZ")
        pdf.save(path)
    journal = UndoRedoJournal(Document.open(path))
    word = text_units(extract_page_spans(journal.document.raw, 0), "word")[0]
    op = {
        "op": "edit_text_unit",
        "page_index": 0,
        "unit": "word",
        "index": word.index,
        "expect_text": word.text,
        "bold": True,
    }
    with pytest.raises(OpValidationError, match="fell back to tier 'approximate'"):
        journal.record(parse_op({**op, "require_tier": "exact"}))
    result = journal.record(parse_op(op))
    assert isinstance(result, EditResult)
    assert result.requires_approval
    assert "not a known family" in result.note
    journal.document.close()


@pytest.mark.feature("EDT-17", criterion=2)
def test_bold_on_text_that_is_already_bold_is_refused_or_dropped() -> None:
    """Review C7: a request that changes nothing visible is refused; alongside another
    change it is simply dropped."""
    journal = _journal()
    with pytest.raises(OpValidationError, match="already bold"):
        _edit(journal, "word", "World", bold=True)
    with pytest.raises(OpValidationError, match="already not italic"):
        _edit(journal, "word", "World", italic=False)
    assert not journal.history
    result = _edit(journal, "word", "World", bold=True, color=(0.0, 0.0, 1.0))
    _looks_right(result)
    assert {s.style.font for s in extract_page_spans(journal.document.raw, 0) if s.style.text == "World"} == {
        "Helvetica-Bold"
    }


@pytest.mark.feature("EDT-17", criterion=2)
def test_restyling_a_word_moves_nothing_before_it_and_the_rest_only_by_its_width_change() -> None:
    """EDT-17 c2 as worded: glyphs before the word stay put; the rest of the line shifts by
    exactly the word's change in width; a color change moves nothing at all."""
    journal = _journal()
    old = _line_glyphs(journal, 100)  # AlphaBravoCharlie
    _looks_right(_edit(journal, "word", "Bravo", bold=True))
    new = _line_glyphs(journal, 100)
    delta = float(pymupdf.Font("hebo").text_length("Bravo", fontsize=12)) - _width("Bravo")
    assert _xs(new[:5]) == pytest.approx(_xs(old[:5]), abs=0.01)
    assert _xs(new[10:]) == pytest.approx([x + delta for x in _xs(old[10:])], abs=0.05)
    journal.undo()
    _looks_right(_edit(journal, "word", "Bravo", color=(0.0, 0.6, 0.0)))
    assert _xs(_line_glyphs(journal, 100)) == pytest.approx(_xs(old), abs=0.01)


def _narrow_columns() -> UndoRedoJournal:
    """Two 10pt columns 12pt apart: under the 1.6x-size column gap, so each row is one
    line unit spanning both columns."""
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_WIDTH, height=842)
    left = float(pymupdf.Font("helv").text_length("Left column line 0 text", fontsize=10))
    for i in range(4):
        page.insert_text((72, 100 + 14 * i), f"Left column line {i} text", fontname="helv", fontsize=10)
        page.insert_text((72 + left + 12, 100 + 14 * i), f"Right col line {i}", fontname="helv", fontsize=10)
    return UndoRedoJournal(Document.from_bytes(doc.tobytes()))


def _right_column(journal: UndoRedoJournal) -> list[tuple[str, tuple[float, float]]]:
    return [(g.char, g.origin) for g in _glyphs(journal) if g.origin[0] > 178]


@pytest.mark.feature("EDT-17", criterion=1)
def test_a_wider_word_in_one_column_never_moves_the_next_column_on_its_line() -> None:
    """Review C1b: the rest-of-line shift stops at a gutter between spans."""
    journal = _narrow_columns()
    assert any("Left column" in u.text and "Right col" in u.text for u in _units_of(journal, "line"))
    right = sorted(_right_column(journal))
    word = next(u for u in _units_of(journal, "word") if u.text == "column")

    result = journal.record(
        parse_op(
            {
                "op": "edit_text_unit",
                "page_index": 0,
                "unit": "word",
                "index": word.index,
                "expect_text": "column",
                "new_text": "columns",
            }
        )
    )

    assert isinstance(result, EditResult)
    _looks_right(result)
    after = sorted(_right_column(journal))
    assert [c for c, _o in after] == [c for c, _o in right]
    assert [o for _c, o in after] == pytest.approx([o for _c, o in right], abs=0.01)
    assert "Left columns line 0 text" in " ".join(u.text for u in _units_of(journal, "line"))


@pytest.mark.feature("EDT-17", criterion=1)
def test_closing_a_deleted_words_gap_never_moves_the_next_column() -> None:
    journal = _narrow_columns()
    right = sorted(_right_column(journal))
    word = next(u for u in _units_of(journal, "word") if u.text == "column")

    journal.record(
        parse_op(
            {
                "op": "delete_objects",
                "items": [
                    {"kind": "text", "page_index": 0, "unit": "word", "index": word.index, "expect_text": "column"}
                ],
            }
        )
    )

    after = sorted(_right_column(journal))
    assert [c for c, _o in after] == [c for c, _o in right]
    assert [o for _c, o in after] == pytest.approx([o for _c, o in right], abs=0.01)
    assert "Left line 0 text" in " ".join(u.text for u in _units_of(journal, "line"))


def _units_of(journal: UndoRedoJournal, granularity: str) -> list[TextUnit]:
    return text_units(extract_page_spans(journal.document.raw, 0), granularity)  # type: ignore[arg-type]
