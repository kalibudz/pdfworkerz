"""The document's own complete font (resolve Tier 0), name matching for vendor-suffixed
names, the fonts-to-research list, the user's font library, one-span-per-line extraction,
and the inspector's edit_span Op.

Found on a real bank statement: its fully embedded Delta-Book was never reused (every edit
fell to a look-alike), "Arial" never matched Windows' arial.ttf, keystroke previews flagged
fonts for research dozens of times, and every edit merged an untouched five-line address
block into one span. Every font and PDF here is built by the test itself.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pikepdf
import pymupdf
import pytest
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables._g_l_y_f import Glyph

from engine.document import Document
from engine.fonts import library, research
from engine.fonts.classify import classify_font
from engine.fonts.match import BUNDLED_FONTS_DIR, FontCandidate, build_font_index, find_by_name
from engine.fonts.resolve import TIER_EXACT, embedded_program_covers, resolve_font
from engine.fonts.style import extract_page_spans
from engine.ops.base import parse_op
from engine.ops.journal import UndoRedoJournal
from engine.ops.text import PreviewTextOp, _font_index

_POSTSCRIPT = "PWTestSans-Book"


def _test_font(*, fs_type: int = 0, blank: str = "", postscript: str = _POSTSCRIPT) -> bytes:
    """Bitstream Vera, renamed to a family no index contains, with a chosen embedding
    permission (OS/2 fsType) and some glyphs' outlines blanked."""
    tt = TTFont(BUNDLED_FONTS_DIR / "Vera.ttf")
    family = postscript.split("-")[0]
    for record in tt["name"].names:
        if record.nameID in (1, 16):
            record.string = family
        elif record.nameID in (2, 17):
            record.string = "Book"
        elif record.nameID in (4,):
            record.string = f"{family} Book"
        elif record.nameID == 6:
            record.string = postscript
    tt["OS/2"].fsType = fs_type
    cmap = tt.getBestCmap()
    for char in blank:
        tt["glyf"][cmap[ord(char)]] = Glyph()  # still mapped, but no outline
    buffer = io.BytesIO()
    tt.save(buffer)
    return buffer.getvalue()


def _pdf_with_font(path: Path, font: bytes, text: str = "Hello World 123") -> Path:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_font(fontname="F1", fontbuffer=font)
    page.insert_text((72, 100), text, fontname="F1", fontsize=14)
    doc.save(path)
    return path


def _resolve(path: Path, needed: str) -> object:
    with pikepdf.open(path) as pdf:
        classification = classify_font(pdf, 0, "F1")
    with pymupdf.open(path) as doc:
        xref = next(f[0] for f in doc[0].get_fonts(full=True) if f[4] == "F1")
        program = doc.extract_font(xref)[3]
    return resolve_font(
        classification,
        original_font_bytes=program,
        already_rendered_text="Hello World 123",
        needed_text=needed,
        font_index=build_font_index(include_system=False),
    )


# -- Tier 0: the document's own complete font --


@pytest.mark.feature("FNT-06")
def test_a_completely_embedded_unknown_font_is_reused_exactly(work_dir: Path) -> None:
    path = _pdf_with_font(work_dir / "full.pdf", _test_font())
    result = _resolve(path, "Quiz Jumps")  # letters the document never showed
    assert result.tier == TIER_EXACT
    assert result.requires_approval is False
    assert "own embedded font" in result.note


@pytest.mark.feature("FNT-06")
def test_mapped_but_blank_glyphs_are_not_drawn_from_the_embedded_font(work_dir: Path) -> None:
    """Some subsetters keep the whole cmap but blank unused glyphs (a real statement's
    Roboto did): reusing that program would draw nothing for those letters."""
    font = _test_font(blank="Qz")
    assert embedded_program_covers(font, "Hello") is True
    assert embedded_program_covers(font, "Quiz") is False
    path = _pdf_with_font(work_dir / "blank.pdf", font)
    assert _resolve(path, "Quiz").tier != TIER_EXACT  # not in any index: never "exact"


@pytest.mark.feature("FNT-06")
def test_a_restricted_license_font_is_never_reused(work_dir: Path) -> None:
    assert embedded_program_covers(_test_font(fs_type=0x0002), "Hello") is False


# -- Name matching --


def _candidate(postscript: str, family: str, subfamily: str) -> FontCandidate:
    return FontCandidate(Path(f"{postscript}.ttf"), family, subfamily, postscript, "system")


_ARIAL_FAMILY = [
    _candidate("ArialNarrow", "Arial Narrow", "Regular"),
    _candidate("Arial-BoldMT", "Arial", "Bold"),
    _candidate("ArialMT", "Arial", "Regular"),
    _candidate("Arial-ItalicMT", "Arial", "Italic"),
]


@pytest.mark.feature("FNT-06")
@pytest.mark.parametrize(
    ("base_font", "expected"),
    [
        ("Arial", "ArialMT"),
        ("ArialMT", "ArialMT"),
        ("DWHDKR+Arial", "ArialMT"),
        ("Arial-BoldMT", "Arial-BoldMT"),
        ("Arial,Bold", "Arial-BoldMT"),
        ("Arial-ItalicMT", "Arial-ItalicMT"),
        ("ArialNarrow", "ArialNarrow"),
    ],
)
def test_vendor_suffixed_and_plain_names_find_the_right_member(base_font: str, expected: str) -> None:
    found = find_by_name(_ARIAL_FAMILY, base_font)
    assert found is not None and found.postscript_name == expected


@pytest.mark.feature("FNT-06")
def test_a_book_style_name_counts_as_regular() -> None:
    fonts = [_candidate("DeltaJaeger-Bold", "Delta Jaeger", "Bold"), _candidate("Delta-Book", "Delta Jaeger", "Book")]
    found = find_by_name(fonts, "DeltaJaeger")
    assert found is not None and found.postscript_name == "Delta-Book"


# -- The fonts-to-research list --


def _unknown_font_pdf(work_dir: Path) -> Path:
    """Text in a font that is only named (not embedded) and installed nowhere."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Research me", fontname="helv", fontsize=14)
    doc.save(work_dir / "named.pdf")
    with pikepdf.open(work_dir / "named.pdf", allow_overwriting_input=True) as pdf:
        for font in pdf.pages[0].Resources.Font.values():
            font.BaseFont = pikepdf.Name("/NowhereSans-Regular")
        pdf.save(work_dir / "named.pdf")
    return work_dir / "named.pdf"


@pytest.mark.feature("FNT-06")
def test_a_preview_never_flags_a_font_but_an_applied_edit_does(work_dir: Path) -> None:
    journal = UndoRedoJournal(Document.open(_unknown_font_pdf(work_dir)))
    for text in ("R", "Re", "Res"):  # keystrokes
        preview = PreviewTextOp(page_index=0, span_index=0, needed_text=text).apply(journal.document)
        assert preview.tier != TIER_EXACT
    assert research.load() == []

    journal.record(
        parse_op(
            {"op": "edit_span", "page_index": 0, "span_index": 0, "new_text": "Researched", "require_tier": "fallback"}
        )
    )
    rows = research.load()
    assert [row.font for row in rows] == ["NowhereSans-Regular"]
    assert rows[0].times_seen == 1


@pytest.mark.feature("FNT-06")
def test_resolving_a_font_takes_it_off_the_list() -> None:
    research.flag("ABCDEF+SomeFont", tier="approximate", note="x", document="a.pdf")
    research.flag("OtherFont", tier="fallback", note="y", document="b.pdf")
    assert research.resolve("SomeFont") is True
    assert [row.font for row in research.load()] == ["OtherFont"]
    assert research.resolve("SomeFont") is False


# -- The user's font library --


@pytest.mark.feature("FNT-06")
def test_harvesting_a_complete_font_makes_it_an_exact_match_everywhere(work_dir: Path) -> None:
    source = _pdf_with_font(work_dir / "full.pdf", _test_font())
    research.flag(_POSTSCRIPT, tier="approximate", note="look-alike", document="full.pdf")
    with Document.open(source) as document:
        assert library.harvestable(document, _POSTSCRIPT) is None
        entry = library.harvest_from_document(document, _POSTSCRIPT)
    assert entry.postscript_name == _POSTSCRIPT
    assert entry.source == "embedded in full.pdf"
    assert (library.user_fonts_dir() / entry.file).is_file()
    assert (
        json.loads((library.user_fonts_dir() / entry.file).with_suffix(".json").read_text())["family"] == "PWTestSans"
    )
    assert research.load() == []  # added: no longer needs research
    assert [font.postscript_name for font in library.list_fonts()] == [_POSTSCRIPT]

    found = find_by_name(_font_index(), _POSTSCRIPT)  # the very next lookup sees it
    assert found is not None and found.source == "user"


@pytest.mark.feature("FNT-06")
def test_harvest_refuses_what_the_library_cannot_use(work_dir: Path) -> None:
    subset_doc = pymupdf.open()
    page = subset_doc.new_page()
    page.insert_font(fontname="F1", fontbuffer=_test_font())
    page.insert_text((72, 100), "Only these", fontname="F1", fontsize=14)
    subset_doc.subset_fonts()  # now tagged ABCDEF+..., holding only "Only these"
    subset_doc.save(work_dir / "subset.pdf")
    with Document.open(work_dir / "subset.pdf") as document:
        problem = library.harvestable(document, _POSTSCRIPT)
        assert problem is not None and "subset" in problem

    blank = _pdf_with_font(work_dir / "blank.pdf", _test_font(blank="QZqz"))
    with Document.open(blank) as document:
        problem = library.harvestable(document, _POSTSCRIPT)
        assert problem is not None and "not completely" in problem

    restricted = _pdf_with_font(work_dir / "restricted.pdf", _test_font(fs_type=0x0002))
    with Document.open(restricted) as document, pytest.raises(library.FontLibraryError, match="restricted"):
        library.harvest_from_document(document, _POSTSCRIPT)

    with Document.open(blank) as document, pytest.raises(library.FontLibraryError, match="no font named"):
        library.harvest_from_document(document, "NotHere")
    assert library.list_fonts() == []


@pytest.mark.feature("FNT-06")
def test_adding_a_font_file(work_dir: Path) -> None:
    path = work_dir / "mine.ttf"
    path.write_bytes(_test_font(postscript="PWMine-Regular"))
    entry = library.add_font_file(path)
    assert entry.postscript_name == "PWMine-Regular"
    assert entry.source == str(path)
    assert find_by_name(_font_index(), "PWMine-Regular") is not None
    (work_dir / "mine.pdf").write_bytes(b"%PDF-1.7")
    with pytest.raises(library.FontLibraryError, match=r"not a \.ttf"):
        library.add_font_file(work_dir / "mine.pdf")
    library.remove_font("PWMine-Regular")
    assert find_by_name(_font_index(), "PWMine-Regular") is None


# -- One span per line --


def _td_lines_pdf(path: Path) -> Path:
    """Five lines in one text object, moved with TD and T* -- the form PyMuPDF's content
    cleaning writes, and some generators write to begin with."""
    doc = pymupdf.open()
    doc.new_page()
    doc.save(path)
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        page = pdf.pages[0]
        helvetica = pdf.make_indirect(
            pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica)
        )
        page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=helvetica))
        page.Contents = pdf.make_stream(
            b"BT /F1 12 Tf 14 TL 72 700 Td (First line) Tj T* (Second line) Tj 0 -14 TD (Third line) Tj ET"
        )
        pdf.save(path)
    return path


@pytest.mark.feature("FNT-01")
def test_lines_joined_by_line_moves_come_back_as_separate_spans(work_dir: Path) -> None:
    with pymupdf.open(_td_lines_pdf(work_dir / "td.pdf")) as doc:
        texts = [span.style.text for span in extract_page_spans(doc, 0)]
    assert texts == ["First line", "Second line", "Third line"]


@pytest.mark.feature("FNT-12")
def test_an_edit_leaves_other_lines_as_they_were(work_dir: Path) -> None:
    journal = UndoRedoJournal(Document.open(_td_lines_pdf(work_dir / "td.pdf")))
    result = journal.record(
        parse_op(
            {"op": "edit_span", "page_index": 0, "span_index": 1, "new_text": "Second row", "require_tier": "fallback"}
        )
    )
    assert result.verification.looks_right
    spans = sorted(extract_page_spans(journal.document.raw, 0), key=lambda span: span.style.bbox[1])
    assert [span.style.text for span in spans] == ["First line", "Second row", "Third line"]  # top to bottom


# -- edit_span: text and style in one step --


@pytest.mark.feature("EDT-06")
def test_edit_span_changes_text_and_style_in_one_undoable_step(work_dir: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Plain words", fontname="helv", fontsize=12)
    doc.save(work_dir / "plain.pdf")
    journal = UndoRedoJournal(Document.open(work_dir / "plain.pdf"))

    result = journal.record(
        parse_op(
            {"op": "edit_span", "page_index": 0, "span_index": 0, "new_text": "Bold words", "bold": True, "size": 14}
        )
    )
    assert result.verification.looks_right
    span = extract_page_spans(journal.document.raw, 0)[0]
    assert span.style.text == "Bold words"
    assert "bold" in span.style.font.lower()
    assert span.style.size == pytest.approx(14, abs=0.1)
    assert len(journal.history) == 1

    journal.undo()
    span = extract_page_spans(journal.document.raw, 0)[0]
    assert (span.style.text, span.style.size) == ("Plain words", pytest.approx(12, abs=0.1))


@pytest.mark.feature("EDT-06")
def test_edit_span_refuses_a_change_that_changes_nothing(work_dir: Path) -> None:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "Same", fontname="helv", fontsize=12)
    doc.save(work_dir / "same.pdf")
    journal = UndoRedoJournal(Document.open(work_dir / "same.pdf"))
    with pytest.raises(Exception, match="change the text or"):
        journal.record(parse_op({"op": "edit_span", "page_index": 0, "span_index": 0, "new_text": "Same"}))
