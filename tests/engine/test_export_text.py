"""CVF-05 / CVF-06: PDF to text, Markdown and HTML."""

from __future__ import annotations

import base64
import re
from html.parser import HTMLParser

import pymupdf
import pytest

from engine.document import Document
from engine.errors import OpValidationError
from engine.export_text import to_html, to_markdown, to_text
from engine.pagerange import parse_page_range
from tests import pdfs


@pytest.fixture(scope="module")
def report() -> Document:
    return Document.from_bytes(pdfs.report_pdf())


@pytest.mark.feature("CVF-05", criterion=1)
def test_text_export_is_in_reading_order_with_page_breaks_marked(report: Document) -> None:
    text = to_text(report)
    order = [
        text.index(s)
        for s in (
            "--- Page 1 ---",
            "Quarterly Report",
            "Results",
            "Step one",
            "Item\tQty\tPrice",
            "--- Page 2 ---",
            "Appendix 2",
        )
    ]
    assert order == sorted(order)
    assert "Revenue grew strongly and costs stayed flat this quarter." in text


@pytest.mark.feature("CVF-05", criterion=2)
def test_markdown_marks_headings_emphasis_lists_and_tables(report: Document) -> None:
    md = to_markdown(report, skip_running=True)
    assert "# Quarterly Report" in md and "## Results" in md
    assert re.search(r"^# Quarterly Report$", md, re.M), "a heading carries no bold marks of its own"
    assert "Revenue grew **strongly** and costs stayed *flat* this quarter." in md
    assert "- First bullet point\n- Second bullet point" in md
    assert "1. Step one\n2. Step two" in md
    assert "| Item | Qty | Price |\n| --- | --- | --- |\n| Widget, large | 3 | 1,250.00 |" in md


@pytest.mark.feature("CVF-05", criterion=2)
def test_markdown_escapes_what_would_be_read_as_markup() -> None:
    raw = pymupdf.open()
    raw.new_page().insert_text((72, 100), "Use *stars* and _under_ and [links] here", fontsize=11)
    md = to_markdown(Document.from_bytes(raw.tobytes()))
    assert r"Use \*stars\* and \_under\_ and \[links\] here" in md


@pytest.mark.feature("CVF-05", criterion=3)
def test_running_headers_footers_and_page_numbers_can_be_left_out(report: Document) -> None:
    kept, dropped = to_text(report), to_text(report, skip_running=True)
    assert "ACME Corp confidential" in kept and "Page 2" in kept
    assert "ACME Corp confidential" not in dropped
    assert not re.search(r"^Page \d+$", dropped, re.M)
    assert "Quarterly Report" in dropped, "real content stays"


@pytest.mark.feature("CVF-05", criterion=4)
def test_a_page_range_limits_the_export(report: Document) -> None:
    text = to_text(report, parse_page_range("2", report.page_count))
    assert "Appendix 2" in text and "Quarterly Report" not in text and "--- Page 1 ---" not in text
    assert "Quarterly" not in to_markdown(report, [1])


@pytest.mark.feature("CVF-05", criterion=4)
def test_page_ranges_are_read_the_way_people_write_them() -> None:
    assert parse_page_range("1-3,5", 6) == [0, 1, 2, 4]
    assert parse_page_range("4-", 6) == [3, 4, 5]
    assert parse_page_range("-2", 6) == [0, 1]
    assert parse_page_range(None, 3) == [0, 1, 2]
    assert parse_page_range("3,1", 3) == [2, 0]
    for bad in ("0", "7", "3-2", "a", "1,,2"):
        with pytest.raises(OpValidationError):
            parse_page_range(bad, 6)


class _Tags(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.seen: list[str] = []
        self.images: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.seen.append(tag)
        if tag == "img":
            self.images.append(dict(attrs)["src"] or "")


def _parsed(html: str) -> _Tags:
    parser = _Tags()
    parser.feed(html)
    return parser


@pytest.mark.feature("CVF-06", criterion=1)
def test_html_has_real_elements_in_one_file(report: Document) -> None:
    html = to_html(report, skip_running=True)
    tags = _parsed(html).seen
    for expected in ("h1", "h2", "p", "strong", "em", "ul", "ol", "li", "table", "th", "td", "section"):
        assert expected in tags, expected
    assert html.startswith("<!DOCTYPE html>") and "<title>" in html
    assert "<link" not in html and "<script" not in html and 'src="http' not in html


@pytest.mark.feature("CVF-06", criterion=2)
def test_pictures_are_embedded_in_the_file(report: Document) -> None:
    [source] = _parsed(to_html(report)).images
    assert source.startswith("data:image/png;base64,")
    assert base64.b64decode(source.split(",", 1)[1])[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.feature("CVF-06", criterion=3)
def test_text_in_the_pdf_can_never_become_markup() -> None:
    raw = pymupdf.open()
    raw.new_page().insert_text((72, 100), "<script>alert(1)</script> & <b>x</b>", fontsize=11)
    html = to_html(Document.from_bytes(raw.tobytes()), title="<i>t</i>")
    assert "<script>" not in html and "&lt;script&gt;alert(1)&lt;/script&gt; &amp; &lt;b&gt;x&lt;/b&gt;" in html
    assert "<title>&lt;i&gt;t&lt;/i&gt;</title>" in html


@pytest.mark.feature("CVF-06", criterion=4)
def test_html_export_honours_a_page_range(report: Document) -> None:
    html = to_html(report, [1])
    assert 'data-page="2"' in html and 'data-page="1"' not in html and "Quarterly" not in html
