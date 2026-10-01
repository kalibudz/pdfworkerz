"""SIG-01: the typed Op for visual signatures (see engine.signatures).

SIG-06's certificate generator (engine.certs) has its own Op,
GenerateCertificateOp, in engine.ops.certs -- kept in a separate module since
it doesn't touch a Document at all (no `page_index`, nothing to "apply" to an
open document), unlike every Op here."""

from __future__ import annotations

import base64
import binascii
from typing import Literal

from engine.document import Document
from engine.errors import OpValidationError
from engine.ops.base import Op, register_op
from engine.signatures import PlaceResult, Point, Rect, SignatureKind, place_signature


@register_op
class PlaceSignatureOp(Op):
    """Draw a signature (drawn strokes, typed text, or an image) as ordinary
    page content inside `rect` (see engine.signatures.place_signature).
    `image_base64` travels as base64 like InsertImageOp's `image_base64`, so
    the Op stays plain JSON end to end."""

    op: Literal["place_signature"] = "place_signature"
    page_index: int
    rect: Rect
    kind: SignatureKind
    strokes: list[list[Point]] | None = None
    text: str | None = None
    font: str | None = None
    image_base64: str | None = None

    def apply(self, document: Document) -> PlaceResult:
        image_bytes = None
        if self.image_base64 is not None:
            try:
                image_bytes = base64.b64decode(self.image_base64, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise OpValidationError("image data is not valid base64") from exc
        return place_signature(
            document,
            self.page_index,
            self.rect,
            kind=self.kind,
            strokes=self.strokes,
            text=self.text,
            font=self.font,
            image_bytes=image_bytes,
        )
