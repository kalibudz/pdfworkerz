"""SEC-04 (add password, AES-256), SEC-05 (remove password), SEC-06 (set permissions)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pikepdf
import pytest

from engine.document import Document
from engine.errors import OpValidationError, PasswordRequiredError, WrongPasswordError
from engine.ops.protect import RemovePasswordOp, SetPasswordOp, SetPermissionsOp
from engine.security import inspect_encryption
from tests.corpus.build_corpus import Corpus

USER_PW = "a-user-password"
OWNER_PW = "a-different-owner-password"


def _copy(corpus_path: Path, work_dir: Path, name: str = "work.pdf") -> Path:
    dest = work_dir / name
    shutil.copy(corpus_path, dest)
    return dest


# -- SEC-04: add password, AES-256 -------------------------------------------------


@pytest.mark.feature("SEC-04", criterion=1)
def test_protecting_a_plain_document_refuses_opening_without_the_password(corpus: Corpus, work_dir: Path) -> None:
    work = _copy(corpus.simple, work_dir)
    with Document.open(work) as document:
        SetPasswordOp(user_password=USER_PW, path=str(work), overwrite=True).apply(document)
    with pytest.raises(pikepdf.PasswordError):
        pikepdf.open(work)
    with pytest.raises(PasswordRequiredError):
        Document.open(work)


@pytest.mark.feature("SEC-04", criterion=1)
def test_an_explicit_empty_owner_password_is_refused_not_silently_accepted(corpus: Corpus, work_dir: Path) -> None:
    """A real user password plus an *explicit* empty-string owner password used to
    slip past the "at least one password" check and reach PyMuPDF as owner_pw="" --
    which every PDF reader (including pikepdf) authenticates as a no-password-needed
    owner-level open, defeating the user password entirely. Found by independent
    review; regression test for the fix in engine.protect.encrypt_document."""
    work = _copy(corpus.simple, work_dir)
    with Document.open(work) as document, pytest.raises(OpValidationError):
        SetPasswordOp(user_password=USER_PW, owner_password="", path=str(work), overwrite=True).apply(document)
    # The Op must refuse *before* ever saving -- the file stays the original,
    # unprotected copy (opens with no password at all), not a half-applied
    # "protected" file that's actually wide open via the empty owner password.
    with pikepdf.open(work) as reopened:
        assert reopened.is_encrypted is False


@pytest.mark.feature("SEC-04", criterion=2)
def test_protect_sets_distinct_user_and_owner_passwords(corpus: Corpus, work_dir: Path) -> None:
    work = _copy(corpus.simple, work_dir)
    with Document.open(work) as document:
        SetPasswordOp(user_password=USER_PW, owner_password=OWNER_PW, path=str(work), overwrite=True).apply(document)
    with Document.open(work, password=USER_PW) as document:
        assert document.page_count == 1
    with Document.open(work, password=OWNER_PW) as document:  # owner also authenticates (ISO 32000)
        assert document.page_count == 1
    with pytest.raises(WrongPasswordError):
        Document.open(work, password="neither-of-the-above")


@pytest.mark.feature("SEC-04", criterion=3)
def test_protect_always_uses_aes_256_never_rc4(corpus: Corpus, work_dir: Path) -> None:
    work = _copy(corpus.simple, work_dir)
    with Document.open(work) as document:
        SetPasswordOp(user_password=USER_PW, path=str(work), overwrite=True).apply(document)
    info = inspect_encryption(work, password=USER_PW)
    assert info.algorithm == "AES-256"
    assert info.revision == 6


@pytest.mark.feature("SEC-04", criterion=4)
def test_protect_defaults_to_save_as_and_never_overwrites_without_opt_in(corpus: Corpus, work_dir: Path) -> None:
    work = _copy(corpus.simple, work_dir)
    original_bytes = work.read_bytes()
    with Document.open(work) as document:
        result = SetPasswordOp(user_password=USER_PW).apply(document)  # no path/overwrite given
    assert result.path != work
    assert result.path.exists()
    assert work.read_bytes() == original_bytes  # the original is untouched
    assert inspect_encryption(work).is_encrypted is False


@pytest.mark.feature("SEC-04", criterion=1)
def test_protect_refuses_with_neither_password(corpus: Corpus, work_dir: Path) -> None:
    work = _copy(corpus.simple, work_dir)
    with Document.open(work) as document, pytest.raises(OpValidationError):
        SetPasswordOp().apply(document)


# -- SEC-05: remove password --------------------------------------------------------


@pytest.mark.feature("SEC-05", criterion=1)
def test_unlock_with_the_correct_password_removes_encryption(corpus: Corpus, work_dir: Path) -> None:
    work = _copy(corpus.encrypted_aes_256, work_dir)
    with Document.open(work, password=corpus.user_password) as document:
        RemovePasswordOp(path=str(work), overwrite=True).apply(document)
    with pikepdf.open(work):
        pass  # opens with no password at all
    assert inspect_encryption(work).is_encrypted is False


@pytest.mark.feature("SEC-05", criterion=1)
def test_unlock_with_the_owner_password_also_removes_encryption(corpus: Corpus, work_dir: Path) -> None:
    work = _copy(corpus.encrypted_aes_256, work_dir)
    with Document.open(work, password=corpus.owner_password) as document:
        RemovePasswordOp(path=str(work), overwrite=True).apply(document)
    assert inspect_encryption(work).is_encrypted is False


@pytest.mark.feature("SEC-05", criterion=2)
def test_unlock_with_a_wrong_password_is_refused_and_the_file_is_untouched(corpus: Corpus, work_dir: Path) -> None:
    work = _copy(corpus.encrypted_aes_256, work_dir)
    original_bytes = work.read_bytes()
    with pytest.raises(WrongPasswordError):
        Document.open(work, password="definitely-wrong")
    assert work.read_bytes() == original_bytes


@pytest.mark.feature("SEC-05", criterion=3)
def test_unlock_preserves_content_byte_for_byte_aside_from_encryption(corpus: Corpus, work_dir: Path) -> None:
    work = _copy(corpus.encrypted_aes_256, work_dir)
    with Document.open(work, password=corpus.user_password) as document:
        before_text = document.raw[0].get_text()
        RemovePasswordOp(path=str(work), overwrite=True).apply(document)
    with Document.open(work) as document:
        assert document.page_count == 1
        assert document.raw[0].get_text() == before_text


# -- SEC-06: set permissions ---------------------------------------------------------


@pytest.mark.feature("SEC-06", criterion=1)
@pytest.mark.parametrize(
    ("flag", "report_field"),
    [
        ("allow_print", "print_lowres"),
        ("allow_copy", "copy_for_extraction"),
        ("allow_modify", "modify"),
        ("allow_annotate", "annotate_and_fill_forms"),
    ],
)
def test_each_permission_flag_can_be_independently_denied(
    corpus: Corpus, work_dir: Path, flag: str, report_field: str
) -> None:
    work = _copy(corpus.simple, work_dir, name=f"{flag}.pdf")
    with Document.open(work) as document:
        SetPermissionsOp(owner_password=OWNER_PW, path=str(work), overwrite=True, **{flag: False}).apply(document)
    info = inspect_encryption(work)
    assert getattr(info.permissions, report_field) is False
    # every other flag stays allowed (independence, not an all-or-nothing switch)
    for other_field in ("print_lowres", "copy_for_extraction", "modify", "annotate_and_fill_forms"):
        if other_field != report_field:
            assert getattr(info.permissions, other_field) is True


@pytest.mark.feature("SEC-06", criterion=2)
def test_denied_permissions_are_reflected_in_pdfworkerzs_own_inspection(corpus: Corpus, work_dir: Path) -> None:
    work = _copy(corpus.simple, work_dir)
    with Document.open(work) as document:
        SetPermissionsOp(
            owner_password=OWNER_PW, allow_modify=False, allow_annotate=False, path=str(work), overwrite=True
        ).apply(document)
    info = inspect_encryption(work)
    assert info.is_encrypted is True
    assert info.permissions is not None
    assert info.permissions.modify is False
    assert info.permissions.annotate_and_fill_forms is False
    assert info.permissions.print_lowres is True
    assert info.permissions.copy_for_extraction is True


@pytest.mark.feature("SEC-06", criterion=3)
def test_set_permissions_requires_an_owner_password(corpus: Corpus, work_dir: Path) -> None:
    work = _copy(corpus.simple, work_dir)
    with Document.open(work) as document, pytest.raises(OpValidationError):
        SetPermissionsOp(owner_password="", allow_modify=False).apply(document)


@pytest.mark.feature("SEC-06", criterion=3)
def test_set_permissions_owner_password_cannot_be_bypassed_by_opening_with_none(corpus: Corpus, work_dir: Path) -> None:
    """The restriction is backed by a real owner password: opening with no password at all
    must not grant owner-level (permission-bypassing) access."""
    work = _copy(corpus.simple, work_dir)
    with Document.open(work) as document:
        SetPermissionsOp(owner_password=OWNER_PW, allow_modify=False, path=str(work), overwrite=True).apply(document)
    with pikepdf.open(work) as pdf:  # no password given at all
        assert pdf.allow.modify_other is False
        assert pdf.user_password_matched is True  # opened as the (empty) user, not the owner
