"""Typed Ops for SEC-04 (add password), SEC-05 (remove password) and SEC-06
(set permissions). See engine.protect for the underlying save logic and
why these write encryption at all, unlike engine.security's read-only
SEC-01/02/03/07.

Unlike most Ops, applying one of these *is* a save, not an in-memory edit
saved later: PyMuPDF's encryption, passwords and permission bits only
exist as parameters to ``Document.save``, so there is nothing to set them
on in between. This matches how ExtractPagesOp/SplitOp (engine.ops.pages)
already work -- an Op that performs its own file I/O and takes its own
``path``/``overwrite`` fields -- rather than the typical "mutate, then the
caller saves separately" shape. ``path=None, overwrite=False`` (the
default) reuses Document.save's own COR-08 protection: a new
`<name>.edited.pdf` is written next to the original, never the original
itself, unless the caller opts in with ``overwrite=True``. The web UI
should still ask for confirmation before ever sending ``overwrite=True``
for one of these three Ops specifically, since changing or removing a
document's password/permissions is a more consequential overwrite than a
typical text edit.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from engine import protect
from engine.ops.base import Op, register_op

if TYPE_CHECKING:
    from engine.document import Document, SaveResult


@register_op
class SetPasswordOp(Op):
    """SEC-04: add (or replace) a password, encrypting with AES-256 (R6).

    At least one of ``user_password``/``owner_password`` is required (both
    may be given, and they may differ); refused with
    :class:`~engine.errors.OpValidationError` otherwise. Permission flags
    default to allow -- set them here too, or follow up with
    :class:`SetPermissionsOp`, to restrict them.
    """

    op: Literal["set_password"] = "set_password"
    user_password: str | None = None
    owner_password: str | None = None
    allow_print: bool = True
    allow_copy: bool = True
    allow_modify: bool = True
    allow_annotate: bool = True
    path: str | None = None
    overwrite: bool = False

    def apply(self, document: Document) -> SaveResult:
        return protect.encrypt_document(
            document,
            user_password=self.user_password,
            owner_password=self.owner_password,
            allow_print=self.allow_print,
            allow_copy=self.allow_copy,
            allow_modify=self.allow_modify,
            allow_annotate=self.allow_annotate,
            path=self.path,
            overwrite=self.overwrite,
        )


@register_op
class RemovePasswordOp(Op):
    """SEC-05: remove all encryption from an already-open document.

    Takes no password field of its own: reaching ``apply`` at all already
    proves the password used to open the document (if any) was correct --
    ``Document.open`` raises :class:`~engine.errors.PasswordRequiredError`
    or :class:`~engine.errors.WrongPasswordError` first, before any file is
    touched, so a wrong password never reaches this Op and never changes
    the file on disk.
    """

    op: Literal["remove_password"] = "remove_password"
    path: str | None = None
    overwrite: bool = False

    def apply(self, document: Document) -> SaveResult:
        return protect.remove_password(document, path=self.path, overwrite=self.overwrite)


@register_op
class SetPermissionsOp(Op):
    """SEC-06: set print/copy/modify/annotate independently.

    ``owner_password`` is required (SEC-06's third criterion: a
    restriction with no owner password could trivially be removed by
    reopening the file with no password at all). ``user_password``
    defaults to the document's own current password when omitted, so
    reapplying permissions doesn't silently drop an existing user
    password; pass ``user_password=""`` explicitly to keep the document
    opening freely while still restricting what an opener may do.
    """

    op: Literal["set_permissions"] = "set_permissions"
    owner_password: str
    user_password: str | None = None
    allow_print: bool = True
    allow_copy: bool = True
    allow_modify: bool = True
    allow_annotate: bool = True
    path: str | None = None
    overwrite: bool = False

    def apply(self, document: Document) -> SaveResult:
        return protect.set_permissions(
            document,
            owner_password=self.owner_password,
            user_password=self.user_password,
            allow_print=self.allow_print,
            allow_copy=self.allow_copy,
            allow_modify=self.allow_modify,
            allow_annotate=self.allow_annotate,
            path=self.path,
            overwrite=self.overwrite,
        )
