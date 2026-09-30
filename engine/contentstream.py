"""Protect text against a content-cleaner bug that silently moves it.

Every redaction PDFWorkerz applies (removing a span's glyphs before redrawing them,
deleting an image, deleting a shape) makes MuPDF clean the page's content stream. That
cleaner **drops a text line move that translates by nothing** -- ``0 0 Td``, ``0 0 TD``,
and ``T*`` while the leading is 0 -- as if it were a no-op.

It never is. ``Td`` sets the *line* matrix and then resets the text matrix to it, which
throws away the horizontal advance of every glyph drawn since the last line move. Drop
it and the following text starts further right by exactly that much. Confirmed against
pymupdf 1.28.2 with a plain Helvetica line: "AAAA" then ``0 0 Td`` then "X" put X at
x=72; after cleaning, x=104. The same happens to ``'`` and ``"`` (show text on the next
line), whichever leading is set.

Real documents rely on this: Word writes ``0 0 Td`` between the parts of a colour emoji,
whose layers are drawn on top of one another. Editing anything on such a page pulled the
emoji's layers apart -- far from the edit, and caught only by the after-edit check
(FNT-12), which refused the edit rather than let it through.

So before redacting, every at-risk operator is rewritten into the explicit ``Tm`` it is
equivalent to, offset by a ten-thousandth of a point so that the cleaner keeps it
(see ``_NUDGE_PT``). The line matrix is tracked exactly as ISO
32000-1 9.4.2 defines it, so the rewrite changes nothing about where text lands, and
running it again finds nothing left to do.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pikepdf

if TYPE_CHECKING:
    import pymupdf

Matrix = tuple[float, float, float, float, float, float]
_IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _translated(matrix: Matrix, tx: float, ty: float) -> Matrix:
    """[1 0 0 1 tx ty] x `matrix` -- what Td does to the line matrix."""
    a, b, c, d, e, f = matrix
    return (a, b, c, d, tx * a + ty * c + e, tx * b + ty * d + f)


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _numbers(operands: Any, count: int) -> list[float] | None:
    if len(operands) != count:
        return None
    values = [_number(operand) for operand in operands]
    return None if any(value is None for value in values) else [value for value in values if value is not None]


def _real(value: float) -> pikepdf.Object:
    parsed: pikepdf.Object = pikepdf.Object.parse(f"{value:.6f}".encode())
    return parsed


_NUDGE_PT = 1e-4
"""How far a replacement ``Tm`` is offset horizontally, so the cleaner keeps it.

The cleaner drops a ``Tm`` that matches the line matrix it is already tracking -- which
is exactly the ``Tm`` this module emits, since that is the whole point of the operator
being rewritten. Measured against pymupdf 1.28.2: an offset of 1e-6 pt is still folded
away, 1e-5 pt survives. This is a hundred times that, and still a ten-thousandth of a
point: about 1/1200 of a pixel at 600 dpi, and far below what any renderer can show.
The tracked line matrix keeps its exact value, so the offset never accumulates.
"""


def _matrix_instruction(matrix: Matrix) -> pikepdf.ContentStreamInstruction:
    a, b, c, d, e, f = matrix
    return pikepdf.ContentStreamInstruction([_real(v) for v in (a, b, c, d, e + _NUDGE_PT, f)], _TM)


def _instruction(operands: list[Any], operator: pikepdf.Operator) -> pikepdf.ContentStreamInstruction:
    return pikepdf.ContentStreamInstruction(operands, operator)


_TM = pikepdf.Operator("Tm")
_TJ = pikepdf.Operator("Tj")
_TL = pikepdf.Operator("TL")
_TW = pikepdf.Operator("Tw")
_TC = pikepdf.Operator("Tc")


def _rewrite(instructions: list[Any]) -> tuple[list[Any], bool]:
    """Every at-risk line move replaced by its exact `Tm`. Returns the new instructions
    and whether anything changed."""
    out: list[Any] = []
    line: Matrix = _IDENTITY
    leading = 0.0
    changed = False

    for item in instructions:
        operator = str(getattr(item, "operator", ""))
        operands = list(getattr(item, "operands", []))

        if operator == "BT":  # both matrices reset to the identity
            line = _IDENTITY
        elif operator == "Tm":
            values = _numbers(operands, 6)
            if values is not None:
                line = (values[0], values[1], values[2], values[3], values[4], values[5])
        elif operator == "TL":
            values = _numbers(operands, 1)
            if values is not None:
                leading = values[0]
        elif operator in ("Td", "TD"):
            values = _numbers(operands, 2)
            if values is not None:
                tx, ty = values
                if operator == "TD":
                    leading = -ty
                line = _translated(line, tx, ty)
                if (tx, ty) == (0.0, 0.0):
                    # The cleaner would drop this; the same move as an explicit Tm survives.
                    if operator == "TD":
                        out.append(_instruction([_real(leading)], _TL))
                    out.append(_matrix_instruction(line))
                    changed = True
                    continue
        elif operator == "T*":
            line = _translated(line, 0.0, -leading)
            if leading == 0.0:
                out.append(_matrix_instruction(line))
                changed = True
                continue
        elif operator in ("'", '"'):
            # Show text on the next line. Split into the moves it stands for, plus Tj:
            # the cleaner mangles both forms whatever the leading is.
            text = operands[-1] if operands else None
            if text is not None:
                if operator == '"' and len(operands) == 3:
                    out.append(_instruction([operands[0]], _TW))
                    out.append(_instruction([operands[1]], _TC))
                line = _translated(line, 0.0, -leading)
                out.append(_matrix_instruction(line))
                out.append(_instruction([text], _TJ))
                changed = True
                continue

        out.append(item)

    return out, changed


def _protected_bytes(data: bytes) -> bytes | None:
    """`data` with its at-risk line moves rewritten, or None if nothing changed (or it
    could not be parsed, in which case it is safer to leave it exactly as it is)."""
    try:
        scratch = pikepdf.Pdf.new()
        instructions = pikepdf.parse_content_stream(scratch.make_stream(data))
    except Exception:
        return None
    try:
        rewritten, changed = _rewrite(list(instructions))
        if not changed:
            return None
        result: bytes = pikepdf.unparse_content_stream(rewritten)
    except Exception:
        return None
    return result


def _stream_bytes(document: pymupdf.Document, xref: int) -> bytes | None:
    """The stream at `xref`, or None when it has none (an image XObject, say)."""
    try:
        data: bytes = document.xref_stream(xref)
    except Exception:
        return None
    return data or None


def protect_text_line_moves(page: pymupdf.Page) -> int:
    """Rewrite the at-risk line moves in `page`'s own content streams and in the Form
    XObjects it draws. Returns how many streams were changed.

    Call this before anything that redacts on the page: after the rewrite, the content
    cleaner that redaction runs can no longer move text it was not asked to touch.
    """
    document = page.parent
    xrefs = list(page.get_contents())
    xrefs += [xobject[0] for xobject in page.get_xobjects()]
    changed = 0
    for xref in dict.fromkeys(xrefs):
        data = _stream_bytes(document, xref)
        if not data:
            continue
        protected = _protected_bytes(data)
        if protected is not None:
            document.update_stream(xref, protected)
            changed += 1
    return changed
