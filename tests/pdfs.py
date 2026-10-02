"""Text-PDF fixtures for the P7 conversion tests: a ruled table, and a small report with
headings, emphasis, a list, a table, a picture and a running header and footer -- everything the
converters have to carry across, with text a test can look for."""

from __future__ import annotations

import io

import pymupdf
from PIL import Image

TABLE_ROWS = [
    ["Item", "Qty", "Price"],
    ["Widget, large", "3", "1,250.00"],
    ['Gadget "pro"', "12", "(45.50)"],
    ["Line\nbreak", "007", "-8"],
]


def draw_table(
    page: pymupdf.Page,
    rows: list[list[str]],
    *,
    origin: tuple[float, float] = (72, 120),
    col: float = 120,
    row: float = 26,
) -> pymupdf.Rect:
    """A table with ruling lines on ``page``; returns its outline."""
    x0, y0 = origin
    cols = max(len(r) for r in rows)
    box = pymupdf.Rect(x0, y0, x0 + col * cols, y0 + row * len(rows))
    for i in range(len(rows) + 1):
        page.draw_line((x0, y0 + i * row), (box.x1, y0 + i * row))
    for j in range(cols + 1):
        page.draw_line((x0 + j * col, y0), (x0 + j * col, box.y1))
    for i, cells in enumerate(rows):
        for j, text in enumerate(cells):
            page.insert_text((x0 + j * col + 5, y0 + i * row + 17), text.replace("\n", " "), fontsize=11)
    return box


def table_pdf(rows: list[list[str]] | None = None) -> bytes:
    doc = pymupdf.open()
    draw_table(doc.new_page(), rows or TABLE_ROWS)
    return doc.tobytes()


def _picture() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (160, 100), (200, 40, 40)).save(buffer, format="PNG")
    return buffer.getvalue()


def report_pdf(*, pages: int = 2) -> bytes:
    """Each page: a running header and footer; page one adds a title, a heading, a paragraph with
    bold and italic runs, a bullet list, a numbered list, a picture and a table."""
    doc = pymupdf.open()
    for number in range(1, pages + 1):
        page = doc.new_page()
        page.insert_text((72, 40), "ACME Corp confidential", fontsize=9)
        page.insert_text((290, 770), f"Page {number}", fontsize=9)
        if number > 1:
            page.insert_text((72, 120), f"Appendix {number}", fontsize=18, fontname="hebo")
            page.insert_text((72, 150), "Closing remarks for the appendix.", fontsize=11)
            continue
        page.insert_text((72, 90), "Quarterly Report", fontsize=26, fontname="hebo")
        page.insert_text((72, 130), "Results", fontsize=18, fontname="hebo")
        page.insert_text((72, 160), "Revenue grew ", fontsize=11)
        page.insert_text((140, 160), "strongly", fontsize=11, fontname="hebo")
        page.insert_text((190, 160), " and costs stayed ", fontsize=11)
        page.insert_text((275, 160), "flat", fontsize=11, fontname="heit")
        page.insert_text((300, 160), " this quarter.", fontsize=11)
        page.insert_text((80, 190), "• First bullet point", fontsize=11)
        page.insert_text((80, 206), "• Second bullet point", fontsize=11)
        page.insert_text((80, 230), "1. Step one", fontsize=11)
        page.insert_text((80, 246), "2. Step two", fontsize=11)
        page.insert_image(pymupdf.Rect(72, 270, 172, 332), stream=_picture())
        draw_table(page, TABLE_ROWS[:3], origin=(72, 360))
    return doc.tobytes()
