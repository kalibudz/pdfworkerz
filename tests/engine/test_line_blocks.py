"""EDT-19: text blocks are paragraphs of lines, not of single-style spans.

A line with a bold or colored word, or one an in-line edit redrew in several pieces, is
several spans on one baseline. Block mode must still select the whole paragraph
(criterion 1), and moving, copying or deleting that block must keep every run's own
font, size and color (criterion 2).
"""

from __future__ import annotations

import pymupdf
import pytest

from engine.document import Document
from engine.edit import move_resize_block, reflow_block
from engine.fonts.blocks import TextBlock, detect_blocks, find_block_containing
from engine.fonts.style import CharBox, SpanStyle, SpanTrace, extract_page_spans
from engine.fonts.units import group_blocks, text_units
from engine.ops.base import parse_op
from engine.ops.journal import UndoRedoJournal
from engine.ops.objects import PageBlocksOp
from engine.ops.text import _block_at, _font_index

RED = (1.0, 0.0, 0.0)
PARAGRAPH = "Alpha Bravo Charlie Delta Echo Foxtrot Golf Hotel India"


def _runs(page: pymupdf.Page, y: float, runs: list[tuple[str, str, tuple[float, float, float]]]) -> None:
    x = 72.0
    for text, font, color in runs:
        page.insert_text((x, y), text, fontname=font, fontsize=12, color=color)
        x += float(pymupdf.Font(font).text_length(text, fontsize=12))


def _journal() -> UndoRedoJournal:
    """A three-line paragraph whose middle line has a bold red word, and a separate note."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Alpha Bravo Charlie", fontname="helv", fontsize=12)
    _runs(page, 116, [("Delta ", "helv", (0, 0, 0)), ("Echo", "hebo", RED), (" Foxtrot", "helv", (0, 0, 0))])
    page.insert_text((72, 132), "Golf Hotel India", fontname="helv", fontsize=12)
    page.insert_text((72, 300), "Separate note", fontname="helv", fontsize=12)
    return UndoRedoJournal(Document.from_bytes(doc.tobytes()))


def _spans(journal: UndoRedoJournal) -> list[SpanTrace]:
    return extract_page_spans(journal.document.raw, 0)


def _paragraph(journal: UndoRedoJournal) -> TextBlock:
    spans = _spans(journal)
    first = next(i for i, span in enumerate(spans) if span.style.text.startswith("Alpha"))
    return _block_at(journal.document, 0, first)


def _styles(journal: UndoRedoJournal) -> dict[str, tuple[str, float, tuple[float, float, float], tuple[float, float]]]:
    return {
        s.style.text: (s.style.font, s.style.size, s.style.color, s.style.chars[0].origin)
        for s in _spans(journal)
        if s.style.chars
    }


# -- criterion 1: a line in several runs stays in its paragraph -------------------------------


@pytest.mark.feature("EDT-19", criterion=1)
def test_a_line_with_a_bold_colored_word_joins_its_paragraph() -> None:
    journal = _journal()
    spans = _spans(journal)
    blocks = detect_blocks(spans)
    assert [block.text for block in blocks] == [PARAGRAPH, "Separate note"]
    paragraph = blocks[0]
    assert [len(row) for row in paragraph.rows] == [1, 3, 1]
    assert paragraph.fragmented and paragraph.mixed_style and paragraph.off_style_runs() == 1
    assert paragraph.dominant.style.font == "Helvetica"
    # Every span of the paragraph, clicked in Block mode, selects the whole paragraph.
    for index, span in enumerate(spans[:5]):
        assert _block_at(journal.document, 0, index).lines == paragraph.lines
        assert find_block_containing(blocks, span) is paragraph


@pytest.mark.feature("EDT-19", criterion=1)
def test_block_units_and_the_blocks_route_agree_on_the_paragraph() -> None:
    journal = _journal()
    spans = _spans(journal)
    served = PageBlocksOp(page_index=0).apply(journal.document)
    units = text_units(spans, "block")
    assert [b["span_indices"] for b in served] == [[0, 1, 2, 3, 4], [5]]
    assert [u.span_indices for u in units] == [b["span_indices"] for b in served]
    assert [u.text for u in units] == [PARAGRAPH, "Separate note"]
    assert [g.text for g in group_blocks(spans)] == [PARAGRAPH, "Separate note"]


@pytest.mark.feature("EDT-19", criterion=1)
def test_after_a_word_edit_the_paragraph_is_still_one_block() -> None:
    journal = _journal()
    word = next(u for u in text_units(_spans(journal), "word") if u.text == "Bravo")
    journal.record(
        parse_op(
            {
                "op": "edit_text_unit",
                "page_index": 0,
                "unit": "word",
                "index": word.index,
                "expect_text": "Bravo",
                "new_text": "Beta",
            }
        )
    )
    spans = _spans(journal)
    assert len(spans) > 6  # the edited line now comes back in several pieces
    texts = [u.text for u in text_units(spans, "block")]
    assert "Alpha Beta Charlie Delta Echo Foxtrot Golf Hotel India" in texts
    assert "Separate note" in texts and len(texts) == 2
    # And the same after restyling a word of another line.
    word = next(u for u in text_units(spans, "word") if u.text == "Hotel")
    journal.record(
        parse_op(
            {
                "op": "edit_text_unit",
                "page_index": 0,
                "unit": "word",
                "index": word.index,
                "expect_text": "Hotel",
                "color": [0, 0, 1],
            }
        )
    )
    assert len(text_units(_spans(journal), "block")) == 2


@pytest.mark.feature("EDT-19", criterion=1)
def test_headings_and_other_sizes_still_stand_alone() -> None:
    """The existing limits hold: a heading in another size, or set entirely in another
    face, is not part of the paragraph below it; nor is a line at another margin."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Bold heading", fontname="hebo", fontsize=12)
    page.insert_text((72, 116), "Body text line one", fontname="helv", fontsize=12)
    page.insert_text((72, 132), "Body text line two", fontname="helv", fontsize=12)
    page.insert_text((72, 180), "Big title", fontname="helv", fontsize=18)
    page.insert_text((72, 200), "Under the title", fontname="helv", fontsize=12)
    page.insert_text((150, 214), "Indented elsewhere", fontname="helv", fontsize=12)
    blocks = detect_blocks(extract_page_spans(doc, 0))
    assert [b.text for b in blocks] == [
        "Bold heading",
        "Body text line one Body text line two",
        "Big title",
        "Under the title",
        "Indented elsewhere",
    ]


@pytest.mark.feature("EDT-19", criterion=1)
def test_a_leading_superscript_neither_sets_the_baseline_nor_splits_the_block() -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "First line of a note", fontname="helv", fontsize=12)
    page.insert_text((72, 112), "1", fontname="helv", fontsize=7)
    page.insert_text((76, 116), "Footnote marker line", fontname="helv", fontsize=12)
    (block,) = detect_blocks(extract_page_spans(doc, 0))
    assert block.text == "First line of a note 1Footnote marker line"
    assert block.row_origin(1) == (72.0, 116.0)


@pytest.mark.feature("EDT-19", criterion=1)
def test_a_block_built_from_spans_alone_keeps_one_line_per_span() -> None:
    journal = _journal()
    spans = _spans(journal)
    block = TextBlock(lines=(spans[0], spans[4]))
    assert block.rows == ((spans[0],), (spans[4],))
    assert block.text == "Alpha Bravo Charlie Golf Hotel India"
    assert not block.fragmented and not block.mixed_style
    assert TextBlock(lines=()).text == ""


# -- criterion 2: moving, copying and deleting keeps every run's style --------------------------


@pytest.mark.feature("EDT-19", criterion=2)
def test_moving_a_mixed_block_keeps_each_runs_font_size_and_color() -> None:
    journal = _journal()
    before = _styles(journal)
    index = _paragraph(journal).lines[0].style.span_index
    journal.record(
        parse_op(
            {"op": "move_objects", "items": [{"kind": "text", "page_index": 0, "index": index}], "dx": 30, "dy": 40}
        )
    )
    after = _styles(journal)
    assert after["Echo"][:3] == ("Helvetica-Bold", 12.0, RED)
    for text, (font, size, color, (x, y)) in before.items():
        if text == "Separate note":
            assert after[text] == before[text]
            continue
        assert after[text][:3] == (font, size, color)
        assert after[text][3] == pytest.approx((x + 30, y + 40), abs=0.01)
    assert [u.text for u in text_units(_spans(journal), "block")] == ["Separate note", PARAGRAPH]
    journal.undo()
    assert _styles(journal) == before


@pytest.mark.feature("EDT-19", criterion=2)
def test_duplicating_a_mixed_block_copies_each_run_in_its_own_style() -> None:
    journal = _journal()
    index = _paragraph(journal).lines[0].style.span_index
    item = {"kind": "text", "page_index": 0, "index": index}
    journal.record(parse_op({"op": "duplicate_objects", "items": [item], "dx": 0, "dy": 400}))
    echoes = [s.style for s in _spans(journal) if s.style.text == "Echo"]
    assert len(echoes) == 2
    assert all((e.font, e.color) == ("Helvetica-Bold", RED) for e in echoes)
    assert [u.text for u in text_units(_spans(journal), "block")].count(PARAGRAPH) == 2


@pytest.mark.feature("EDT-19", criterion=2)
def test_deleting_a_mixed_block_removes_every_run_and_nothing_else() -> None:
    journal = _journal()
    index = _paragraph(journal).lines[0].style.span_index
    (result,) = journal.record(
        parse_op({"op": "delete_objects", "items": [{"kind": "text", "page_index": 0, "index": index}]})
    )
    assert result.note == "deleted 3 line(s)"
    assert [s.style.text for s in _spans(journal)] == ["Separate note"]


@pytest.mark.feature("EDT-19", criterion=2)
def test_a_pure_move_of_a_mixed_block_is_one_faithful_change() -> None:
    journal = _journal()
    block = _paragraph(journal)
    results = move_resize_block(journal.document, 0, block, dx=10, font_index=_font_index())
    assert len(results) == 1 and not results[0].requires_approval
    assert results[0].verification is not None and results[0].verification.looks_right


@pytest.mark.feature("EDT-19", criterion=2)
def test_rewrapping_a_mixed_block_says_which_runs_lose_their_style() -> None:
    journal = _journal()
    results = move_resize_block(journal.document, 0, _paragraph(journal), width=150, font_index=_font_index())
    assert results[-1].requires_approval
    assert "mixed styles: redrawn in the block's main style (Helvetica 12 pt), so 1 run(s) lost" in results[-1].note
    assert all(s.style.font == "Helvetica" for s in _spans(journal))


@pytest.mark.feature("EDT-19", criterion=2)
def test_rewrapping_a_plain_block_adds_no_style_note() -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    for i, text in enumerate(["One plain line here", "and another plain line"]):
        page.insert_text((72, 100 + 14 * i), text, fontname="helv", fontsize=12)
    document = Document.from_bytes(doc.tobytes())
    (block,) = detect_blocks(extract_page_spans(document.raw, 0))
    results = move_resize_block(document, 0, block, width=90, font_index=_font_index())
    assert all("mixed styles" not in r.note for r in results)


@pytest.mark.feature("EDT-19", criterion=2)
def test_editing_a_mixed_block_reflows_in_the_main_style_with_a_note() -> None:
    journal = _journal()
    block = next(u for u in text_units(_spans(journal), "block") if u.text == PARAGRAPH)
    results = journal.record(
        parse_op(
            {
                "op": "edit_text_unit",
                "page_index": 0,
                "unit": "block",
                "index": block.index,
                "expect_text": PARAGRAPH,
                "new_text": "One two three four five six seven",
            }
        )
    )
    assert results[-1].requires_approval and "1 run(s) lost their own font, size or color" in results[-1].note
    texts = [u.text for u in text_units(_spans(journal), "block")]
    assert texts == ["Separate note", "One two three four five six seven"] or texts == [
        "One two three four five six seven",
        "Separate note",
    ]


@pytest.mark.feature("EDT-19", criterion=2)
def test_reflow_of_a_block_fragmented_in_one_style_loses_nothing() -> None:
    """Pieces an earlier edit left, all in one style: re-wrapping them costs no style."""
    doc = pymupdf.open()
    page = doc.new_page()
    _runs(page, 100, [("Split ", "helv", (0, 0, 0)), ("into", "helv", (0, 0, 0)), (" pieces", "helv", (0, 0, 0))])
    page.insert_text((72, 114), "second line", fontname="helv", fontsize=12)
    document = Document.from_bytes(doc.tobytes())
    (block,) = detect_blocks(extract_page_spans(document.raw, 0))
    assert block.fragmented and not block.mixed_style
    results = reflow_block(document, 0, block, "New words here and there", font_index=_font_index())
    assert results and all("mixed styles" not in r.note for r in results)
    assert " ".join(s.style.text for s in extract_page_spans(document.raw, 0)) == "New words here and there"


def _hand_span(index: int, glyphs: list[tuple[str, float]], y: float = 100.0, font: str = "Helvetica") -> SpanTrace:
    """A span built by hand, one glyph per (char, x), each 5 pt wide and 10 pt tall."""
    chars = [CharBox(char=c, origin=(x, y), bbox=(x, y - 10, x + 5, y)) for c, x in glyphs]
    return SpanTrace(
        style=SpanStyle(
            page_index=0,
            span_index=index,
            text="".join(c for c, _x in glyphs),
            font=font,
            size=10.0,
            color=(0.0, 0.0, 0.0),
            opacity=1.0,
            bbox=(glyphs[0][1], y - 10, glyphs[-1][1] + 5, y),
            rotation_degrees=0.0,
            ascender=1.0,
            descender=0.0,
            chars=chars,
        ),
        text_state=None,
    )


@pytest.mark.feature("EDT-19", criterion=1)
def test_a_span_running_on_into_another_column_keeps_its_own_text() -> None:
    """One span whose glyphs sit in two columns (a wide TJ gap), with another run beside
    its first half: the line holding its start keeps it whole, in the spans' own text."""
    wide = _hand_span(0, [("a", 72), ("b", 77), ("c", 200), ("d", 205)])
    beside = _hand_span(1, [("Z", 82)], font="Helvetica-Bold")
    (block,) = detect_blocks([wide, beside])
    assert block.rows == ((wide, beside),)
    assert block.text == "abcdZ"


# -- review fixes: narrow gutters, and never stacking around a line ----------------------------


def _two_columns(gutter: float) -> UndoRedoJournal:
    """Two 10pt columns of six lines, written column by column, `gutter` pt apart."""
    doc = pymupdf.open()
    page = doc.new_page()
    left = [f"Left column line {i} text" for i in range(6)]
    right_x = 72 + max(pymupdf.get_text_length(t, fontname="helv", fontsize=10) for t in left) + gutter
    for i, text in enumerate(left):
        page.insert_text((72, 100 + 14 * i), text, fontname="helv", fontsize=10)
    for i in range(6):
        page.insert_text((right_x, 100 + 14 * i), f"Right col line {i}", fontname="helv", fontsize=10)
    return UndoRedoJournal(Document.from_bytes(doc.tobytes()))


@pytest.mark.feature("EDT-19", criterion=1)
@pytest.mark.parametrize("gutter", [10.0, 12.0, 14.0])
def test_columns_with_a_narrow_gutter_are_two_blocks(gutter: float) -> None:
    journal = _two_columns(gutter)
    blocks = PageBlocksOp(page_index=0).apply(journal.document)
    assert [b["span_indices"] for b in blocks] == [list(range(6)), list(range(6, 12))]
    units = text_units(_spans(journal), "block")
    assert [u.text.split(" line ")[0] for u in units] == ["Left column", "Right col"]


@pytest.mark.feature("EDT-19", criterion=1)
def test_reflowing_or_deleting_one_column_leaves_the_other_alone() -> None:
    journal = _two_columns(12.0)
    right = [(s.style.text, s.style.chars[0].origin) for s in _spans(journal) if s.style.text.startswith("Right")]
    journal.record(
        parse_op(
            {
                "op": "reflow_text",
                "page_index": 0,
                "match": "Left column line 0",
                "new_text": "Short new left text",
                "allow_overflow": True,
            }
        )
    )
    after = [(s.style.text, s.style.chars[0].origin) for s in _spans(journal) if s.style.text.startswith("Right")]
    assert after == right
    journal.undo()
    journal.record(parse_op({"op": "delete_objects", "items": [{"kind": "text", "page_index": 0, "index": 0}]}))
    left = [s.style.text for s in _spans(journal) if s.style.text.startswith("Left")]
    assert left == []
    assert [(s.style.text, s.style.chars[0].origin) for s in _spans(journal)] == right


@pytest.mark.feature("EDT-19", criterion=1)
def test_justified_text_with_wide_word_spacing_is_still_one_block() -> None:
    """Word spacing (Tw) widens gaps inside one span: that's justification, not a gutter."""
    lines = ["Justified text with", "rather wide word gaps", "set by word spacing"]
    body = " T* ".join(f"({text}) Tj" for text in lines)
    content = f"BT /helv 10 Tf 14 TL 9 Tw 1 0 0 1 72 700 Tm {body} ET"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_font(fontname="helv")
    xref = doc.get_new_xref()
    doc.update_object(xref, "<<>>")
    doc.update_stream(xref, content.encode("latin-1"))
    doc.xref_set_key(page.xref, "Contents", f"{xref} 0 R")
    spans = extract_page_spans(doc, 0)
    assert len(spans) == 3
    first = spans[0].style.chars
    assert first[10].bbox[0] - first[8].bbox[2] > 0.8 * 10  # a word gap wider than the gutter cut
    (block,) = detect_blocks(spans)
    assert block.text == " ".join(lines)


@pytest.mark.feature("EDT-19", criterion=1)
def test_a_restyled_middle_line_is_not_skipped_over() -> None:
    """The redrawn line goes to the end of the content stream; its paragraph's first and
    last lines, now adjacent in stream order, must not be stacked around it."""
    journal = _journal()
    line = next(u for u in text_units(_spans(journal), "line") if u.text.startswith("Delta"))
    journal.record(
        parse_op(
            {
                "op": "edit_text_unit",
                "page_index": 0,
                "unit": "line",
                "index": line.index,
                "expect_text": line.text,
                "size": 14,
            }
        )
    )
    texts = [u.text for u in text_units(_spans(journal), "block")]
    assert "Alpha Bravo Charlie Golf Hotel India" not in texts
    assert sorted(texts) == sorted(["Alpha Bravo Charlie", "Delta Echo Foxtrot", "Golf Hotel India", "Separate note"])
