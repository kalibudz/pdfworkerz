"""XTR-03: find tables on pages and export them (CSV here; Excel in engine.export_office).

Detection is PyMuPDF's: tables drawn with ruling lines are found by those lines, and a table
laid out with spaces only by the gaps between its columns. ``"auto"`` tries the lines first
(precise) and falls back to the gaps only on a page where it found no ruled table (a looser
guess, so used only when the precise one found nothing).
"""

from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import pymupdf

from engine.document import Document
from engine.errors import ConversionError

Strategy = Literal["auto", "lines", "text"]
_MIN_TEXT_TABLE = (2, 2)  # rows, columns a gap-based table must have: fewer is a paragraph, not a table


@dataclass(frozen=True)
class Table:
    """One detected table."""

    page_index: int
    number: int
    """1-based among the tables of that page, top to bottom."""
    bbox: tuple[float, float, float, float]
    rows: list[list[str]]

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def column_count(self) -> int:
        return max((len(row) for row in self.rows), default=0)

    @property
    def label(self) -> str:
        return f"page {self.page_index + 1}, table {self.number}"


def _clean(cell: object) -> str:
    return " ".join(str(cell).split("\n")).strip() if cell is not None else ""


def _find(page: pymupdf.Page, strategy: str, clip: pymupdf.Rect | None) -> list[pymupdf.table.Table]:
    found = page.find_tables(strategy=strategy, clip=clip)
    return list(found.tables)


def find_page_tables(
    page: pymupdf.Page, *, strategy: Strategy = "auto", region: tuple[float, float, float, float] | None = None
) -> list[Table]:
    """The tables on one page, top to bottom, optionally only inside ``region``
    (x0, y0, x1, y1 in points)."""
    clip = pymupdf.Rect(region) if region else None
    if clip is not None and (clip.is_empty or clip.is_infinite):
        raise ConversionError("the region to search for tables is empty")
    raw = [] if strategy == "text" else _find(page, "lines", clip)
    spaced = not raw and strategy != "lines"  # no ruled table: look for columns of aligned text
    if spaced:
        raw = _find(page, "text", clip)
    raw.sort(key=lambda t: (t.bbox[1], t.bbox[0]))
    tables: list[Table] = []
    for table in raw:
        rows = [[_clean(cell) for cell in row] for row in table.extract()]
        if spaced:  # gaps between lines of text come back as blank rows
            rows = [row for row in rows if any(row)]
            if len(rows) < _MIN_TEXT_TABLE[0] or max(len(r) for r in rows) < _MIN_TEXT_TABLE[1]:
                continue
        if any(any(cell for cell in row) for row in rows):
            number = len(tables) + 1
            tables.append(Table(page.number or 0, number, tuple(table.bbox), rows))
    return tables


def find_tables(
    document: Document,
    page_indices: Sequence[int] | None = None,
    *,
    strategy: Strategy = "auto",
    region: tuple[float, float, float, float] | None = None,
) -> list[Table]:
    """The tables on the given pages (all by default), in page order."""
    indices = range(document.page_count) if page_indices is None else page_indices
    return [
        table for index in indices for table in find_page_tables(document.raw[index], strategy=strategy, region=region)
    ]


def table_to_csv(table: Table, *, bom: bool = True) -> bytes:
    """One table as CSV: cells with commas, quotes or line breaks are quoted, quotes doubled.
    ``bom`` adds the marker that makes Excel read it as UTF-8 (accents and all)."""
    out = io.StringIO()
    csv.writer(out, lineterminator="\r\n").writerows(table.rows)
    return (b"\xef\xbb\xbf" if bom else b"") + out.getvalue().encode("utf-8")


def tables_to_files(tables: Sequence[Table], *, bom: bool = True) -> list[tuple[str, bytes]]:
    """(file name, CSV bytes) for each table: ``page-2-table-1.csv``."""
    return [(f"page-{t.page_index + 1}-table-{t.number}.csv", table_to_csv(t, bom=bom)) for t in tables]


def tables_to_zip(tables: Sequence[Table], *, bom: bool = True) -> bytes:
    """Every table as its own CSV, in one zip."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in tables_to_files(tables, bom=bom):
            archive.writestr(name, data)
    return buffer.getvalue()
