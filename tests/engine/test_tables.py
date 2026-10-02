"""XTR-03: table detection and CSV export."""

from __future__ import annotations

import csv
import io
import zipfile

import pymupdf
import pytest

from engine.document import Document
from engine.errors import ConversionError
from engine.tables import find_page_tables, find_tables, table_to_csv, tables_to_files, tables_to_zip
from tests import pdfs


def _document(*tables: list[list[str]]) -> Document:
    raw = pymupdf.open()
    for rows in tables:
        pdfs.draw_table(raw.new_page(), rows)
    return Document.from_bytes(raw.tobytes())


@pytest.mark.feature("XTR-03", criterion=1)
def test_a_ruled_table_is_detected_with_its_box_and_size() -> None:
    document = Document.from_bytes(pdfs.table_pdf())
    [table] = find_tables(document)
    assert (table.page_index, table.number) == (0, 1)
    assert (table.row_count, table.column_count) == (4, 3)
    assert table.rows[1] == ["Widget, large", "3", "1,250.00"]
    x0, y0, x1, y1 = table.bbox
    assert (round(x0), round(y0), round(x1), round(y1)) == (72, 120, 432, 224)
    assert table.label == "page 1, table 1"


@pytest.mark.feature("XTR-03", criterion=1)
def test_text_without_ruling_lines_is_not_taken_for_a_table() -> None:
    raw = pymupdf.open()
    raw.new_page().insert_text((72, 100), "Just a sentence of ordinary prose, nothing tabular here.", fontsize=11)
    assert find_tables(Document.from_bytes(raw.tobytes())) == []


@pytest.mark.feature("XTR-03", criterion=2)
def test_csv_quotes_commas_quotes_and_keeps_the_text_in_every_cell() -> None:
    [table] = find_tables(Document.from_bytes(pdfs.table_pdf()))
    data = table_to_csv(table)
    assert data.startswith(b"\xef\xbb\xbf")
    parsed = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
    assert parsed == [[cell.replace("\n", " ") for cell in row] for row in pdfs.TABLE_ROWS]
    assert '"Widget, large"' in data.decode("utf-8-sig")
    assert '"Gadget ""pro"""' in data.decode("utf-8-sig")
    assert table_to_csv(table, bom=False)[:4] == b"Item"


@pytest.mark.feature("XTR-03", criterion=2)
def test_a_cell_with_a_line_break_survives_a_round_trip() -> None:
    from engine.tables import Table

    table = Table(0, 1, (0, 0, 1, 1), [["a", "two\nlines"], ['q"', "c,d"]])
    assert list(csv.reader(io.StringIO(table_to_csv(table, bom=False).decode()))) == table.rows


@pytest.mark.feature("XTR-03", criterion=3)
def test_several_tables_export_as_one_file_each() -> None:
    document = _document(pdfs.TABLE_ROWS, [["A", "B"], ["1", "2"]])
    tables = find_tables(document)
    assert [name for name, _ in tables_to_files(tables)] == ["page-1-table-1.csv", "page-2-table-1.csv"]
    with zipfile.ZipFile(io.BytesIO(tables_to_zip(tables))) as archive:
        assert sorted(archive.namelist()) == ["page-1-table-1.csv", "page-2-table-1.csv"]
        assert archive.read("page-2-table-1.csv").decode("utf-8-sig") == "A,B\r\n1,2\r\n"


@pytest.mark.feature("XTR-03", criterion=4)
def test_detection_can_be_limited_to_a_region_of_the_page() -> None:
    raw = pymupdf.open()
    page = raw.new_page()
    pdfs.draw_table(page, [["Top", "Left"], ["1", "2"]], origin=(72, 100))
    pdfs.draw_table(page, [["Low", "Right"], ["3", "4"]], origin=(72, 400))
    document = Document.from_bytes(raw.tobytes())
    assert len(find_tables(document)) == 2
    [lower] = find_tables(document, region=(0, 350, 612, 600))
    assert lower.rows[0] == ["Low", "Right"]
    with pytest.raises(ConversionError, match="empty"):
        find_page_tables(document.raw[0], region=(10, 10, 10, 10))


@pytest.mark.feature("XTR-03", criterion=1)
def test_a_table_laid_out_with_spaces_only_is_found_when_the_lines_find_nothing() -> None:
    raw = pymupdf.open()
    page = raw.new_page()
    for i, row in enumerate(
        [["Name", "Dept", "Score"], ["Ann", "Sales", "91"], ["Bob", "Ops", "78"], ["Cy", "HR", "85"]]
    ):
        for j, cell in enumerate(row):
            page.insert_text((72 + j * 130, 100 + i * 20), cell, fontsize=11)
    [table] = find_tables(Document.from_bytes(raw.tobytes()))
    assert table.rows[1] == ["Ann", "Sales", "91"]
