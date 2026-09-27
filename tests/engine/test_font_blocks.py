"""FNT-11 (block detection half): engine.fonts.blocks."""

from __future__ import annotations

import pymupdf
import pytest

from engine.fonts.blocks import TextBlock, detect_blocks, find_block_containing
from engine.fonts.style import extract_page_spans
from tests.corpus.build_corpus import Corpus


@pytest.mark.feature("FNT-11")
def test_three_consecutive_lines_form_one_block(corpus: Corpus) -> None:
    with pymupdf.open(corpus.paragraph) as doc:
        spans = extract_page_spans(doc, 0)
        blocks = detect_blocks(spans)

    assert len(blocks) == 2
    assert len(blocks[0].lines) == 3
    assert len(blocks[1].lines) == 1


@pytest.mark.feature("FNT-11")
def test_block_text_joins_lines_with_spaces(corpus: Corpus) -> None:
    with pymupdf.open(corpus.paragraph) as doc:
        spans = extract_page_spans(doc, 0)
        blocks = detect_blocks(spans)

    assert blocks[0].text == (
        "This is line one of a paragraph. This is line two continuing on. And this is line three, the last."
    )
    assert blocks[1].text == "A separate paragraph starts here."


@pytest.mark.feature("FNT-11")
def test_a_much_larger_gap_starts_a_new_block(corpus: Corpus) -> None:
    """The second paragraph sits 71.2pt below the first block's last line --
    far more than a normal line-spacing gap at 12pt -- and must not merge."""
    with pymupdf.open(corpus.paragraph) as doc:
        spans = extract_page_spans(doc, 0)
        blocks = detect_blocks(spans)

    assert blocks[1].lines[0].style.text == "A separate paragraph starts here."


@pytest.mark.feature("FNT-11")
def test_a_single_line_page_is_one_one_line_block(corpus: Corpus) -> None:
    with pymupdf.open(corpus.simple) as doc:
        spans = extract_page_spans(doc, 0)
        blocks = detect_blocks(spans)

    assert len(blocks) == 1
    assert len(blocks[0].lines) == 1


@pytest.mark.feature("FNT-11")
def test_an_empty_page_produces_no_blocks() -> None:
    doc = pymupdf.open()
    doc.new_page()
    spans = extract_page_spans(doc, 0)
    assert detect_blocks(spans) == []
    doc.close()


@pytest.mark.feature("FNT-11")
def test_different_font_sizes_do_not_merge_into_one_block() -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Heading text here", fontsize=18, fontname="helv")
    page.insert_text((72, 118), "Body text right below it", fontsize=12, fontname="helv")
    spans = extract_page_spans(doc, 0)
    blocks = detect_blocks(spans)
    assert len(blocks) == 2
    doc.close()


@pytest.mark.feature("FNT-11")
def test_different_left_margins_do_not_merge_into_one_block() -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Flush left line", fontsize=12, fontname="helv")
    page.insert_text((150, 114.4), "Indented next line", fontsize=12, fontname="helv")
    spans = extract_page_spans(doc, 0)
    blocks = detect_blocks(spans)
    assert len(blocks) == 2
    doc.close()


@pytest.mark.feature("FNT-11")
def test_rotated_text_never_merges_into_a_block(work_dir) -> None:
    import math

    import pikepdf

    pdf = pikepdf.new()
    page = pdf.add_blank_page()
    font = pdf.make_indirect(
        pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name("/Helvetica"))
    )
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
    angle = math.radians(90)
    c, s = math.cos(angle), math.sin(angle)
    content = (
        f"BT\n/F1 12 Tf\n{c:.6f} {s:.6f} {-s:.6f} {c:.6f} 300 400 Tm\n(Rotated one) Tj\nET\n"
        f"BT\n/F1 12 Tf\n{c:.6f} {s:.6f} {-s:.6f} {c:.6f} 314.4 400 Tm\n(Rotated two) Tj\nET\n"
    ).encode("ascii")
    page.Contents = pdf.make_stream(content)
    path = work_dir / "rotated_pair.pdf"
    pdf.save(path)
    pdf.close()

    with pymupdf.open(path, filetype="pdf") as doc:
        spans = extract_page_spans(doc, 0)
        blocks = detect_blocks(spans)
    assert len(blocks) == 2


@pytest.mark.feature("FNT-11")
def test_find_block_containing_locates_the_right_block(corpus: Corpus) -> None:
    with pymupdf.open(corpus.paragraph) as doc:
        spans = extract_page_spans(doc, 0)
        blocks = detect_blocks(spans)

    second_line_of_first_block = blocks[0].lines[1]
    found = find_block_containing(blocks, second_line_of_first_block)
    assert found is blocks[0]


@pytest.mark.feature("FNT-11")
def test_find_block_containing_returns_none_for_a_foreign_span(corpus: Corpus) -> None:
    with pymupdf.open(corpus.paragraph) as doc:
        spans = extract_page_spans(doc, 0)
    blocks: list[TextBlock] = []  # deliberately empty
    assert find_block_containing(blocks, spans[0]) is None
