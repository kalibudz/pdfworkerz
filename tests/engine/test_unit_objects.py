"""EDT-18: moving, copying and deleting single lines and words (move_objects,
duplicate_objects, delete_objects with ObjectRef.unit).

Each page is built by the test. Every check is about fidelity (SPEC.md 8.2 item 6): the
glyphs of the unit land exactly where asked, and no other glyph moves -- except the rest
of a line closing the gap a deleted word leaves.
"""

from __future__ import annotations

import io
import math
from typing import Any

import numpy as np
import pymupdf
import pytest
from numpy.typing import NDArray
from PIL import Image

from engine.document import Document
from engine.edit import VERIFY_DPI, EditResult
from engine.errors import OpValidationError
from engine.fonts.style import CharBox, extract_page_spans
from engine.fonts.units import TextUnit, text_units
from engine.images import list_images
from engine.ops.base import parse_op
from engine.ops.journal import UndoRedoJournal
from engine.verify import changed_outside, pixel_diff, render_to_array

RIGHT_EDGE = 540.0
PAGE_WIDTH = 595.0
_HELV = pymupdf.Font("helv")
_MARGIN_PX = 2
BLOCK = "Alpha Bravo Charlie Delta Echo Foxtrot Golf Hotel India"


def _width(text: str) -> float:
    return float(_HELV.text_length(text, fontsize=12))


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (20, 10), (200, 30, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


def _journal() -> UndoRedoJournal:
    """Page 0: a three-line paragraph, a heading, two right-aligned lines, a centered line
    and an image. Page 1: empty."""
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_WIDTH, height=842)
    for i, text in enumerate(["Alpha Bravo Charlie", "Delta Echo Foxtrot", "Golf Hotel India"]):
        page.insert_text((72, 100 + 16 * i), text, fontname="helv", fontsize=12)
    page.insert_text((72, 200), "Standalone heading text", fontname="helv", fontsize=12)
    for i, text in enumerate(["Total due today", "Paid in full"]):
        page.insert_text((RIGHT_EDGE - _width(text), 300 + 16 * i), text, fontname="helv", fontsize=12)
    title = "Quarterly Summary Report"
    page.insert_text((PAGE_WIDTH / 2 - _width(title) / 2, 400), title, fontname="helv", fontsize=12)
    page.insert_image(pymupdf.Rect(430, 40, 530, 80), stream=_png())
    doc.new_page(width=PAGE_WIDTH, height=842)
    return UndoRedoJournal(Document.from_bytes(doc.tobytes()))


def _raw_journal(stream: str) -> UndoRedoJournal:
    """A page whose content stream is written by hand (PDF space: y up)."""
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_WIDTH, height=842)
    page.insert_text((72, 700), "anchor", fontname="helv", fontsize=12)
    doc.update_stream(page.get_contents()[0], stream.encode())
    return UndoRedoJournal(Document.from_bytes(doc.tobytes()))


def _units(journal: UndoRedoJournal, granularity: str, page: int = 0) -> list[TextUnit]:
    return text_units(extract_page_spans(journal.document.raw, page), granularity)  # type: ignore[arg-type]


def _unit(journal: UndoRedoJournal, granularity: str, text: str, page: int = 0) -> TextUnit:
    return next(unit for unit in _units(journal, granularity, page) if unit.text == text)


def _ref(journal: UndoRedoJournal, granularity: str, text: str, **extra: Any) -> dict[str, Any]:
    unit = _unit(journal, granularity, text)
    ref = {"kind": "text", "page_index": 0, "unit": granularity, "index": unit.index, "expect_text": unit.text}
    return {**ref, **extra}


def _render(journal: UndoRedoJournal, page: int = 0) -> NDArray[np.uint8]:
    return render_to_array(journal.document.raw, page, dpi=VERIFY_DPI)


def _pixels(box: tuple[float, float, float, float], dx: float = 0.0, dy: float = 0.0) -> tuple[int, int, int, int]:
    scale = VERIFY_DPI / 72
    return (
        math.floor((box[0] + dx) * scale) - _MARGIN_PX,
        math.floor((box[1] + dy) * scale) - _MARGIN_PX,
        math.ceil((box[2] + dx) * scale) + _MARGIN_PX,
        math.ceil((box[3] + dy) * scale) + _MARGIN_PX,
    )


def _glyphs_in(journal: UndoRedoJournal, unit: TextUnit, page: int = 0) -> list[CharBox]:
    spans = extract_page_spans(journal.document.raw, page)
    return [spans[s.span_index].style.chars[i] for s in unit.segments for i in range(s.start, s.end)]


def _line_glyphs(journal: UndoRedoJournal, baseline: float) -> list[CharBox]:
    """The visible glyphs on one baseline, left to right."""
    glyphs = [c for s in extract_page_spans(journal.document.raw, 0) for c in s.style.chars]
    on_line = [g for g in glyphs if abs(g.origin[1] - baseline) < 0.5 and not g.char.isspace()]
    return sorted(on_line, key=lambda g: g.origin[0])


def _xs(glyphs: list[CharBox]) -> list[float]:
    return [g.origin[0] for g in glyphs]


def _all_look_right(results: object) -> list[EditResult]:
    assert isinstance(results, list)
    texts = [r for r in results if isinstance(r, EditResult)]
    assert texts
    for result in texts:
        assert result.tier == "exact"
        assert result.verification is not None
        assert result.verification.looks_right, result.verification
    return texts


def _assert_moved(old: list[CharBox], new: list[CharBox], dx: float, dy: float) -> None:
    assert [g.char for g in new] == [g.char for g in old]
    for before, after in zip(old, new, strict=True):
        assert after.origin[0] == pytest.approx(before.origin[0] + dx, abs=0.3)
        assert after.origin[1] == pytest.approx(before.origin[1] + dy, abs=0.3)


@pytest.mark.feature("EDT-18", criterion=1)
def test_moving_a_word_leaves_the_rest_of_its_line_pixel_identical() -> None:
    journal = _journal()
    word = _unit(journal, "word", "Bravo")
    old = _glyphs_in(journal, word)
    before = _render(journal)

    results = journal.record(
        parse_op({"op": "move_objects", "items": [_ref(journal, "word", "Bravo")], "dx": 150, "dy": 370})
    )

    _all_look_right(results)
    after = _render(journal)
    assert changed_outside(before, after, [_pixels(word.bbox), _pixels(word.bbox, 150, 370)]) == 0.0
    moved = next(u for u in _units(journal, "word") if u.text == "Bravo")
    _assert_moved(old, _glyphs_in(journal, moved), 150, 370)
    # "Alpha" and "Charlie" are still exactly where they were.
    assert _unit(journal, "word", "Alpha").bbox[0] == pytest.approx(72, abs=0.01)
    assert len(journal.history) == 1
    journal.undo()
    assert pixel_diff(before, _render(journal)).matches


@pytest.mark.feature("EDT-18", criterion=1)
@pytest.mark.parametrize(
    "line_ops",
    [
        "[(A) 60 (lpha ) -300 (B) 40 (r) -80 (avo ) -250 (Ch) 30 (arlie)] TJ",
        "6 Tw (Alpha Bravo Charlie) Tj",
        "1.5 Tc (Alpha Bravo Charlie) Tj",
    ],
    ids=["tj-kerned", "word-spaced", "char-spaced"],
)
def test_a_moved_word_keeps_each_glyph_offset_however_the_line_was_spaced(line_ops: str) -> None:
    journal = _raw_journal(
        f"BT /helv 12 Tf 72 700 Td {line_ops} ET BT /helv 12 Tf 0 Tw 0 Tc 72 670 Td (Second line here) Tj ET"
    )
    word = _unit(journal, "word", "Bravo")
    old = _glyphs_in(journal, word)
    before = _render(journal)

    results = journal.record(
        parse_op({"op": "move_objects", "items": [_ref(journal, "word", "Bravo")], "dx": 20.5, "dy": 60})
    )

    _all_look_right(results)
    assert changed_outside(before, _render(journal), [_pixels(word.bbox), _pixels(word.bbox, 20.5, 60)]) == 0.0
    _assert_moved(old, _glyphs_in(journal, _unit(journal, "word", "Bravo")), 20.5, 60)


@pytest.mark.feature("EDT-18", criterion=2)
def test_moving_a_line_leaves_the_rest_of_its_block_in_place() -> None:
    journal = _journal()
    line = _unit(journal, "line", "Delta Echo Foxtrot")
    old = _glyphs_in(journal, line)
    neighbours = [_unit(journal, "line", text).bbox for text in ("Alpha Bravo Charlie", "Golf Hotel India")]
    before = _render(journal)

    results = journal.record(
        parse_op({"op": "move_objects", "items": [_ref(journal, "line", "Delta Echo Foxtrot")], "dx": 40, "dy": 400})
    )

    _all_look_right(results)
    after = _render(journal)
    assert changed_outside(before, after, [_pixels(line.bbox), _pixels(line.bbox, 40, 400)]) == 0.0
    _assert_moved(old, _glyphs_in(journal, _unit(journal, "line", "Delta Echo Foxtrot")), 40, 400)
    assert [_unit(journal, "line", text).bbox for text in ("Alpha Bravo Charlie", "Golf Hotel India")] == neighbours
    journal.undo()
    assert pixel_diff(before, _render(journal)).matches


@pytest.mark.feature("EDT-18", criterion=3)
def test_duplicating_a_line_and_a_word_on_the_same_page() -> None:
    journal = _journal()
    line = _unit(journal, "line", "Delta Echo Foxtrot")
    word = _unit(journal, "word", "India")
    old_line, old_word = _glyphs_in(journal, line), _glyphs_in(journal, word)
    before = _render(journal)
    items = [_ref(journal, "line", "Delta Echo Foxtrot"), _ref(journal, "word", "India")]

    results = journal.record(parse_op({"op": "duplicate_objects", "items": items, "dx": 30, "dy": 420}))

    assert len(_all_look_right(results)) == 2
    assert len(journal.history) == 1
    # The originals are untouched; only the two copies' boxes changed.
    after = _render(journal)
    assert changed_outside(before, after, [_pixels(line.bbox, 30, 420), _pixels(word.bbox, 30, 420)]) == 0.0
    lines = [u for u in _units(journal, "line") if u.text == "Delta Echo Foxtrot"]
    assert len(lines) == 2
    _assert_moved(old_line, _glyphs_in(journal, lines[1]), 30, 420)
    words = [u for u in _units(journal, "word") if u.text == "India"]
    assert len(words) == 2
    _assert_moved(old_word, _glyphs_in(journal, words[1]), 30, 420)
    journal.undo()
    assert pixel_diff(before, _render(journal)).matches


@pytest.mark.feature("EDT-18", criterion=3)
def test_duplicating_a_line_and_a_word_onto_another_page() -> None:
    journal = _journal()
    line = _unit(journal, "line", "Total due today")
    word = _unit(journal, "word", "Hotel")
    old_line, old_word = _glyphs_in(journal, line), _glyphs_in(journal, word)
    first_page = _render(journal)
    items = [_ref(journal, "line", "Total due today"), _ref(journal, "word", "Hotel")]

    results = journal.record(
        parse_op({"op": "duplicate_objects", "items": items, "dx": 0, "dy": 0, "target_page_index": 1})
    )

    _all_look_right(results)
    assert pixel_diff(first_page, _render(journal)).matches  # the source page is untouched
    assert [u.text for u in _units(journal, "line", page=1)] == ["Hotel", "Total due today"]
    _assert_moved(old_line, _glyphs_in(journal, _unit(journal, "line", "Total due today", page=1), page=1), 0, 0)
    _assert_moved(old_word, _glyphs_in(journal, _unit(journal, "word", "Hotel", page=1), page=1), 0, 0)
    with pytest.raises(OpValidationError, match="copied to another page"):
        from engine.edit import move_glyph_ranges
        from engine.ops.text import _font_index

        spans = extract_page_spans(journal.document.raw, 0)
        ranges = [(spans[s.span_index], s.start, s.end) for s in word.segments]
        move_glyph_ranges(journal.document, 0, ranges, target_page_index=1, font_index=_font_index())


@pytest.mark.feature("EDT-18", criterion=4)
def test_deleting_a_word_on_a_left_aligned_line_pulls_what_follows_left() -> None:
    journal = _journal()
    line = _unit(journal, "line", "Alpha Bravo Charlie")
    old = _line_glyphs(journal, 100)  # AlphaBravoCharlie
    before = _render(journal)

    results = journal.record(parse_op({"op": "delete_objects", "items": [_ref(journal, "word", "Bravo")]}))

    texts = _all_look_right(results)
    assert "left-aligned" in texts[0].note
    assert _unit(journal, "line", "Alpha Charlie")  # one space left between them, not two
    new = _line_glyphs(journal, 100)
    assert _xs(new[:5]) == pytest.approx(_xs(old[:5]), abs=0.01)  # "Alpha" untouched
    closed = _width("Bravo ")
    assert _xs(new[5:]) == pytest.approx([x - closed for x in _xs(old[10:])], abs=0.3)  # "Charlie" where "Bravo" was
    assert changed_outside(before, _render(journal), [_pixels(line.bbox)]) == 0.0
    assert len(journal.history) == 1
    journal.undo()
    assert pixel_diff(before, _render(journal)).matches


@pytest.mark.feature("EDT-18", criterion=4)
def test_deleting_a_word_on_a_right_aligned_line_pulls_what_precedes_right() -> None:
    journal = _journal()
    old = _line_glyphs(journal, 300)  # Totalduetoday

    results = journal.record(parse_op({"op": "delete_objects", "items": [_ref(journal, "word", "due")]}))

    texts = _all_look_right(results)
    assert "right-aligned" in texts[0].note
    line = _unit(journal, "line", "Total today")
    assert line.bbox[2] == pytest.approx(RIGHT_EDGE, abs=0.05)  # still ends at the margin
    new = _line_glyphs(journal, 300)
    assert _xs(new[-5:]) == pytest.approx(_xs(old[-5:]), abs=0.01)  # "today" untouched
    closed = _width("due ")
    assert _xs(new[:5]) == pytest.approx([x + closed for x in _xs(old[:5])], abs=0.3)


@pytest.mark.feature("EDT-18", criterion=4)
def test_deleting_the_last_word_of_a_right_aligned_line_takes_the_space_before_it() -> None:
    journal = _journal()

    results = journal.record(parse_op({"op": "delete_objects", "items": [_ref(journal, "word", "today")]}))

    _all_look_right(results)
    line = _unit(journal, "line", "Total due")  # no trailing space left behind
    assert line.bbox[2] == pytest.approx(RIGHT_EDGE, abs=0.3)


@pytest.mark.feature("EDT-18", criterion=4)
def test_deleting_a_word_on_a_centered_line_pulls_both_sides_half_way() -> None:
    journal = _journal()
    old = _line_glyphs(journal, 400)  # QuarterlySummaryReport
    centre = PAGE_WIDTH / 2

    results = journal.record(parse_op({"op": "delete_objects", "items": [_ref(journal, "word", "Summary")]}))

    texts = _all_look_right(results)
    assert "center-aligned" in texts[0].note
    line = _unit(journal, "line", "Quarterly Report")
    assert (line.bbox[0] + line.bbox[2]) / 2 == pytest.approx(centre, abs=0.3)
    half = _width("Summary ") / 2
    new = _line_glyphs(journal, 400)
    assert _xs(new[:9]) == pytest.approx([x + half for x in _xs(old[:9])], abs=0.3)
    assert _xs(new[9:]) == pytest.approx([x - half for x in _xs(old[16:])], abs=0.3)


@pytest.mark.feature("EDT-18", criterion=4)
def test_deleting_a_word_without_closing_the_gap_moves_nothing() -> None:
    journal = _journal()
    word = _unit(journal, "word", "Bravo")
    old = _line_glyphs(journal, 100)
    before = _render(journal)
    op = {"op": "delete_objects", "items": [_ref(journal, "word", "Bravo")], "close_gap": False}

    results = journal.record(parse_op(op))

    _all_look_right(results)
    new = _line_glyphs(journal, 100)
    assert _xs(new) == pytest.approx(_xs(old[:5] + old[10:]), abs=0.01)
    box = (word.bbox[0], word.bbox[1], word.bbox[2] + _width(" "), word.bbox[3])  # the word and its space
    assert changed_outside(before, _render(journal), [_pixels(box)]) == 0.0


@pytest.mark.feature("EDT-18", criterion=4)
def test_deleting_two_words_of_one_line_and_a_whole_line() -> None:
    journal = _journal()
    neighbours = [_unit(journal, "line", text).bbox for text in ("Alpha Bravo Charlie", "Delta Echo Foxtrot")]
    items = [_ref(journal, "word", "Golf"), _ref(journal, "word", "India"), _ref(journal, "line", "Paid in full")]

    results = journal.record(parse_op({"op": "delete_objects", "items": items}))

    assert len(_all_look_right(results)) == 2  # the two words of one line went in one pass
    hotel = _unit(journal, "line", "Hotel")
    assert hotel.origin[0] == pytest.approx(72, abs=0.3)
    assert "Paid in full" not in [u.text for u in _units(journal, "line")]
    assert [_unit(journal, "line", text).bbox for text in ("Alpha Bravo Charlie", "Delta Echo Foxtrot")] == neighbours
    assert len(journal.history) == 1


@pytest.mark.feature("EDT-18", criterion=5)
def test_a_mixed_selection_is_one_undo_drops_contained_units_and_survives_renumbering() -> None:
    journal = _journal()
    before = _render(journal)
    image_before = list_images(journal.document, 0)[0].rect
    heading = _unit(journal, "block", "Standalone heading text")
    items = [
        _ref(journal, "word", "due"),
        {"kind": "text", "page_index": 0, "index": heading.index, "expect_text": heading.text},
        _ref(journal, "word", "heading"),  # inside the selected heading block: dropped
        _ref(journal, "line", "Delta Echo Foxtrot"),
        _ref(journal, "word", "Echo"),  # inside the selected line: dropped
        _ref(journal, "word", "Summary"),
        _ref(journal, "word", "Report"),  # same line as "Summary": moved in the same pass
        {"kind": "image", "page_index": 0, "index": 0},
        _ref(journal, "line", "Delta Echo Foxtrot"),  # named twice: once is enough
    ]
    old = {
        text: unit.origin
        for text in ("due", "Echo", "heading", "Summary", "Report")
        for unit in [_unit(journal, "word", text)]
    }

    results = journal.record(parse_op({"op": "move_objects", "items": items, "dx": 7, "dy": 420}))

    # Five text changes (word, block, line, the two words of one line together) and the image.
    assert len(_all_look_right(results)) == 4
    assert len(results) == 5
    assert len(journal.history) == 1
    for text, origin in old.items():
        moved = [u for u in _units(journal, "word") if u.text == text]
        assert len(moved) == 1, text  # moved once, not once per selected unit that contains it
        assert moved[0].origin == pytest.approx((origin[0] + 7, origin[1] + 420), abs=0.3)
    assert list_images(journal.document, 0)[0].rect[1] == pytest.approx(image_before[1] + 420, abs=0.5)
    assert _unit(journal, "line", "Alpha Bravo Charlie").origin == pytest.approx((72, 100), abs=0.01)

    journal.undo()
    assert pixel_diff(before, _render(journal)).matches
    assert not journal.can_undo


@pytest.mark.feature("EDT-18", criterion=5)
def test_words_of_one_line_can_each_move_by_their_own_offset() -> None:
    """What align sends: every unit its own dx. The second word is found again by its
    origin and text after the first one's move renumbered the page."""
    journal = _journal()
    old = {text: _unit(journal, "word", text).origin for text in ("Alpha", "Charlie")}
    items = [_ref(journal, "word", "Alpha", dx=300), _ref(journal, "word", "Charlie", dx=-5, dy=500)]

    results = journal.record(parse_op({"op": "move_objects", "items": items}))

    assert len(_all_look_right(results)) == 2
    assert _unit(journal, "word", "Alpha").origin == pytest.approx((old["Alpha"][0] + 300, old["Alpha"][1]), abs=0.3)
    assert _unit(journal, "word", "Charlie").origin == pytest.approx(
        (old["Charlie"][0] - 5, old["Charlie"][1] + 500), abs=0.3
    )


@pytest.mark.feature("EDT-18", criterion=5)
def test_a_stale_selection_is_refused_and_changes_nothing() -> None:
    journal = _journal()
    before = _render(journal)
    good = _ref(journal, "word", "Alpha")
    stale = {**_ref(journal, "word", "Bravo"), "expect_text": "Charlie"}

    for op in ("move_objects", "duplicate_objects", "delete_objects"):
        with pytest.raises(OpValidationError, match="is now 'Bravo', not 'Charlie'"):
            journal.record(
                parse_op(
                    {"op": op, "items": [good, stale], "dx": 5, "dy": 5}
                    if op != "delete_objects"
                    else {"op": op, "items": [good, stale]}
                )
            )
    stale_line = {**_ref(journal, "line", "Golf Hotel India"), "expect_text": "Golf Hotel"}
    with pytest.raises(OpValidationError, match="is now 'Golf Hotel India'"):
        journal.record(parse_op({"op": "delete_objects", "items": [stale_line]}))
    block = _unit(journal, "block", BLOCK)
    stale_block = {"kind": "text", "page_index": 0, "index": block.index, "expect_text": "something else"}
    with pytest.raises(OpValidationError, match="is now"):
        journal.record(parse_op({"op": "delete_objects", "items": [stale_block]}))
    with pytest.raises(OpValidationError, match="out of range"):
        journal.record(parse_op({"op": "delete_objects", "items": [{**good, "index": 999}]}))
    assert pixel_diff(before, _render(journal)).matches
    assert not journal.history


@pytest.mark.feature("EDT-18", criterion=5)
def test_unit_and_expect_text_are_only_for_text() -> None:
    with pytest.raises(OpValidationError, match='only for kind "text"'):
        parse_op({"op": "delete_objects", "items": [{"kind": "image", "page_index": 0, "index": 0, "unit": "word"}]})
    op = parse_op({"op": "delete_objects", "items": [{"kind": "text", "page_index": 0, "index": 0}]})
    assert op.items[0].unit == "block" and op.close_gap is True  # type: ignore[attr-defined]


@pytest.mark.feature("EDT-18", criterion=4)
def test_a_line_with_a_deleted_word_still_reads_in_order_in_plain_extraction() -> None:
    """The tail that closed the gap is appended to the page's content; the line is drawn
    again in reading order so copy-and-paste doesn't see "Alpha", other lines, "Charlie"."""
    journal = _journal()
    journal.record(parse_op({"op": "delete_objects", "items": [_ref(journal, "word", "Bravo")]}))
    reloaded = pymupdf.open("pdf", journal.document.to_bytes())
    lines = [line.strip() for line in reloaded[0].get_text().splitlines()]
    reloaded.close()
    assert "Alpha Charlie" in lines
    assert "Delta Echo Foxtrot" in lines


@pytest.mark.feature("EDT-18", criterion=5)
def test_the_same_line_at_the_same_spot_on_two_pages_is_two_objects() -> None:
    """Review F5: a running header on every page is selected once per page, and each page's
    copy is changed -- not only the first page's."""
    doc = pymupdf.open()
    for n in range(2):
        page = doc.new_page(width=PAGE_WIDTH, height=842)
        page.insert_text((72, 60), "Confidential draft", fontname="helv", fontsize=12)
        page.insert_text((72, 100), f"Body {n}", fontname="helv", fontsize=12)
    journal = UndoRedoJournal(Document.from_bytes(doc.tobytes()))
    words = [
        {"kind": "text", "page_index": p, "unit": "word", "index": _unit(journal, "word", "draft", page=p).index}
        for p in (0, 1)
    ]

    journal.record(parse_op({"op": "delete_objects", "items": [{**w, "expect_text": "draft"} for w in words]}))
    assert [u.text for u in _units(journal, "line", 0)] == ["Confidential", "Body 0"]
    assert [u.text for u in _units(journal, "line", 1)] == ["Confidential", "Body 1"]

    items = [
        {"kind": "text", "page_index": p, "unit": "line", "index": 0, "expect_text": "Confidential"} for p in (0, 1)
    ]
    journal.record(parse_op({"op": "delete_objects", "items": items}))
    assert [u.text for u in _units(journal, "line", 0)] == ["Body 0"]
    assert [u.text for u in _units(journal, "line", 1)] == ["Body 1"]


@pytest.mark.feature("EDT-18", criterion=1)
def test_nudging_a_word_twice_by_a_point_is_not_flagged_as_overlapping() -> None:
    """Review F7: after the first nudge the rest of the line is one span across the gap;
    the second nudge moves into that gap's empty space, not onto other text."""
    journal = _journal()
    for _ in range(2):
        results = journal.record(
            parse_op({"op": "move_objects", "items": [_ref(journal, "word", "Bravo")], "dx": 1, "dy": 0})
        )
        _all_look_right(results)
    assert _unit(journal, "word", "Bravo").origin[0] == pytest.approx(72 + _width("Alpha ") + 2, abs=0.3)


@pytest.mark.feature("EDT-18", criterion=5)
def test_expect_text_is_required_for_a_selected_line_or_word() -> None:
    """Review F8."""
    journal = _journal()
    word = _unit(journal, "word", "Bravo")
    with pytest.raises(OpValidationError, match="expect_text is required"):
        parse_op(
            {"op": "delete_objects", "items": [{"kind": "text", "page_index": 0, "unit": "word", "index": word.index}]}
        )
    parse_op({"op": "delete_objects", "items": [{"kind": "text", "page_index": 0, "index": 0}]})  # a block needn't


@pytest.mark.feature("EDT-18", criterion=1)
def test_moving_a_word_onto_the_next_word_of_its_own_line_is_flagged() -> None:
    """Review C3: the rest of a partly removed span is still other text."""
    journal = _journal()
    results = journal.record(
        parse_op({"op": "move_objects", "items": [_ref(journal, "word", "Echo")], "dx": 40, "dy": 0})
    )
    texts = [r for r in results if isinstance(r, EditResult)]
    assert texts[0].verification is not None
    assert texts[0].verification.overlaps_other_text
    assert not texts[0].verification.looks_right
