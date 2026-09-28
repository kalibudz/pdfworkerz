"""Serializing a live document without breaking its encryption (SEC-03).

Measured on pymupdf 1.28.2: a plain ``Document.tobytes()`` -- or any ``save``
without ``encryption=PDF_ENCRYPT_KEEP`` -- permanently drops an encrypted
document's encryption state, so every *later* save, even one that asks to
keep the encryption, writes the file decrypted. The engine serialized the
live document on every edit (to parse it with pikepdf), so opening an
encrypted PDF, editing it and saving produced an unencrypted file.

``tobytes(encryption=KEEP)`` leaves that state intact but returns encrypted
bytes. :func:`plain_bytes` therefore decrypts a throwaway copy instead, which
needs the password the document was opened with; :func:`remember_password`
records it on the pymupdf document itself (pymupdf documents can't be weakly
referenced, so the attribute lives exactly as long as the document does).
Nothing in the engine may call ``tobytes()`` or ``save`` on a live document
without KEEP; use this module.
"""

from __future__ import annotations

import pymupdf

from engine.errors import WrongPasswordError

# Verified at runtime (pymupdf 1.28.2): PDF_ENCRYPT_KEEP == 0. Missing from pymupdf's stub.
PDF_ENCRYPT_KEEP: int = pymupdf.PDF_ENCRYPT_KEEP  # type: ignore[attr-defined]
_PASSWORD_ATTR = "_pdfworkerz_password"  # nosec B105 -- the name of an attribute, not a password


def remember_password(doc: pymupdf.Document, password: str | None) -> None:
    setattr(doc, _PASSWORD_ATTR, password)


def encryption_of(doc: pymupdf.Document) -> str | None:
    """The encryption method MuPDF reports (e.g. "Standard V5 R6 256-bit AES"), or None."""
    return (doc.metadata or {}).get("encryption") or None


def plain_bytes(doc: pymupdf.Document) -> bytes:
    """`doc`'s current state as decrypted bytes, leaving `doc`'s own encryption untouched."""
    data: bytes = doc.tobytes(encryption=PDF_ENCRYPT_KEEP)
    if encryption_of(doc) is None:
        return data
    copy = pymupdf.open(stream=data, filetype="pdf")
    try:
        if copy.needs_pass and not copy.authenticate(getattr(doc, _PASSWORD_ATTR, None) or ""):
            raise WrongPasswordError("could not decrypt a working copy of this document")
        plain: bytes = copy.tobytes()  # the copy's own encryption state no longer matters
        return plain
    finally:
        copy.close()


def encrypted_snapshot(doc: pymupdf.Document) -> bytes:
    """`doc`'s current state as bytes that keep its encryption (for the undo journal)."""
    data: bytes = doc.tobytes(encryption=PDF_ENCRYPT_KEEP)
    return data
