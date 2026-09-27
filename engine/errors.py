"""Exceptions raised by the PDFWorkerz engine.

Every error the engine raises is one of these, so callers (CLI, server,
tests) can catch a single, documented hierarchy instead of guessing which
underlying library exception might leak through.
"""

from __future__ import annotations


class PdfWorkerzError(Exception):
    """Base class for all errors raised by the engine."""


class DocumentNotFoundError(PdfWorkerzError):
    """The given path does not exist or is not a file."""


class NotAPdfError(PdfWorkerzError):
    """The file could not be parsed as a PDF, even after repair was attempted."""


class PasswordRequiredError(PdfWorkerzError):
    """The document is encrypted and needs a user password to open."""


class WrongPasswordError(PdfWorkerzError):
    """The supplied password did not unlock the document."""


class CertificateEncryptedError(PdfWorkerzError):
    """The document uses public-key (certificate) encryption, which v1 cannot open.

    See SPEC.md section 6: opening this class of file requires the
    recipient's private key, which is out of scope. Detection is supported
    (SEC-07); decryption is not.
    """


class RepairFailedError(PdfWorkerzError):
    """The document is damaged and could not be repaired by any backend."""


class OverwriteRefusedError(PdfWorkerzError):
    """A save would overwrite the original file without an explicit opt-in.

    See SPEC.md section 4.2 rule 3 / feature COR-08: the original is never
    overwritten unless the caller passes ``overwrite=True`` to ``Document.save``.
    """


class NothingToUndoError(PdfWorkerzError):
    """The undo/redo journal has no earlier (or later) state to move to."""


class OpValidationError(PdfWorkerzError):
    """An Op failed validation before it could be applied or serialized."""
