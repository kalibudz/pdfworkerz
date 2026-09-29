"""FNT-17: justified reflow, and moving later content when a paragraph grows."""

from __future__ import annotations

import shutil
from pathlib import Path

import pymupdf
import pytest

from engine.document import Document
from engine.errors import OpValidationError
from engine.ops.base import parse_op
from engine.ops.journal import UndoRedoJournal
from tests.corpus.build_corpus import Corpus

LONG = (
    "Revenue grew steadily across every region this quarter, driven by strong demand for the "
    "new product line and a careful expansion of the partner network into two new markets."
)


def _lines(page: pymupdf.Page) -> list[tuple[float, str]]:
    """Each visual line: (baseline y, words in reading order)."""
    rows: dict[float, list[tuple[float, str]]] = {}
    for x0, _y0, _x1, y1, word, *_ in page.get_text("words"):
        rows.setdefault(round(y1, 0), []).append((x0, word))
    return [(y, " ".join(w for _x, w in sorted(words))) for y, words in sorted(rows.items())]


def _paragraph_doc(
    tmp_path: Path, *, below: str = "A separate paragraph starts here.", below_y: float = 200
) -> Document:
    doc = pymupdf.open()
    page = doc.new_page()
    for i, line in enumerate(
        ["The first line of a paragraph that", "spans three lines at the left", "margin of the page."]
    ):
        page.insert_text((72, 100 + i * 14.4), line, fontsize=12, fontname="helv")
    page.insert_text((72, below_y), below, fontsize=12, fontname="helv")
    doc.save(tmp_path / "p.pdf")
    return Document.open(tmp_path / "p.pdf")


def _reflow(doc: Document, **fields: object) -> object:
    op = parse_op({"op": "reflow_text", "match": "first line", "page_index": 0, "require_tier": "fallback", **fields})
    op.check_pages(doc)
    return op.apply(doc)


@pytest.mark.feature("FNT-17")
def test_justified_lines_reach_the_right_edge_and_the_last_line_does_not(tmp_path: Path) -> None:
    doc = _paragraph_doc(tmp_path)
    text = "Each line of this justified text ends at one right edge but the last one."
    _reflow(doc, new_text=text, align="justify")
    words = doc.raw[0].get_text("words")
    block = [w for w in words if w[3] < 140]
    by_line: dict[float, list[tuple[float, ...]]] = {}
    for w in block:
        by_line.setdefault(round(w[3], 0), []).append(w)
    rows = [sorted(ws, key=lambda w: w[0]) for _y, ws in sorted(by_line.items())]
    assert len(rows) == 3
    right_edges = [row[-1][2] for row in rows]
    assert abs(right_edges[0] - right_edges[1]) < 1.0  # the justified lines end together
    assert right_edges[2] < right_edges[0] - 20  # the last line stays ragged
    assert all(abs(row[0][0] - 72) < 1 for row in rows)  # and all start at the margin
    assert " ".join(w[4] for row in rows for w in row) == text
    assert "A separate paragraph starts here." in doc.raw[0].get_text()


@pytest.mark.feature("FNT-17")
def test_each_justified_word_stays_searchable(tmp_path: Path) -> None:
    doc = _paragraph_doc(tmp_path)
    _reflow(
        doc, new_text="Justified words should each remain one searchable piece of text on the page.", align="justify"
    )
    assert doc.raw[0].search_for("searchable")
    assert doc.raw[0].search_for("Justified")


@pytest.mark.feature("FNT-17")
def test_growing_a_paragraph_moves_the_text_below_it_down(tmp_path: Path) -> None:
    doc = _paragraph_doc(tmp_path)
    results = _reflow(doc, new_text=LONG, grow=True)
    lines = _lines(doc.raw[0])
    paragraph = [text for _y, text in lines if "separate" not in text]
    assert " ".join(paragraph) == LONG
    assert len(paragraph) > 3
    extra = len(paragraph) - 3
    (below_y,) = [y for y, text in lines if "separate" in text]
    assert below_y == pytest.approx(200 + 3 + 14.4 * extra, abs=2)  # moved down by the extra lines' pitch
    assert lines[-2][0] < below_y  # nothing overlaps: the paragraph ends above the moved text
    assert "moved 1 line(s) below it down" in results[-1].note  # type: ignore[index]


@pytest.mark.feature("FNT-17")
def test_growing_and_justifying_together(tmp_path: Path) -> None:
    doc = _paragraph_doc(tmp_path)
    _reflow(doc, new_text=LONG, grow=True, align="justify")
    rows: dict[float, list[tuple[float, ...]]] = {}
    for w in doc.raw[0].get_text("words"):
        if "separate" not in w[4] and w[3] < 190 + 60:
            rows.setdefault(round(w[3], 0), []).append(w)
    edges = [max(w[2] for w in ws) for _y, ws in sorted(rows.items())]
    assert len({round(e) for e in edges[:-2]}) == 1  # all but the last paragraph line (and the moved one) align


@pytest.mark.feature("FNT-17")
def test_growing_is_refused_when_text_would_leave_the_page(tmp_path: Path) -> None:
    doc = _paragraph_doc(tmp_path, below="Footer line at the very bottom.", below_y=835)
    before = doc.raw[0].get_text()
    with pytest.raises(OpValidationError, match="off the bottom of the page"):
        _reflow(doc, new_text=LONG, grow=True)
    assert doc.raw[0].get_text() == before  # nothing changed


@pytest.mark.feature("FNT-17")
def test_growing_is_refused_past_images_or_drawings_below(tmp_path: Path) -> None:
    doc = _paragraph_doc(tmp_path)
    doc.raw[0].draw_line((72, 180), (500, 180))  # a rule between the paragraphs
    with pytest.raises(OpValidationError, match="drawings"):
        _reflow(doc, new_text=LONG, grow=True)
    # left alone, the old bounded behavior still reports the overflow instead
    with pytest.raises(OpValidationError, match="overflow"):
        _reflow(doc, new_text=LONG)


@pytest.mark.feature("FNT-17")
def test_text_in_another_column_is_not_moved(tmp_path: Path) -> None:
    doc = _paragraph_doc(tmp_path)
    doc.raw[0].insert_text((420, 250), "Sidebar", fontsize=12, fontname="helv")
    _reflow(doc, new_text=LONG, grow=True)
    (sidebar,) = doc.raw[0].search_for("Sidebar")
    assert sidebar.y1 == pytest.approx(253, abs=2)


@pytest.mark.feature("FNT-17")
def test_grown_paragraph_undoes_in_one_step(corpus: Corpus, tmp_path: Path) -> None:
    path = tmp_path / "paragraph.pdf"
    shutil.copy(corpus.paragraph, path)
    journal = UndoRedoJournal(Document.open(path))
    before = journal.document.raw[0].get_text()
    journal.record(
        parse_op(
            {
                "op": "reflow_text",
                "match": "line one",
                "page_index": 0,
                "new_text": LONG,
                "grow": True,
                "align": "justify",
                "require_tier": "fallback",
            }
        )
    )
    assert "separate paragraph" in journal.document.raw[0].get_text()
    journal.undo()
    assert journal.document.raw[0].get_text() == before
    journal.document.close()


@pytest.mark.feature("FNT-17")
def test_growing_is_refused_when_new_lines_would_cover_an_image(tmp_path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    for i, line in enumerate(
        ["The first line of a paragraph that", "spans three lines at the left", "margin of the page."]
    ):
        page.insert_text((72, 100 + i * 14.4), line, fontsize=12, fontname="helv")
    page.draw_rect(pymupdf.Rect(72, 140, 300, 200), color=(1, 0, 0), fill=(1, 0, 0))  # nothing else below
    doc.save(tmp_path / "img.pdf")
    document = Document.open(tmp_path / "img.pdf")
    with pytest.raises(OpValidationError, match="over drawings"):
        _reflow(document, new_text=LONG, grow=True)
