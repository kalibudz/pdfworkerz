"""Encryption and permission inspection (SEC-01, SEC-02, SEC-03, SEC-07).

Reading is layered on **pikepdf**, which exposes the PDF's own encryption
dictionary (revision, key length, crypt filter method) directly, and
authenticates with the empty string when no password is given -- exactly
what real-world "owner restricted, no user password" files expect. Opening
with a user password (SEC-01) and re-saving under the original encryption
settings (SEC-03) are handled by :mod:`engine.document`, which calls into
the helpers here for reporting.

No password guessing or cracking is ever performed: a password is used only
when the caller supplies one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pikepdf
from pydantic import BaseModel, ConfigDict

# Revisions 2 and 3 (PDF encryption /V 1 and /V 2) predate crypt-filter
# dictionaries, so pikepdf reports EncryptionMethod.none for them even
# though the algorithm is always RC4 -- confirmed against pikepdf 10.14.0
# (see tests/engine/test_security.py::test_algorithm_name_matches_every_revision).
_LEGACY_RC4_REVISIONS = (2, 3)


class PermissionReport(BaseModel):
    """Which operations the document's permission flags allow.

    Mirrors :class:`pikepdf.Permissions`, translated to the vocabulary used
    in SPEC.md section 6 (print, copy, modify, annotate, ...).
    """

    model_config = ConfigDict(frozen=True)

    print_lowres: bool
    print_highres: bool
    modify: bool
    copy_for_extraction: bool
    annotate_and_fill_forms: bool
    assemble_document: bool
    accessibility_extraction: bool

    @classmethod
    def from_pikepdf(cls, allow: pikepdf.Permissions) -> PermissionReport:
        return cls(
            print_lowres=allow.print_lowres,
            print_highres=allow.print_highres,
            modify=allow.modify_other,
            copy_for_extraction=allow.extract,
            annotate_and_fill_forms=allow.modify_annotation or allow.modify_form,
            assemble_document=allow.modify_assembly,
            accessibility_extraction=allow.accessibility,
        )


class EncryptionInfo(BaseModel):
    """What SEC-01/02/03/07 need to know about a document's encryption."""

    model_config = ConfigDict(frozen=True)

    is_encrypted: bool
    is_certificate_encrypted: bool = False
    revision: int | None = None
    algorithm: str | None = None  # "RC4-40" | "RC4-128" | "AES-128" | "AES-256" | None
    requires_user_password: bool = False
    """True when no password (including the empty string) opens the file."""
    owner_password_only: bool = False
    """True when the file opens with an empty user password but has an owner password set."""
    permissions: PermissionReport | None = None


def detect_certificate_encryption(path: str | Path) -> bool:
    """Return True if the trailer announces public-key (Adobe.PubSec) encryption.

    pikepdf can parse a PDF's Encrypt dictionary -- which is never itself
    encrypted -- without being able to decrypt content protected for a
    specific recipient certificate. When the filter is unsupported for that
    reason, pikepdf raises :class:`pikepdf.PdfError` with a message naming
    the encryption filter, rather than :class:`pikepdf.PasswordError`
    (which means "this is normal password encryption, and this password
    didn't work"). See SPEC.md section 6.
    """
    try:
        with pikepdf.open(path):
            return False
    except pikepdf.PasswordError:
        return False
    except pikepdf.PdfError as exc:
        return "encryption filter" in str(exc).lower()


def _algorithm_name(enc: Any) -> str:
    # `enc` is a pikepdf.models.encryption.EncryptionInfo; kept as Any to avoid
    # a name clash with our own EncryptionInfo model in this module.
    if enc.R in _LEGACY_RC4_REVISIONS:
        return f"RC4-{enc.bits}"
    if enc.R == 4:
        return "AES-128" if "aes" in str(enc.file_method).lower() else "RC4-128"
    return "AES-256"  # R5 (deprecated) and R6 both report 256-bit AES keys


def inspect_encryption(path: str | Path, *, password: str = "") -> EncryptionInfo:
    """Report a document's encryption without requiring a correct password.

    ``password`` defaults to empty, which is exactly the password an
    owner-restricted-only file expects (SEC-02): such a file opens
    successfully and ``owner_password_only`` is True. A file that also
    requires a user password raises nothing here; instead
    ``requires_user_password`` is True and every other field is unset, so
    callers can prompt without an exception-driven control flow.
    """
    if detect_certificate_encryption(path):
        return EncryptionInfo(is_encrypted=True, is_certificate_encrypted=True, requires_user_password=True)

    try:
        pdf = pikepdf.open(path, password=password)
    except pikepdf.PasswordError:
        return EncryptionInfo(is_encrypted=True, requires_user_password=True)

    try:
        if not pdf.is_encrypted:
            return EncryptionInfo(is_encrypted=False)
        enc = pdf.encryption
        # "" here means "no password was given", not a credential -- see the docstring above.
        opened_with_no_password = password == ""  # nosec B105
        return EncryptionInfo(
            is_encrypted=True,
            revision=enc.R,
            algorithm=_algorithm_name(enc),
            owner_password_only=opened_with_no_password and pdf.user_password_matched,
            permissions=PermissionReport.from_pikepdf(pdf.allow),
        )
    finally:
        pdf.close()
