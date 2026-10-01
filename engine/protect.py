"""Protect: add a password, remove one, and set permissions (SEC-04, SEC-05, SEC-06).

Unlike SEC-01/02/03/07 (engine.security), which only *read* a document's
encryption through pikepdf, these three features *write* it -- and this
codebase's save path (engine.document.Document.save) already goes through
PyMuPDF, not pikepdf: every save already passes ``encryption=`` and, for
SEC-03, ``owner_pw``/``user_pw`` straight to ``pymupdf.Document.save``. So
rather than introducing a second, pikepdf-based encryption-writing path,
these three features extend that same PyMuPDF save with two more
save-time parameters it already accepts but the engine never set before
now: ``encryption`` (the algorithm: AES-256 here) and ``permissions`` (the
bitmask). Confirmed against the installed versions (pymupdf 1.28.2,
pikepdf 10.14.0; see tests/engine/test_protect.py and the module docstring
below for what was checked and why).

A quick REPL check before writing this module turned up a real trap in
Document.save's existing (until-now-unused) SEC-03 groundwork: it turned
``owner_password=None`` into ``owner_pw=""`` whenever *either* password was
given. An explicit empty owner password is not "no owner password was
set" -- it's a real owner password of "", and pikepdf (like every PDF
reader) authenticates an attempted-empty-string open *as the owner* when
it matches, silently granting full access to a file meant to need
`user_password` to open at all. Passing `owner_pw=None` (omitted, pymupdf's
own default) instead lets PyMuPDF pick a sane internal default that does
NOT reproduce this hole (verified: opening such a file with the empty
string then correctly fails). Document.save was fixed alongside this
module to only forward whichever of `owner_password`/`user_password` the
caller actually gave, instead of coercing the other one to "".
"""

from __future__ import annotations

import pymupdf

from engine.document import Document, SaveResult
from engine.errors import OpValidationError

# pymupdf's own "allow everything" default (confirmed: pymupdf.Document.save's
# `permissions` parameter default is 4095 -- pymupdf 1.28.2).
_ALL_PERMISSIONS = 4095

# Individual permission bits and encryption algorithm codes, confirmed against the installed
# pymupdf 1.28.2 at a REPL before writing this module. Missing from pymupdf's stub (same gap
# documented for PDF_ENCRYPT_KEEP in engine.pdfbytes), hence the type: ignore on each.
_PDF_PERM_PRINT: int = pymupdf.PDF_PERM_PRINT  # type: ignore[attr-defined]
_PDF_PERM_PRINT_HQ: int = pymupdf.PDF_PERM_PRINT_HQ  # type: ignore[attr-defined]
_PDF_PERM_COPY: int = pymupdf.PDF_PERM_COPY  # type: ignore[attr-defined]
_PDF_PERM_MODIFY: int = pymupdf.PDF_PERM_MODIFY  # type: ignore[attr-defined]
_PDF_PERM_ANNOTATE: int = pymupdf.PDF_PERM_ANNOTATE  # type: ignore[attr-defined]
_PDF_PERM_FORM: int = pymupdf.PDF_PERM_FORM  # type: ignore[attr-defined]
PDF_ENCRYPT_AES_256: int = pymupdf.PDF_ENCRYPT_AES_256  # type: ignore[attr-defined]
PDF_ENCRYPT_NONE: int = pymupdf.PDF_ENCRYPT_NONE  # type: ignore[attr-defined]

# print covers both low- and high-resolution printing, and annotate also covers filling form
# fields -- the same grouping engine.security.PermissionReport already reads back out of a
# saved file.
_PRINT_BITS = _PDF_PERM_PRINT | _PDF_PERM_PRINT_HQ
_COPY_BITS = _PDF_PERM_COPY
_MODIFY_BITS = _PDF_PERM_MODIFY
_ANNOTATE_BITS = _PDF_PERM_ANNOTATE | _PDF_PERM_FORM


def _permission_bits(*, allow_print: bool, allow_copy: bool, allow_modify: bool, allow_annotate: bool) -> int:
    bits = _ALL_PERMISSIONS
    if not allow_print:
        bits &= ~_PRINT_BITS
    if not allow_copy:
        bits &= ~_COPY_BITS
    if not allow_modify:
        bits &= ~_MODIFY_BITS
    if not allow_annotate:
        bits &= ~_ANNOTATE_BITS
    return bits


def encrypt_document(
    document: Document,
    *,
    user_password: str | None,
    owner_password: str | None,
    allow_print: bool = True,
    allow_copy: bool = True,
    allow_modify: bool = True,
    allow_annotate: bool = True,
    path: str | None = None,
    overwrite: bool = False,
) -> SaveResult:
    """SEC-04: encrypt with AES-256 (R6), setting a user and/or owner password.

    At least one of `user_password`/`owner_password` must be given (both may
    be, and they may differ) -- refused with :class:`OpValidationError`
    otherwise, matching the pattern used throughout this engine of never
    guessing silently. Permission flags default to allow (SEC-04's own
    concern is adding a password, not restricting permissions; use
    :func:`set_permissions` -- or pass flags here directly -- for SEC-06).

    `path`/`overwrite` are forwarded to :meth:`Document.save` unchanged:
    the original is never overwritten in place unless the caller opts in,
    a save-as path (the default, a new `<name>.edited.pdf`) is always
    available (SEC-04's fourth criterion). The web UI must still warn
    before calling this with `overwrite=True`, since the engine itself
    allows it once asked (same contract as every other save in this
    codebase -- see Document.save's own docstring).
    """
    if user_password == "" or owner_password == "":  # nosec B105 -- guarding against an empty password, not one
        # An *explicit* empty string is not "no password" -- pikepdf (like
        # every PDF reader) authenticates an attempted-empty-string open as
        # the owner, so forwarding one silently recreates the exact "empty
        # owner password" hole this module's docstring describes above.
        # Document.save only guards the *omitted* (None) case; an explicit
        # "" slips past the `not owner_password` check below whenever
        # user_password is also set to a real value (caught by an
        # independent review -- SetPermissionsOp already guarded its own
        # owner_password field this way, this function did not).
        raise OpValidationError(
            "a password cannot be an empty string; omit it (leave it as None/unset) to leave "
            "that password unset, or supply a real one"
        )
    if not user_password and not owner_password:
        raise OpValidationError("protect needs a user password, an owner password, or both")
    bits = _permission_bits(
        allow_print=allow_print, allow_copy=allow_copy, allow_modify=allow_modify, allow_annotate=allow_annotate
    )
    return document.save(
        path,
        overwrite=overwrite,
        mode="full",
        encryption=PDF_ENCRYPT_AES_256,
        user_password=user_password,
        owner_password=owner_password,
        permissions=bits,
    )


def remove_password(document: Document, *, path: str | None = None, overwrite: bool = False) -> SaveResult:
    """SEC-05: strip all encryption from an already-open document.

    `document` having opened successfully already proves the password was
    either not needed or correct (Document.open raises
    :class:`~engine.errors.PasswordRequiredError` or
    :class:`~engine.errors.WrongPasswordError` first, before any file is
    touched) -- so there is nothing left to validate here; this just saves
    the document out with no encryption at all, which the caller reaches
    only after a successful open.

    `encryption=PDF_ENCRYPT_NONE` is passed explicitly: this is the one
    place in the whole engine that must NOT default to
    ``PDF_ENCRYPT_KEEP`` (every ordinary edit keeps the original
    encryption per SEC-03; removing it is the entire point here).
    """
    return document.save(
        path,
        overwrite=overwrite,
        mode="full",
        encryption=PDF_ENCRYPT_NONE,
        user_password="",  # nosec B106 -- removing encryption, not a credential being set
        owner_password="",  # nosec B106 -- same: "no password" is the intended result
    )


def set_permissions(
    document: Document,
    *,
    owner_password: str,
    user_password: str | None = None,
    allow_print: bool = True,
    allow_copy: bool = True,
    allow_modify: bool = True,
    allow_annotate: bool = True,
    path: str | None = None,
    overwrite: bool = False,
) -> SaveResult:
    """SEC-06: set print/copy/modify/annotate independently.

    `owner_password` is required (not optional): a permission restriction
    with no owner password set could be removed by anyone simply by
    re-opening the file with no password at all and saving it back out,
    so SEC-06's third criterion requires one. `user_password` defaults to
    the document's own current password (so re-applying permissions to an
    already user-password-protected document doesn't silently drop that
    password); pass `user_password=""` explicitly to make the document
    open freely while still restricting what an opener may do.
    """
    if not owner_password:
        raise OpValidationError(
            "set_permissions requires an owner password -- otherwise the restriction could be "
            "removed by anyone who opens the file with no password at all"
        )
    bits = _permission_bits(
        allow_print=allow_print, allow_copy=allow_copy, allow_modify=allow_modify, allow_annotate=allow_annotate
    )
    effective_user_password = user_password if user_password is not None else (document.password_used or "")
    return document.save(
        path,
        overwrite=overwrite,
        mode="full",
        encryption=PDF_ENCRYPT_AES_256,
        user_password=effective_user_password,
        owner_password=owner_password,
        permissions=bits,
    )
