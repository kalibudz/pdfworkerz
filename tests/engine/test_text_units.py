"""EDT-16: engine.fonts.units (lines, words, blocks) and the page_text_units Op."""

from __future__ import annotations

import pymupdf
import pytest

from engine.document import Document
from engine.fonts.style import CharBox, SpanStyle, SpanTrace, extract_page_spans
from engine.fonts.units import (
    Segment,
    TextUnit,
    find_unit,
    group_blocks,
    group_lines,
    split_words,
    text_units,
)
from engine.ops import PageTextUnitsOp, parse_op
from engine.ops.objects import PageBlocksOp
from tests.corpus.build_corpus import Corpus

# -- fixtures ---------------------------------------------------------------------------


def _run(page: pymupdf.Page, x: float, y: float, text: str, font: str = "helv", size: float = 12) -> float:
    """Draw one style run at (x, y) and return the x just past it."""
    page.insert_text((x, y), text, fontsize=size, fontname=font)
    return x + pymupdf.get_text_length(text, fontname=font, fontsize=size)


def _raw_page(content: str) -> pymupdf.Document:
    """A one-page document whose content stream is exactly `content` (font /helv)."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_font(fontname="helv")
    xref = doc.get_new_xref()
    doc.update_object(xref, "<<>>")
    doc.update_stream(xref, content.encode("latin-1"))
    doc.xref_set_key(page.xref, "Contents", f"{xref} 0 R")
    return doc


def _synthetic(index: int, text: str, x: float, y: float, size: float = 10.0, advance: float = 5.0) -> SpanTrace:
    """A span built by hand, one glyph every `advance` points -- for characters base-14
    fonts can't produce through texttrace (MuPDF reports U+00A0 as a plain space)."""
    chars = [
        CharBox(char=c, origin=(x + i * advance, y), bbox=(x + i * advance, y - size, x + (i + 1) * advance, y))
        for i, c in enumerate(text)
    ]
    return SpanTrace(
        style=SpanStyle(
            page_index=0,
            span_index=index,
            text=text,
            font="Helvetica",
            size=size,
            color=(0.0, 0.0, 0.0),
            opacity=1.0,
            bbox=(x, y - size, x + len(text) * advance, y),
            rotation_degrees=0.0,
            ascender=1.0,
            descender=0.0,
            chars=chars,
        ),
        text_state=None,
    )


def _texts(units: list[TextUnit]) -> list[str]:
    return [unit.text for unit in units]


# -- criterion 1: lines -------------------------------------------------------------------


@pytest.mark.feature("EDT-16", criterion=1)
def test_mixed_style_runs_on_one_baseline_are_one_line() -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    x = _run(page, 72, 100, "Hello ")
    x = _run(page, x, 100, "bold", font="hebo")
    _run(page, x, 100, " world.")
    spans = extract_page_spans(doc, 0)
    assert len(spans) == 3

    (line,) = group_lines(spans)
    assert line.text == "Hello bold world."
    assert line.segments == (Segment(0, 0, 6), Segment(1, 0, 4), Segment(2, 0, 7))
    assert line.span_indices == [0, 1, 2]
    assert line.origin == spans[0].style.chars[0].origin
    assert line.rotation_degrees == 0.0 and line.size == 12
    assert line.bbox[0] == pytest.approx(72) and line.bbox[2] == pytest.approx(spans[2].style.bbox[2])


@pytest.mark.feature("EDT-16", criterion=1)
def test_a_superscript_and_a_subscript_stay_on_their_line() -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    x = _run(page, 72, 130, "E = mc")
    x = _run(page, x, 126, "2", size=7)  # raised 4pt: a third of the body size
    x = _run(page, x, 130, " and H")
    x = _run(page, x, 132.5, "2", size=7)
    _run(page, x, 130, "O")
    lines = group_lines(extract_page_spans(doc, 0))
    assert [line.text for line in lines] == ["E = mc2 and H2O"]
    assert lines[0].size == 12
    # The line's origin is its first glyph's, on the body text's baseline.
    assert lines[0].origin == (72.0, 130.0)


@pytest.mark.feature("EDT-16", criterion=1)
def test_two_columns_on_one_baseline_are_two_lines_numbered_left_to_right() -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    for y in (100, 114):
        _run(page, 72, y, "Left column text")
        _run(page, 300, y, "Right column text")
    lines = group_lines(extract_page_spans(doc, 0))
    assert [line.text for line in lines] == ["Left column text", "Right column text"] * 2
    assert [line.index for line in lines] == [0, 1, 2, 3]


@pytest.mark.feature("EDT-16", criterion=1)
def test_a_justified_gap_under_the_column_ratio_does_not_split_the_line() -> None:
    size = 10.0
    spans = [
        _synthetic(0, "wide", 72, 100, size=size),
        _synthetic(1, "gap", 72 + 20 + 1.5 * size, 100, size=size),  # 1.5x: justify's maximum stretch
        _synthetic(2, "column", 72 + 20 + 1.5 * size + 15 + 1.7 * size, 100, size=size),  # 1.7x: a new column
    ]
    lines = group_lines(spans)
    assert [line.text for line in lines] == ["wide gap", "column"]
    assert lines[0].glyph_map[4] is None  # the synthetic space where "wide" meets "gap"


@pytest.mark.feature("EDT-16", criterion=1)
def test_rotated_text_is_its_own_line_after_the_upright_ones() -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((500, 400), "Rotated text", fontsize=12, fontname="helv", rotate=90)
    _run(page, 72, 400, "Upright text")
    spans = extract_page_spans(doc, 0)
    lines = group_lines(spans)
    assert [(line.text, line.rotation_degrees) for line in lines] == [("Upright text", 0.0), ("Rotated text", -90.0)]
    words = split_words(lines)
    assert [w.text for w in words] == ["Upright", "text", "Rotated", "text"]
    # Reading bottom to top: "Rotated" sits below "text" on the page.
    assert words[2].bbox[1] > words[3].bbox[3] - 0.5


@pytest.mark.feature("EDT-16", criterion=1)
def test_a_line_placed_one_glyph_at_a_time_is_one_line() -> None:
    """Some writers (and fit-to-width output) place every glyph with its own Tm, so MuPDF
    reports one span per character."""
    parts = []
    x = 72.0
    for char in "Fitted":
        parts.append(f"BT /helv 12 Tf 1 0 0 1 {x:.3f} 700 Tm ({char}) Tj ET")
        x += pymupdf.get_text_length(char, fontname="helv", fontsize=12) + 0.3
    with _raw_page("\n".join(parts)) as doc:
        spans = extract_page_spans(doc, 0)
    assert len(spans) == 6
    (line,) = group_lines(spans)
    assert line.text == "Fitted"
    assert line.segments == tuple(Segment(i, 0, 1) for i in range(6))
    (word,) = split_words([line])
    assert word.text == "Fitted" and word.span_indices == list(range(6))


@pytest.mark.feature("EDT-16", criterion=1)
def test_lines_are_deterministic_whatever_the_span_order() -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    x = _run(page, 72, 100, "One ")
    _run(page, x, 100, "two", font="hebo")
    _run(page, 72, 130, "E = mc")
    _run(page, 300, 130, "Other column")
    spans = extract_page_spans(doc, 0)
    first = group_lines(spans)
    assert group_lines(spans) == first
    assert group_lines(list(reversed(spans))) == first
    for granularity in ("block", "line", "word"):
        assert text_units(spans, granularity) == text_units(spans, granularity)


@pytest.mark.feature("EDT-16", criterion=1)
def test_empty_spans_belong_to_no_line_and_half_turns_group_together() -> None:
    blank = _synthetic(0, "", 72, 100)
    upside_a = _synthetic(1, "ab", 300, 300).model_copy(
        update={"style": _synthetic(1, "ab", 300, 300).style.model_copy(update={"rotation_degrees": 180.0})}
    )
    upside_b = _synthetic(2, "cd", 310, 300).model_copy(
        update={"style": _synthetic(2, "cd", 310, 300).style.model_copy(update={"rotation_degrees": -180.0})}
    )
    assert group_lines([blank]) == []
    (block,) = text_units([blank], "block")  # a block still lists it, anchored at its box
    assert block.origin == (72.0, 100.0) and block.segments == [Segment(0, 0, 0)]
    lines = group_lines([blank, upside_a, upside_b])
    assert len(lines) == 1 and lines[0].rotation_degrees == 180.0


# -- criterion 2: words -------------------------------------------------------------------


@pytest.mark.feature("EDT-16", criterion=2)
def test_words_cross_style_runs_and_keep_their_punctuation() -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    x = _run(page, 72, 100, "Say mc")
    x = _run(page, x, 96, "2", size=7)
    x = _run(page, x, 100, ", please")
    _run(page, x, 100, "!", font="hebo")
    spans = extract_page_spans(doc, 0)
    lines = group_lines(spans)
    words = split_words(lines)
    assert [w.text for w in words] == ["Say", "mc2,", "please!"]
    mc2 = words[1]
    assert mc2.segments == (Segment(0, 4, 6), Segment(1, 0, 1), Segment(2, 0, 1))
    assert mc2.span_indices == [0, 1, 2]
    assert lines[0].text[mc2.line_start : mc2.line_end] == "mc2,"
    assert all(w.line_index == 0 for w in words) and [w.index for w in words] == [0, 1, 2]
    assert mc2.origin == spans[0].style.chars[4].origin


@pytest.mark.feature("EDT-16", criterion=2)
def test_tj_gaps_become_spaces_but_kerning_does_not() -> None:
    content = "BT /helv 12 Tf 1 0 0 1 72 650 Tm [(Hello) -300 (world,) -40 (AV) 80 (A)] TJ ET"
    with _raw_page(content) as doc:
        spans = extract_page_spans(doc, 0)
    (line,) = group_lines(spans)
    assert line.text == "Hello world,AVA"
    assert line.glyph_map[5] is None and line.glyph_map[6] == (0, 5)
    assert [w.text for w in split_words([line])] == ["Hello", "world,AVA"]


@pytest.mark.feature("EDT-16", criterion=2)
def test_a_no_break_space_separates_words_and_is_never_doubled() -> None:
    nbsp = chr(0xA0)
    (line,) = group_lines([_synthetic(0, f"ten{nbsp}km  away", 72, 100)])
    assert line.text == f"ten{nbsp}km  away"  # real space glyphs: nothing synthetic added
    assert None not in line.glyph_map
    assert [w.text for w in split_words([line])] == ["ten", "km", "away"]


@pytest.mark.feature("EDT-16", criterion=2)
def test_a_real_space_glyph_beside_a_gap_adds_no_synthetic_space() -> None:
    spans = [_synthetic(0, "one ", 72, 100), _synthetic(1, "two", 72 + 20 + 8, 100)]
    (line,) = group_lines(spans)
    assert line.text == "one two"
    assert [w.text for w in split_words([line])] == ["one", "two"]


# -- criterion 3: blocks, the Op and re-finding ---------------------------------------------


@pytest.mark.feature("EDT-16", criterion=3)
def test_blocks_match_page_blocks_exactly(corpus: Corpus) -> None:
    for path in (corpus.paragraph, corpus.simple):
        document = Document.open(path)
        try:
            expected = PageBlocksOp(page_index=0).apply(document)
            units = PageTextUnitsOp(page_index=0, granularity="block").apply(document)
            spans = extract_page_spans(document.raw, 0)
        finally:
            document.close()
        assert [u.span_indices for u in units] == [b["span_indices"] for b in expected]
        assert [u.index for u in units] == [b["span_indices"][0] for b in expected]
        assert [u.bbox for u in units] == [tuple(b["bbox"]) for b in expected]
        assert [g.span_indices for g in group_blocks(spans)] == [tuple(b["span_indices"]) for b in expected]
        for unit in units:
            assert unit.line_index is None and unit.granularity == "block"
            assert unit.segments == [Segment(i, 0, len(spans[i].style.chars)) for i in unit.span_indices]


@pytest.mark.feature("EDT-16", criterion=3)
def test_the_op_is_registered_and_defaults_to_lines(corpus: Corpus) -> None:
    op = parse_op({"op": "page_text_units", "page_index": 0})
    assert isinstance(op, PageTextUnitsOp) and op.granularity == "line"
    document = Document.open(corpus.paragraph)
    try:
        lines = op.apply(document)
        words = parse_op({"op": "page_text_units", "granularity": "word"}).apply(document)
    finally:
        document.close()
    assert _texts(lines)[0] == "This is line one of a paragraph."
    assert all(u.granularity == "line" and u.line_index is None for u in lines)
    assert words[0].text == "This" and words[0].line_index == 0
    assert {w.line_index for w in words} == set(range(len(lines)))


@pytest.mark.feature("EDT-16", criterion=3)
def test_find_unit_refinds_a_unit_after_renumbering() -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    _run(page, 72, 200, "Keep this line")
    before = text_units(extract_page_spans(doc, 0), "line")
    target = before[0]
    _run(page, 72, 100, "A new first line")  # sorts above: every line index shifts
    after = text_units(extract_page_spans(doc, 0), "line")
    found = find_unit(after, "line", target.origin, target.text)
    assert found is not None and found.index == 1 and found.text == "Keep this line"
    assert find_unit(after, "line", target.origin, "Changed text") is None
    assert find_unit(after, "word", target.origin, target.text) is None
    assert find_unit(after, "line", (target.origin[0] + 1, target.origin[1]), target.text) is None
