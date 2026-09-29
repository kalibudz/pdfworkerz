"""EDT-03 explicit style, EDT-06 font and weight changes, and the fonts-to-research list."""

from __future__ import annotations

from pathlib import Path

import pikepdf
import pymupdf
import pytest

from engine.document import Document
from engine.errors import OpValidationError
from engine.fonts import research
from engine.fonts.choose import available_families, resolve_chosen_font
from engine.fonts.style import extract_page_spans
from engine.ops.text import InsertTextOp, ReplaceSpanTextOp, RestyleSpanOp, RestyleTextOp, _font_index


def _one_line(path: Path, text: str = "Quarterly report", fontname: str = "helv") -> Path:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), text, fontsize=12, fontname=fontname)
    doc.save(path)
    return path


def _styles(doc: Document) -> list[tuple[str, str, float]]:
    return [(s.style.text, s.style.font, round(s.style.size, 1)) for s in extract_page_spans(doc.raw, 0)]


@pytest.mark.feature("EDT-06")
def test_available_families_lists_the_standard_and_bundled_families() -> None:
    families = available_families(_font_index())
    assert families[:3] == ["Helvetica", "Times", "Courier"]
    assert "Bitstream Vera Sans" in families and "Roboto" in families


@pytest.mark.feature("EDT-06")
def test_an_unavailable_family_or_style_is_refused_with_the_choices() -> None:
    with pytest.raises(OpValidationError, match="choose one of: Helvetica, Times, Courier"):
        resolve_chosen_font("No Such Family", bold=False, italic=False, text="x", font_index=_font_index())
    with pytest.raises(OpValidationError, match="Roboto has no bold italic style"):
        resolve_chosen_font("Roboto", bold=True, italic=True, text="x", font_index=_font_index())


@pytest.mark.feature("EDT-06")
def test_restyle_span_makes_text_bold_in_its_own_family(tmp_path: Path) -> None:
    doc = Document.open(_one_line(tmp_path / "a.pdf"))
    result = RestyleSpanOp(page_index=0, span_index=0, bold=True).apply(doc)
    assert _styles(doc) == [("Quarterly report", "Helvetica-Bold", 12.0)]
    assert result.tier == "exact" and "chosen by the user" in result.note
    doc.close()


@pytest.mark.feature("EDT-06")
def test_restyle_span_changes_the_font_family_size_and_color_together(tmp_path: Path) -> None:
    doc = Document.open(_one_line(tmp_path / "a.pdf"))
    RestyleSpanOp(page_index=0, span_index=0, font="Bitstream Vera Sans", italic=True, size=16, color=(1, 0, 0)).apply(
        doc
    )
    (span,) = extract_page_spans(doc.raw, 0)
    assert span.style.text == "Quarterly report"
    assert "Oblique" in span.style.font and "Vera" in span.style.font
    assert span.style.size == pytest.approx(16, abs=0.1)
    assert span.style.color == pytest.approx((1, 0, 0), abs=0.01)
    doc.close()


@pytest.mark.feature("EDT-06")
def test_restyle_text_changes_the_weight_of_every_match(tmp_path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Total due", fontsize=12, fontname="tiro")
    page.insert_text((72, 130), "Total paid", fontsize=12, fontname="tiro")
    doc.save(tmp_path / "b.pdf")
    document = Document.open(tmp_path / "b.pdf")
    results = RestyleTextOp(match="Total", bold=True).apply(document)
    assert len(results) == 2
    assert {font for _text, font, _size in _styles(document)} == {"Times-Bold"}
    document.close()


@pytest.mark.feature("EDT-06")
def test_restyle_needs_something_to_change(tmp_path: Path) -> None:
    doc = Document.open(_one_line(tmp_path / "a.pdf"))
    with pytest.raises(OpValidationError, match="set at least one"):
        RestyleSpanOp(page_index=0, span_index=0).apply(doc)
    doc.close()


@pytest.mark.feature("EDT-03")
def test_insert_text_in_an_explicit_style_needs_no_reference(tmp_path: Path) -> None:
    doc = Document.open(_one_line(tmp_path / "a.pdf"))
    result = InsertTextOp(
        page_index=0, text="Approved", position=(72, 200), font="Roboto", size=18, color=(0, 0.5, 0)
    ).apply(doc)
    added = [s.style for s in extract_page_spans(doc.raw, 0) if s.style.text == "Approved"]
    assert len(added) == 1
    assert "Roboto" in added[0].font and added[0].size == pytest.approx(18, abs=0.1)
    assert added[0].chars[0].origin == pytest.approx((72, 200), abs=0.5)
    assert result.verification is not None and result.verification.looks_right
    doc.close()


@pytest.mark.feature("EDT-03")
def test_insert_text_can_match_a_style_and_override_parts_of_it(tmp_path: Path) -> None:
    doc = Document.open(_one_line(tmp_path / "a.pdf", fontname="tiro"))
    InsertTextOp(page_index=0, text="Draft", position=(72, 200), reference_match="Quarterly", bold=True, size=20).apply(
        doc
    )
    added = [s.style for s in extract_page_spans(doc.raw, 0) if s.style.text == "Draft"]
    assert added[0].font == "Times-Bold" and added[0].size == pytest.approx(20, abs=0.1)
    doc.close()


@pytest.mark.feature("EDT-03")
def test_insert_text_without_a_reference_needs_font_and_size(tmp_path: Path) -> None:
    doc = Document.open(_one_line(tmp_path / "a.pdf"))
    with pytest.raises(OpValidationError, match="explicit font and size"):
        InsertTextOp(page_index=0, text="x", position=(72, 200), font="Helvetica").apply(doc)
    doc.close()


# -- fonts to research (owner's rule, 2026-09-28) --


def _unknown_font_pdf(path: Path) -> Path:
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))
    font = pdf.make_indirect(
        pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name("/MysteryGrotesk"))
    )
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
    page.Contents = pdf.make_stream(b"BT /F1 12 Tf 72 700 Td (Hello) Tj ET BT /F1 12 Tf 72 600 Td (World) Tj ET")
    pdf.save(path)
    return path


@pytest.mark.feature("FNT-06")
def test_a_font_that_cannot_be_matched_exactly_is_flagged_for_research(tmp_path: Path) -> None:
    doc = Document.open(_unknown_font_pdf(tmp_path / "mystery.pdf"))
    # Two lines in the same unknown font. (Editing the *same* line twice flags it once: after
    # the first edit that text is drawn in the fallback font, which then matches exactly.)
    ReplaceSpanTextOp(page_index=0, span_index=0, new_text="Howdy", require_tier="fallback").apply(doc)
    world = [s.style.text for s in extract_page_spans(doc.raw, 0)].index("World")
    ReplaceSpanTextOp(page_index=0, span_index=world, new_text="Earth", require_tier="fallback").apply(doc)
    (row,) = research.load()
    assert row.font == "MysteryGrotesk" and row.best_tier == "fallback"
    assert row.times_seen == 2 and row.example_document == "mystery.pdf"
    doc.close()


@pytest.mark.feature("FNT-06")
def test_an_exact_match_is_not_flagged(tmp_path: Path) -> None:
    doc = Document.open(_one_line(tmp_path / "a.pdf"))
    ReplaceSpanTextOp(page_index=0, span_index=0, new_text="Annual report", require_tier="exact").apply(doc)
    assert research.load() == []
    doc.close()


@pytest.mark.feature("FNT-06")
def test_the_research_list_lives_in_the_data_folder_and_can_be_cleared(tmp_path: Path) -> None:
    research.flag("ABCDEF+Obscure-Bold", tier="approximate", note="closest metric match: x.ttf", document="d.pdf")
    assert research.research_file().parent == research.data_dir()
    assert [r.font for r in research.load()] == ["Obscure-Bold"]  # subset tag removed
    research.clear()
    assert research.load() == []
