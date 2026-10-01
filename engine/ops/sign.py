"""SIG-02/03/04/05: the typed Ops wrapping engine.pades -- real cryptographic
PAdES signing and its validation report, as opposed to engine.ops.signatures'
PlaceSignatureOp (SIG-01), which is a purely visual stamp."""

from __future__ import annotations

from typing import Literal

from engine.document import Document
from engine.ops.base import Op, register_op
from engine.pades import (
    SignatureReport,
    SignedDocStatus,
    SignResult,
    document_signature_status,
    sign_document,
    validate_signatures,
)


@register_op
class SignDocumentOp(Op):
    """Apply a real PAdES digital signature to the document (SIG-02), optionally
    timestamped by an RFC 3161 TSA (SIG-04). See engine.pades.sign_document for
    the exact contract -- in particular, this signs the document's current
    on-disk bytes, not unsaved in-memory edits, and leaves `document` reloaded
    from the signed file afterward."""

    op: Literal["sign_document"] = "sign_document"
    cert_pem: str
    key_pem: str
    key_passphrase: str | None = None
    field_name: str | None = None
    reason: str | None = None
    location: str | None = None
    tsa_url: str | None = None
    path: str | None = None
    """Where to write the signed file; omitted, overwrites `document.source_path`
    itself (the natural default for an incremental update -- see engine.pades)."""

    def apply(self, document: Document) -> SignResult:
        return sign_document(
            document,
            cert_pem=self.cert_pem,
            key_pem=self.key_pem,
            key_passphrase=self.key_passphrase,
            field_name=self.field_name,
            reason=self.reason,
            location=self.location,
            tsa_url=self.tsa_url,
            path=self.path,
        )


@register_op
class ValidateSignaturesOp(Op):
    """SIG-03: a validation report for every signature embedded in the document.
    Read-only (like InspectOp/RenderPageOp): never journaled, applied directly."""

    op: Literal["validate_signatures"] = "validate_signatures"
    trust_anchors: list[str] | None = None
    ts_trust_anchors: list[str] | None = None

    def apply(self, document: Document) -> list[SignatureReport]:
        return validate_signatures(document, trust_anchors=self.trust_anchors, ts_trust_anchors=self.ts_trust_anchors)


@register_op
class DocumentSignatureStatusOp(Op):
    """SIG-05: whether the document is signed and whether that signature is
    still current -- what the web UI checks before warning the user that
    editing further will invalidate it. Read-only, applied directly."""

    op: Literal["document_signature_status"] = "document_signature_status"

    def apply(self, document: Document) -> SignedDocStatus:
        return document_signature_status(document)
