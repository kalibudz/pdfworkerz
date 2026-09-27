"""EDT-01/02/03/04/06: the typed text-editing Ops."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pikepdf
import pytest

from engine.document import Document
from engine.errors import OpValidationError
from engine.ops.base import parse_op
from engine.ops.text import DeleteTextOp, InsertTextOp, ReplaceTextOp, RestyleTextOp
from tests.corpus.build_corpus import Corpus


@pytest.fixture
def simple_doc(corpus: Corpus, work_dir: Path) -> Iterator[Document]:
    source = work_dir / "simple.pdf"
    shutil.copy(corpus.simple, source)
    doc = Document.open(source)
    yield doc
    doc.close()


@pytest.mark.feature("EDT-02")
def test_replace_text_op_literal_match(simple_doc: Document) -> None:
    op = ReplaceTextOp(match="PDFWorkerz", replacement="Editor", require_tier="exact")
    results = op.apply(simple_doc)
    assert len(results) == 1
    assert results[0].tier == "exact"
    assert simple_doc.raw[0].get_text().strip() == "Hello, Editor."


@pytest.mark.feature("EDT-02")
def test_replace_text_op_regex_match(simple_doc: Document) -> None:
    op = ReplaceTextOp(match=r"PDF\w+", replacement="App", mode="regex")
    op.apply(simple_doc)
    assert simple_doc.raw[0].get_text().strip() == "Hello, App."


@pytest.mark.feature("EDT-02")
def test_replace_text_op_case_insensitive(simple_doc: Document) -> None:
    op = ReplaceTextOp(match="pdfworkerz", replacement="X", case_sensitive=False)
    op.apply(simple_doc)
    assert simple_doc.raw[0].get_text().strip() == "Hello, X."


@pytest.mark.feature("EDT-02")
def test_replace_text_op_no_match_is_a_no_op(simple_doc: Document) -> None:
    op = ReplaceTextOp(match="NotInTheDocument", replacement="X")
    results = op.apply(simple_doc)
    assert results == []
    assert simple_doc.raw[0].get_text().strip() == "Hello, PDFWorkerz."


@pytest.mark.feature("EDT-02")
def test_replace_text_op_multiple_occurrences_on_one_page(work_dir: Path) -> None:
    path = work_dir / "repeated.pdf"
    pdf = pikepdf.new()
    page = pdf.add_blank_page()
    font = pdf.make_indirect(
        pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name("/Helvetica"))
    )
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
    page.Contents = pdf.make_stream(b"BT\n/F1 12 Tf\n1 0 0 1 72 700 Tm\n(cat and cat and cat) Tj\nET\n")
    pdf.save(path)
    pdf.close()

    doc = Document.open(path)
    op = ReplaceTextOp(match="cat", replacement="dog")
    results = op.apply(doc)
    assert len(results) == 3
    assert doc.raw[0].get_text().strip() == "dog and dog and dog"
    doc.close()


@pytest.mark.feature("EDT-02")
def test_replace_text_op_require_tier_rejects_a_weak_match(corpus: Corpus, work_dir: Path) -> None:
    """An *embedded* font (FontFile2 present) whose name matches nothing available
    must fall through to a weaker tier, which require_tier="exact" then rejects.
    Renamed from a real embedded fixture so `embedded` stays true -- a bare Type1
    dict with no FontFile is (correctly) treated as a non-embedded standard font
    instead, which always resolves "exact" and wouldn't exercise this path."""
    path = work_dir / "unmatchable.pdf"
    with pikepdf.open(corpus.embedded_font_subset) as pdf:
        font = pdf.pages[0].Resources.Font["/EmbeddedVeraBold"]
        font["/BaseFont"] = pikepdf.Name("/XYZUNK+TotallyUnknownFontXYZ")
        font["/DescendantFonts"][0]["/BaseFont"] = pikepdf.Name("/XYZUNK+TotallyUnknownFontXYZ")
        pdf.save(path)

    doc = Document.open(path)
    op = ReplaceTextOp(match="AB", replacement="world", require_tier="exact")
    with pytest.raises(OpValidationError, match="fell back to tier"):
        op.apply(doc)
    doc.close()


@pytest.mark.feature("EDT-02")
def test_replace_text_op_round_trips_through_json() -> None:
    op = ReplaceTextOp(match="a", replacement="b", mode="regex", case_sensitive=False, page_index=2)
    restored = parse_op(op.model_dump())
    assert restored == op


@pytest.mark.feature("EDT-04")
def test_delete_text_op(simple_doc: Document) -> None:
    op = DeleteTextOp(match="PDFWorkerz")
    results = op.apply(simple_doc)
    assert len(results) == 1
    assert simple_doc.raw[0].get_text().strip() == "Hello, ."


@pytest.mark.feature("EDT-04")
def test_delete_text_op_regex(simple_doc: Document) -> None:
    op = DeleteTextOp(match=r",\s*PDFWorkerz", mode="regex")
    op.apply(simple_doc)
    assert simple_doc.raw[0].get_text().strip() == "Hello."


@pytest.mark.feature("EDT-04")
def test_delete_text_op_no_match_is_a_no_op(simple_doc: Document) -> None:
    results = DeleteTextOp(match="nope").apply(simple_doc)
    assert results == []


@pytest.mark.feature("EDT-06")
def test_restyle_text_op_changes_color_not_text(simple_doc: Document) -> None:
    op = RestyleTextOp(match="PDFWorkerz", color=(1.0, 0.0, 0.0))
    results = op.apply(simple_doc)
    assert len(results) == 1
    assert simple_doc.raw[0].get_text().strip() == "Hello, PDFWorkerz."

    from engine.fonts.style import extract_page_spans

    spans = extract_page_spans(simple_doc.raw, 0)
    assert any(s.style.color == pytest.approx((1.0, 0.0, 0.0)) for s in spans)


@pytest.mark.feature("EDT-06")
def test_restyle_text_op_changes_size(simple_doc: Document) -> None:
    op = RestyleTextOp(match="PDFWorkerz", size=30.0)
    op.apply(simple_doc)

    from engine.fonts.style import extract_page_spans

    spans = extract_page_spans(simple_doc.raw, 0)
    assert any(s.style.size == pytest.approx(30.0) for s in spans)


@pytest.mark.feature("EDT-06")
def test_restyle_text_op_requires_at_least_one_field(simple_doc: Document) -> None:
    with pytest.raises(OpValidationError, match="at least one of size or color"):
        RestyleTextOp(match="PDFWorkerz").apply(simple_doc)


@pytest.mark.feature("EDT-03")
def test_insert_text_op(simple_doc: Document) -> None:
    op = InsertTextOp(page_index=0, text=" Extra", position=(300, 72), reference_match="PDFWorkerz")
    result = op.apply(simple_doc)
    assert result.tier == "exact"
    text = simple_doc.raw[0].get_text()
    assert "Hello, PDFWorkerz." in text
    assert "Extra" in text


@pytest.mark.feature("EDT-03")
def test_insert_text_op_raises_when_reference_not_found(simple_doc: Document) -> None:
    op = InsertTextOp(page_index=0, text="x", position=(0, 0), reference_match="NoSuchText")
    with pytest.raises(OpValidationError, match="no span matches"):
        op.apply(simple_doc)


@pytest.mark.feature("EDT-03")
def test_insert_text_op_round_trips_through_json() -> None:
    op = InsertTextOp(page_index=1, text="hi", position=(10.5, 20.5), reference_match="ref")
    restored = parse_op(op.model_dump())
    assert restored == op
