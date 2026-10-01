"""SIG-06: the typed Op for the local self-signed certificate generator (see
engine.certs). Unlike every other Op in this package, it never touches a
Document -- there's no page, no file being edited, nothing to undo -- so its
`apply` takes `document` only to satisfy Op's shared interface (never reads
it) and server/app.py reaches it through its own dedicated, non-document
route (POST /certs/generate) rather than the generic, journaled
/documents/{id}/ops endpoint."""

from __future__ import annotations

from typing import Literal

from engine.certs import MIN_KEY_SIZE, CertResult, generate_self_signed_cert
from engine.document import Document
from engine.ops.base import Op, register_op


@register_op
class GenerateCertificateOp(Op):
    """Generate an RSA key pair and a self-signed X.509 certificate for it,
    locally, with no network call (see engine.certs.generate_self_signed_cert).
    `passphrase`, if given, encrypts the private key in both the standalone
    PEM and the PKCS#12 bundle the result carries."""

    op: Literal["generate_certificate"] = "generate_certificate"
    common_name: str
    key_size: int = MIN_KEY_SIZE
    passphrase: str | None = None

    def apply(self, document: Document | None = None) -> CertResult:
        return generate_self_signed_cert(
            self.common_name,
            key_size=self.key_size,
            passphrase=self.passphrase.encode("utf-8") if self.passphrase else None,
        )
