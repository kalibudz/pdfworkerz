"""Redaction's content cleaning must never move text it was not asked to touch.

MuDF's cleaner drops a text line move that translates by nothing (``0 0 Td``, ``0 0 TD``,
``T*`` at zero leading) and mangles ``'`` and ``"``. Since ``Td`` also resets the text
matrix to the line matrix, dropping it starts the following text after the advance of
everything already drawn on that line. Found on a real proposal: editing one table cell
pulled apart a colour emoji 100pt away, whose layers Word had stacked with ``0 0 Td``.
Every PDF here is built by the test.
"""

from __future__ import annotations

import io

import pikepdf
import pymupdf
import pytest

from engine.contentstream import protect_text_line_moves
from engine.document import Document
from engine.fonts.style import dedupe_texttrace
from engine.ops.base import parse_op
from engine.ops.journal import UndoRedoJournal

# "AAAA" is drawn, then the line move puts the pen back at the line start, so "X" is
# drawn over the As rather than after them -- how stacked emoji layers are positioned.
_BODIES = {
    "Td": "BT /F1 12 Tf 72 700 Td (AAAA) Tj 0 0 Td (X) Tj ET",
    "TD": "BT /F1 12 Tf 72 700 Td (AAAA) Tj 0 0 TD (X) Tj ET",
    "T* at zero leading": "BT /F1 12 Tf 72 700 Td (AAAA) Tj T* (X) Tj ET",
    "quote": "BT /F1 12 Tf 72 700 Td (AAAA) Tj (X) ' ET",
    "quote with leading": "BT /F1 12 Tf 14 TL 72 700 Td (AAAA) Tj (X) ' ET",
    "double quote": 'BT /F1 12 Tf 72 700 Td (AAAA) Tj 0 0 (X) " ET',
    "rotated": "BT /F1 12 Tf 0 1 -1 0 300 400 Tm (AAAA) Tj 0 0 Td (X) Tj ET",
}


def _page_with(body: str) -> bytes:
    doc = pymupdf.open()
    doc.new_page()
    data = doc.tobytes()
    doc.close()
    pdf = pikepdf.open(io.BytesIO(data))
    page = pdf.pages[0]
    helvetica = pdf.make_indirect(
        pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica)
    )
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=helvetica))
    page.Contents = pdf.make_stream(body.encode())
    out = io.BytesIO()
    pdf.save(out)
    pdf.close()
    return out.getvalue()


def _x_origins(data: bytes) -> list[tuple[str, float, float]]:
    """Every glyph's character and origin, rounded -- where the page really draws them."""
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        return [
            (chr(char[0]), round(char[2][0], 2), round(char[2][1], 2))
            for span in dedupe_texttrace(doc[0].get_texttrace())
            for char in span["chars"]
        ]


@pytest.mark.feature("FNT-12")
@pytest.mark.parametrize("name", list(_BODIES))
def test_cleaning_moves_text_unless_the_page_is_protected_first(name: str) -> None:
    data = _page_with(_BODIES[name])
    original = _x_origins(data)
    assert original, name

    with pymupdf.open(stream=data, filetype="pdf") as unprotected:
        unprotected[0].clean_contents()
        damaged = _x_origins(unprotected.tobytes())

    with pymupdf.open(stream=data, filetype="pdf") as protected:
        assert protect_text_line_moves(protected[0]) == 1
        assert _x_origins(protected.tobytes()) == original, f"{name}: the protection itself moved text"
        protected[0].clean_contents()
        assert _x_origins(protected.tobytes()) == original, f"{name}: text moved despite the protection"

    # The bug this guards against is real: without the protection, cleaning moves it.
    if damaged == original:
        pytest.skip(f"this pymupdf no longer moves text for {name}; the protection is still exact")


@pytest.mark.feature("FNT-12")
def test_protection_is_idempotent_and_leaves_ordinary_pages_alone() -> None:
    data = _page_with(_BODIES["Td"])
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        assert protect_text_line_moves(doc[0]) == 1
        assert protect_text_line_moves(doc[0]) == 0  # nothing left at risk

    plain = _page_with("BT /F1 12 Tf 72 700 Td (AAAA) Tj 5 0 Td (X) Tj ET")
    with pymupdf.open(stream=plain, filetype="pdf") as doc:
        assert protect_text_line_moves(doc[0]) == 0  # a real move survives cleaning as it is


@pytest.mark.feature("FNT-12")
def test_an_edit_no_longer_moves_stacked_text_elsewhere_on_the_page(work_dir: object) -> None:
    """The reported failure, end to end: editing one line must not disturb text drawn
    with a zero line move far away, and the after-edit check must pass."""
    body = "BT /F1 12 Tf 72 700 Td (Edit me) Tj ET BT /F1 12 Tf 72 300 Td (AAAA) Tj 0 0 Td (X) Tj ET"
    journal = UndoRedoJournal(Document.from_bytes(_page_with(body)))
    before = {(char, x, y) for char, x, y in _x_origins(journal.document.to_bytes()) if y > 500}

    result = journal.record(
        parse_op(
            {"op": "edit_span", "page_index": 0, "span_index": 0, "new_text": "Edited", "require_tier": "fallback"}
        )
    )
    assert result.verification is not None
    assert result.verification.looks_right, result.verification
    after = {(char, x, y) for char, x, y in _x_origins(journal.document.to_bytes()) if y > 500}
    assert after == before  # the stacked text 400pt away is exactly where it was
