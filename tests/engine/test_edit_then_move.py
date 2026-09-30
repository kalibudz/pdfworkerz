"""Text redrawn by an edit must keep a name later Ops can find it by.

Found in live use: after editing a figure, moving it failed or fell to a look-alike font.
An edit registered its font under PyMuPDF's full name ("Arial Regular") while texttrace
reported the PostScript name ("ArialMT"), so the next move could not find the resource;
and a nameless embedded program came back as "(null)", which can't be looked up at all.
Every PDF here is built by the test.
"""

from __future__ import annotations

import io
from pathlib import Path

import pymupdf
import pytest
from fontTools import subset
from fontTools.ttLib import TTFont

from engine.document import Document
from engine.fonts.match import BUNDLED_FONTS_DIR
from engine.fonts.style import extract_page_spans
from engine.ops.base import parse_op
from engine.ops.journal import UndoRedoJournal

_VERA = BUNDLED_FONTS_DIR / "Vera.ttf"


def _nameless_subset(text: str) -> bytes:
    """What some PDF generators embed: a subset that keeps its cmap but has no name table."""
    tt = TTFont(_VERA)
    options = subset.Options()
    options.drop_tables += ["name"]
    subsetter = subset.Subsetter(options)
    subsetter.populate(text=text)
    subsetter.subset(tt)
    if "name" in tt:
        del tt["name"]
    buffer = io.BytesIO()
    tt.save(buffer)
    return buffer.getvalue()


def _journal(build: callable) -> UndoRedoJournal:  # type: ignore[valid-type]
    doc = pymupdf.open()
    build(doc.new_page())
    return UndoRedoJournal(Document.from_bytes(doc.tobytes()))


def _record(journal: UndoRedoJournal, **op: object) -> list:
    result = journal.record(parse_op({"require_tier": "exact", **op}))
    return result if isinstance(result, list) else [result]


def _span(journal: UndoRedoJournal, text: str) -> int:
    spans = extract_page_spans(journal.document.raw, 0)
    return next(i for i, span in enumerate(spans) if span.style.text == text)


_ARIAL = Path("C:/Windows/Fonts/arial.ttf")


@pytest.mark.feature("EDT-05")
@pytest.mark.parametrize(
    "font_file",
    [
        _VERA,
        # Arial's PostScript name ("ArialMT") differs from the full name PyMuPDF
        # registers ("Arial Regular") -- the case that failed in live use.
        pytest.param(_ARIAL, marks=pytest.mark.skipif(not _ARIAL.exists(), reason="Windows' Arial not installed")),
    ],
    ids=["vera", "arial"],
)
def test_a_type0_subset_can_be_edited_then_moved_restyled_and_painted_exactly(font_file: Path) -> None:
    def build(page: pymupdf.Page) -> None:
        page.insert_font(fontname="F1", fontbuffer=font_file.read_bytes())
        page.insert_text((72, 100), "65,914.74", fontname="F1", fontsize=10)
        page.insert_text((72, 140), "Label", fontname="F1", fontsize=10)

    journal = _journal(build)
    journal.document.raw.subset_fonts()  # a Type0 subset, as generators write them
    _record(journal, op="edit_span", page_index=0, span_index=_span(journal, "65,914.74"), new_text="99,000.00")
    edited = extract_page_spans(journal.document.raw, 0)[_span(journal, "99,000.00")]
    assert edited.style.font not in ("", "(null)")

    moved = _record(journal, op="move_text_block", page_index=0, span_index=_span(journal, "99,000.00"), dy=20)
    assert [result.tier for result in moved] == ["exact"]
    restyled = _record(journal, op="restyle_span", page_index=0, span_index=_span(journal, "99,000.00"), size=12)
    assert [result.tier for result in restyled] == ["exact"]
    painted = _record(
        journal,
        op="copy_style",
        page_index=0,
        span_index=_span(journal, "99,000.00"),
        target_page_index=0,
        target_span_index=_span(journal, "Label"),
    )
    assert [result.tier for result in painted] == ["exact"]


@pytest.mark.feature("EDT-05")
def test_text_in_a_nameless_embedded_font_keeps_a_name_through_edit_and_move() -> None:
    program = _nameless_subset("0123456789,. TOTAL:")

    def build(page: pymupdf.Page) -> None:
        page.insert_font(fontname="F1", fontbuffer=program)
        page.insert_text((72, 100), "TOTAL: 1,629.72", fontname="F1", fontsize=10)

    journal = _journal(build)
    _record(journal, op="edit_span", page_index=0, span_index=0, new_text="TOTAL: 1,692.27")
    font = extract_page_spans(journal.document.raw, 0)[0].style.font
    assert font not in ("", "(null)")
    moved = _record(journal, op="move_text_block", page_index=0, span_index=0, dx=15)
    assert [result.tier for result in moved] == ["exact"]
    assert moved[0].verification.looks_right


@pytest.mark.feature("EDT-05")
def test_a_move_finds_the_right_font_among_same_named_subsets() -> None:
    """Two nameless subsets on one page, each holding different characters: the edited
    line's move must draw with the font that has its glyphs."""
    digits = _nameless_subset("0123456789,.")
    letters = _nameless_subset("ABCDEFGHIJKLMNOPQRSTUVWXYZ ")

    def build(page: pymupdf.Page) -> None:
        page.insert_font(fontname="F1", fontbuffer=letters)
        page.insert_text((72, 100), "HEADING TEXT", fontname="F1", fontsize=10)
        page.insert_font(fontname="F2", fontbuffer=digits)
        page.insert_text((72, 140), "1,234.56", fontname="F2", fontsize=10)

    journal = _journal(build)
    moved = _record(journal, op="move_text_block", page_index=0, span_index=_span(journal, "1,234.56"), dy=10)
    assert [result.tier for result in moved] == ["exact"]
    assert moved[0].verification.looks_right


@pytest.mark.feature("FNT-06")
def test_a_nameless_subset_with_no_cmap_is_edited_exactly_from_its_tounicode() -> None:
    """Generators often embed Type0 subsets with neither a name nor a cmap -- nothing says
    which glyph is which character. Their ToUnicode map, read backwards, does: characters
    the document already shows are drawn from its own glyphs, exactly."""
    digits = _nameless_subset("0123456789,.")
    letters = _nameless_subset("ABCDEFGHIJKLMNOPQRSTUVWXYZ ")
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_font(fontname="L", fontbuffer=letters)
    page.insert_text((72, 100), "PLANTERS", fontname="L", fontsize=11)
    page.insert_font(fontname="D", fontbuffer=digits)
    page.insert_text((300, 100), "3,480.50", fontname="D", fontsize=11)
    doc.subset_fonts()  # now cmap-less Type0 subsets, as generators write them
    journal = UndoRedoJournal(Document.from_bytes(doc.tobytes()))

    for old, new in (("3,480.50", "3,408.50"), ("PLANTERS", "PLANTER")):
        (edit,) = _record(journal, op="edit_span", page_index=0, span_index=_span(journal, old), new_text=new)
        assert (edit.tier, edit.verification.looks_right) == ("exact", True)
        moved = _record(journal, op="move_text_block", page_index=0, span_index=_span(journal, new), dy=12)
        assert [result.tier for result in moved] == ["exact"]

    # A digit the subset never held has no exact glyph: that is refused, not faked.
    with pytest.raises(Exception, match=r"approximate|fallback"):
        _record(journal, op="edit_span", page_index=0, span_index=_span(journal, "3,408.50"), new_text="3,408.57")


def test_fixture_font_really_is_nameless(tmp_path: Path) -> None:
    assert "name" not in TTFont(io.BytesIO(_nameless_subset("123")))
