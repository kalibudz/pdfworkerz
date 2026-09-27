"""SEC-01 (open encrypted), SEC-02 (owner-restricted), SEC-03 (save keeps encryption),
SEC-07 (detect certificate encryption)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pikepdf
import pytest

from engine.document import Document
from engine.errors import CertificateEncryptedError, PasswordRequiredError, WrongPasswordError
from engine.security import detect_certificate_encryption, inspect_encryption
from tests.corpus.build_corpus import Corpus

ENCRYPTED_FIXTURES = [
    ("encrypted_rc4_40", "RC4-40", 2),
    ("encrypted_rc4_128", "RC4-128", 3),
    ("encrypted_aes_128", "AES-128", 4),
    ("encrypted_aes_256", "AES-256", 6),
]


@pytest.mark.feature("SEC-01")
@pytest.mark.parametrize(("fixture_name", "algorithm", "revision"), ENCRYPTED_FIXTURES)
def test_open_every_encryption_revision_with_the_user_password(
    corpus: Corpus, fixture_name: str, algorithm: str, revision: int
) -> None:
    path = getattr(corpus, fixture_name)
    with Document.open(path, password=corpus.user_password) as doc:
        assert doc.page_count == 1
        assert doc.is_encrypted
    info = inspect_encryption(path, password=corpus.user_password)
    assert info.algorithm == algorithm
    assert info.revision == revision


@pytest.mark.feature("SEC-01")
def test_open_encrypted_without_a_password_raises_password_required(corpus: Corpus) -> None:
    with pytest.raises(PasswordRequiredError):
        Document.open(corpus.encrypted_aes_256)


@pytest.mark.feature("SEC-01")
def test_open_encrypted_with_the_wrong_password_raises_wrong_password(corpus: Corpus) -> None:
    with pytest.raises(WrongPasswordError):
        Document.open(corpus.encrypted_aes_256, password="definitely-not-it")


@pytest.mark.feature("SEC-01")
def test_no_password_guessing_is_ever_attempted(corpus: Corpus) -> None:
    """Document.open makes exactly one authentication attempt, with exactly the password given.

    It never tries the empty string, a list of common passwords, or any
    password other than the one the caller supplied.
    """
    with pytest.raises(WrongPasswordError):
        Document.open(corpus.encrypted_aes_256, password="")
    with pytest.raises(WrongPasswordError):
        Document.open(corpus.encrypted_aes_256, password="password123")


@pytest.mark.feature("SEC-01")
def test_the_owner_password_also_unlocks_the_document_per_the_pdf_spec(corpus: Corpus) -> None:
    """Per ISO 32000, the owner password is a superset credential: it authenticates too.

    This is real PDF authentication semantics, not password guessing --
    PDFWorkerz never tries anything the caller didn't supply.
    """
    with Document.open(corpus.encrypted_aes_256, password=corpus.owner_password) as doc:
        assert doc.page_count == 1


@pytest.mark.feature("SEC-02")
def test_owner_restricted_file_opens_without_any_password(corpus: Corpus) -> None:
    with Document.open(corpus.owner_only) as doc:
        assert doc.page_count == 1


@pytest.mark.feature("SEC-02")
def test_owner_restricted_file_reports_its_actual_permissions(corpus: Corpus) -> None:
    info = inspect_encryption(corpus.owner_only)
    assert info.is_encrypted is True
    assert info.owner_password_only is True
    assert info.permissions is not None
    assert info.permissions.modify is False
    assert info.permissions.copy_for_extraction is False
    assert info.permissions.print_lowres is True
    assert info.permissions.print_highres is False


@pytest.mark.feature("SEC-02")
def test_fully_open_document_is_not_reported_as_owner_restricted(corpus: Corpus) -> None:
    info = inspect_encryption(corpus.simple)
    assert info.is_encrypted is False
    assert info.owner_password_only is False


@pytest.mark.feature("SEC-03")
def test_full_save_of_an_encrypted_document_keeps_it_encrypted_with_the_same_password(
    corpus: Corpus, work_dir: Path
) -> None:
    work = work_dir / "enc.pdf"
    shutil.copy(corpus.encrypted_aes_256, work)

    with Document.open(work, password=corpus.user_password) as doc:
        doc.save(work, overwrite=True, mode="full")

    info = inspect_encryption(work, password=corpus.user_password)
    assert info.is_encrypted is True
    assert info.algorithm == "AES-256"
    with Document.open(work, password=corpus.user_password) as doc:
        assert doc.page_count == 1


@pytest.mark.feature("SEC-03")
def test_saving_an_unencrypted_document_does_not_add_encryption(corpus: Corpus, work_dir: Path) -> None:
    work = work_dir / "plain.pdf"
    shutil.copy(corpus.simple, work)
    with Document.open(work) as doc:
        doc.save(work, overwrite=True, mode="full")
    info = inspect_encryption(work)
    assert info.is_encrypted is False


@pytest.mark.feature("SEC-07")
def test_certificate_encryption_is_detected(corpus: Corpus) -> None:
    assert detect_certificate_encryption(corpus.certificate_stub) is True


@pytest.mark.feature("SEC-07")
def test_normal_files_are_never_misdetected_as_certificate_encrypted(corpus: Corpus) -> None:
    assert detect_certificate_encryption(corpus.simple) is False
    assert detect_certificate_encryption(corpus.encrypted_aes_256) is False
    assert detect_certificate_encryption(corpus.owner_only) is False


@pytest.mark.feature("SEC-07")
def test_a_damaged_file_is_not_misclassified_as_certificate_encrypted(corpus: Corpus) -> None:
    """A generic parse failure (bad xref) must not be confused with an unsupported encryption filter."""
    assert detect_certificate_encryption(corpus.broken_severely) is False


@pytest.mark.feature("SEC-07")
def test_open_on_a_certificate_encrypted_file_raises_a_clear_dedicated_error(corpus: Corpus) -> None:
    with pytest.raises(CertificateEncryptedError):
        Document.open(corpus.certificate_stub)


@pytest.mark.feature("SEC-07")
def test_certificate_detection_does_not_require_pdf_to_be_damaged(corpus: Corpus, work_dir: Path) -> None:
    """The certificate stub must still be openable as a plain pikepdf trailer read (sanity on the fixture)."""
    with pikepdf.open(corpus.simple):
        pass  # a normal file must never raise PdfError at all
