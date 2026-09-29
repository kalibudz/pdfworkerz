"""The Document model: open, authenticate, repair, render and save (COR-01,
COR-02, COR-06, COR-07, COR-08, COR-09, SEC-01, SEC-02, SEC-03, SEC-07, OPT-06).

Every capability in this module has exactly one code path (SPEC.md section
4.2 rule 1): the CLI, the future server and every test call the same
:class:`Document` methods. PyMuPDF (`pymupdf`) is the source of truth for
the open document; pikepdf is used only for the lower-level encryption
inspection that PyMuPDF does not expose (see engine.security).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Self

import pymupdf

from engine.errors import (
    CertificateEncryptedError,
    DocumentNotFoundError,
    EncryptionLostError,
    NotAPdfError,
    OpValidationError,
    OverwriteRefusedError,
    PasswordRequiredError,
    RepairFailedError,
    SaveFailedError,
    SaveNotPossibleError,
    WrongPasswordError,
)
from engine.inspect import InspectionReport, inspect_document
from engine.pdfbytes import PDF_ENCRYPT_KEEP, encrypted_snapshot, encryption_of, plain_bytes, remember_password
from engine.security import detect_certificate_encryption

DEFAULT_RENDER_DPI = 150
_EDITED_SUFFIX = "edited"
_PDF_HEADER = b"%PDF-"


@dataclass(frozen=True)
class SaveResult:
    """What COR-06/COR-07/COR-08 report back after a save."""

    path: Path
    mode: str  # "incremental" | "full"
    bytes_written: int
    note: str = ""
    """Anything the caller should tell the user, e.g. that a signed file's signatures no longer apply."""


_TRAILER_ID = re.compile(rb"/ID\s*\[")
_PINNED_ID_PLACEHOLDER = b"<" + b"0" * 32 + b">"


def _pdf_string_end(data: bytes, start: int) -> int:
    """The index just past the PDF string starting at `start`: <hex> or (literal), where a
    literal may contain backslash escapes and balanced parentheses."""
    if data[start : start + 1] == b"<":
        return data.index(b">", start) + 1
    if data[start : start + 1] != b"(":
        raise ValueError("not a PDF string")
    depth, i = 0, start
    while True:
        char = data[i : i + 1]
        if char == b"\\":
            i += 2
            continue
        if char == b"(":
            depth += 1
        elif char == b")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1


def _pin_second_file_id(path: Path) -> None:
    """Make a saved file reproducible: with no_new_id, pymupdf 1.28.2 keeps the first half of
    the trailer's /ID but still writes a random second half on every save -- sometimes as a
    <hex> string, sometimes as a (literal) one (both measured). That second half only
    identifies the revision (encryption keys use the first), so it is replaced by a hash of
    the file's own bytes. The classic trailer comes after the cross-reference table, so
    changing its length moves no offset the file depends on."""
    data = path.read_bytes()
    trailer = data.rfind(b"trailer")
    found = [m for m in _TRAILER_ID.finditer(data) if trailer != -1 and m.start() > trailer]
    if not found:
        return  # the /ID sits in a compressed cross-reference stream; nothing to pin
    i = found[-1].end()
    try:
        while data[i : i + 1].isspace():
            i += 1
        i = _pdf_string_end(data, i)
        while data[i : i + 1].isspace():
            i += 1
        second_start = i
        second_end = _pdf_string_end(data, i)
    except (ValueError, IndexError):
        return  # an /ID we don't recognise: leave the file exactly as written
    blank = data[:second_start] + _PINNED_ID_PLACEHOLDER + data[second_end:]
    pinned = b"<" + hashlib.sha256(blank).hexdigest().upper()[:32].encode("ascii") + b">"
    path.write_bytes(data[:second_start] + pinned + data[second_end:])


def _default_output_path(source: Path) -> Path:
    """The first name in `stem.edited.pdf`, `stem.edited.2.pdf`, ... that doesn't exist yet.

    Feature COR-08: the original is never silently overwritten.
    """
    candidate = source.with_name(f"{source.stem}.{_EDITED_SUFFIX}{source.suffix}")
    n = 2
    while candidate.exists():
        candidate = source.with_name(f"{source.stem}.{_EDITED_SUFFIX}.{n}{source.suffix}")
        n += 1
    return candidate


class Document:
    """A single open PDF. Construct with :meth:`Document.open`, not directly."""

    def __init__(
        self,
        fitz_doc: pymupdf.Document,
        *,
        source_path: Path | None,
        password_used: str | None,
        file_backed: bool = False,
    ) -> None:
        self._doc = fitz_doc
        self.source_path = source_path
        self._password_used = password_used
        remember_password(fitz_doc, password_used)
        self._file_backed = file_backed
        """Whether `_doc` was read from `source_path` itself (not from a memory snapshot):
        only then can an incremental save append to that file."""

    # -- lifecycle -----------------------------------------------------

    @classmethod
    def open(cls, path: str | Path, *, password: str | None = None) -> Self:
        """Open a PDF, authenticating and repairing it as needed.

        Raises :class:`engine.errors.CertificateEncryptedError` for
        public-key-encrypted files (SEC-07), :class:`PasswordRequiredError`
        when the file needs a password that wasn't given, and
        :class:`WrongPasswordError` when the one given doesn't unlock it.
        A damaged file is repaired automatically (OPT-06); PyMuPDF reports
        whether repair was needed via ``Document.is_repaired``.
        """
        path = Path(path)
        if not path.is_file():
            raise DocumentNotFoundError(f"{path} does not exist or is not a file")

        if detect_certificate_encryption(path):
            raise CertificateEncryptedError(
                f"{path} uses public-key (certificate) encryption, which PDFWorkerz v1 cannot open"
            )

        try:
            # filetype="pdf" is required: without it, PyMuPDF guesses the format from
            # the extension and will happily open a .txt, .xps or .epub file as if it
            # were a one-page document of that other type instead of refusing it.
            fitz_doc = pymupdf.open(path, filetype="pdf")
        except pymupdf.FileDataError as exc:
            with path.open("rb") as fh:
                looks_like_pdf = fh.read(len(_PDF_HEADER)) == _PDF_HEADER
            if looks_like_pdf:
                raise RepairFailedError(f"{path} looks like a PDF but is too damaged to repair: {exc}") from exc
            raise NotAPdfError(f"{path} could not be parsed as a PDF: {exc}") from exc

        password_used: str | None = None
        if fitz_doc.needs_pass:
            if password is None:
                fitz_doc.close()
                raise PasswordRequiredError(f"{path} is password-protected; supply a password")
            if not fitz_doc.authenticate(password):
                fitz_doc.close()
                raise WrongPasswordError(f"the supplied password did not unlock {path}")
            password_used = password

        return cls(fitz_doc, source_path=path, password_used=password_used, file_backed=True)

    @classmethod
    def from_bytes(cls, data: bytes, *, source_path: Path | None = None) -> Self:
        """Open unencrypted bytes (e.g. a render-only copy). Use :meth:`restore` for journal snapshots."""
        return cls(pymupdf.open(stream=data, filetype="pdf"), source_path=source_path, password_used=None)

    def snapshot(self) -> bytes:
        """The current state as bytes that keep the original encryption (SEC-03), for the undo journal."""
        return encrypted_snapshot(self._doc)

    def restore(self, data: bytes) -> Document:
        """A new Document over `data` (a :meth:`snapshot`), logged in with this one's password."""
        fitz_doc = pymupdf.open(stream=data, filetype="pdf")
        if fitz_doc.needs_pass and not fitz_doc.authenticate(self._password_used or ""):
            fitz_doc.close()
            raise WrongPasswordError("could not reopen an encrypted snapshot with the document's password")
        # MuPDF repairs a snapshot it can't fully read back, and a repair drops /Encrypt:
        # never hand back a silently decrypted copy of an encrypted document.
        if encryption_of(self._doc) and not encryption_of(fitz_doc):
            fitz_doc.close()
            raise EncryptionLostError("restoring this encrypted document would have dropped its encryption")
        return type(self)(fitz_doc, source_path=self.source_path, password_used=self._password_used)

    def close(self) -> None:
        self._doc.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- read ------------------------------------------------------------

    @property
    def raw(self) -> pymupdf.Document:
        """The underlying PyMuPDF document, for engine code that needs the full library surface
        (engine.verify's pixel-diff harness, future edit operations). Prefer the typed
        Document methods when they cover what you need."""
        return self._doc

    @property
    def page_count(self) -> int:
        return self._doc.page_count

    @property
    def is_encrypted(self) -> bool:
        return bool(self._doc.is_encrypted) or self._password_used is not None

    @property
    def is_repaired(self) -> bool:
        return bool(self._doc.is_repaired)

    @property
    def password_used(self) -> str | None:
        return self._password_used

    def iter_pages(self) -> Iterator[pymupdf.Page]:
        """Yield pages one at a time (COR-09).

        PyMuPDF loads each page's content on first access, so opening a
        1,000-page document is fast and iterating here never holds more
        than one page's content in memory at a time.
        """
        for index in range(self._doc.page_count):
            yield self._doc[index]

    def render_page(self, index: int, *, dpi: int = DEFAULT_RENDER_DPI) -> bytes:
        """Render one page to PNG bytes (COR-02)."""
        pixmap = self._doc[index].get_pixmap(dpi=dpi)
        return pixmap.tobytes("png")

    def inspect(self, *, password: str = "") -> InspectionReport:
        """Build the COR-03 inspection report for this document."""
        return inspect_document(
            self._doc,
            path=self.source_path or "",
            is_repaired=self.is_repaired,
            password=password,
        )

    def to_bytes(self) -> bytes:
        """The current state as decrypted bytes (for parsing or display), without touching disk
        or this document's own encryption (engine.pdfbytes explains why that matters)."""
        return plain_bytes(self._doc)

    # -- write -------------------------------------------------------------

    def save(
        self,
        path: str | Path | None = None,
        *,
        overwrite: bool = False,
        mode: str = "auto",
        owner_password: str | None = None,
        user_password: str | None = None,
        deterministic: bool = False,
    ) -> SaveResult:
        """Save the document (COR-06, COR-07, COR-08).

        ``path=None`` writes to a new, versioned filename next to the
        source (COR-08) unless ``overwrite=True``, in which case the
        original path is reused. Passing an explicit ``path`` that equals
        ``source_path`` also requires ``overwrite=True``.

        ``mode``: ``"incremental"`` appends changes without touching
        existing objects (used for signed documents so earlier signatures
        stay valid); ``"full"`` rewrites the file with garbage collection;
        ``"auto"`` (default) picks incremental only when the source
        document already carries a signature field, and full rewrite
        otherwise (SPEC.md section 4.2 rule 4).
        """
        if mode not in ("auto", "incremental", "full"):
            raise OpValidationError(f"unknown save mode {mode!r}: use 'auto', 'incremental' or 'full'")
        target = self._resolve_target(path, overwrite=overwrite)
        writes_original = self._is_original(target)
        signed = self._has_signature()
        # Incremental appends to the file this document was read from, so it's only possible
        # when writing back to that file and the document wasn't reloaded from memory since.
        can_append = writes_original and self._file_backed
        if mode == "incremental" and not can_append:
            raise SaveNotPossibleError(
                "an incremental save can only append to the original file, read from disk"
                + ("" if writes_original else "; pass overwrite=True to write back to it")
                + ("" if self._file_backed else "; undo/redo reloaded this document from memory")
            )
        chosen_mode = mode if mode != "auto" else ("incremental" if signed and can_append else "full")
        note = ""
        if signed and chosen_mode == "full":
            note = "this document is signed; a full save invalidates its signatures"
        if deterministic and chosen_mode == "incremental":
            # An incremental save appends to the original file, so its trailer /ID can't be pinned.
            note = (note + "; " if note else "") + "an incremental save is not byte-for-byte reproducible"

        save_kwargs: dict[str, object] = {"encryption": PDF_ENCRYPT_KEEP}
        if deterministic:
            # CMD-07: keep the file's /ID instead of generating a random one, so the same
            # recipe on the same input writes byte-identical output (verified, pymupdf 1.28.2).
            # Unencrypted files only: AES re-encrypts every stream with a fresh random IV on
            # each save, which is part of what makes it secure.
            save_kwargs["no_new_id"] = True
        if owner_password is not None or user_password is not None:
            save_kwargs["owner_pw"] = owner_password or ""
            save_kwargs["user_pw"] = user_password or ""

        tmp = target.with_name(f".{target.name}.pdfworkerz-tmp")
        unsaved = self.snapshot()  # to recover the edits if the file can't be replaced after closing it
        try:
            if chosen_mode == "incremental":
                self._doc.save(str(target), incremental=True, **save_kwargs)
            elif writes_original and self._file_backed:
                # PyMuPDF refuses a full (non-incremental) save back to the file it read from
                # -- the writer can't rewrite a file it's still reading. Write to a sibling
                # temp file and atomically replace the original (SPEC.md section 10: "atomic
                # writes, temp file then rename"), so a crash mid-write never corrupts it.
                self._doc.save(str(tmp), garbage=4, deflate=True, **save_kwargs)
                if deterministic:
                    _pin_second_file_id(tmp)  # on the temp file: the rename below stays the only write to target
                self._doc.close()  # release the read handle on `target` before replacing it (required on Windows)
                Path(tmp).replace(target)
                self._reopen(target, user_password)
            elif writes_original:
                self._doc.save(str(tmp), garbage=4, deflate=True, **save_kwargs)
                if deterministic:
                    _pin_second_file_id(tmp)
                Path(tmp).replace(target)
                self._reopen(target, user_password)
            else:
                # Save As: write from a detached copy. Measured on pymupdf 1.28.2, garbage>=3
                # compacts the live document's xref table but leaves the catalog's /Metadata
                # pointing at the old number, so the next save from it silently dropped the XMP.
                copy = self._detached_copy(unsaved)
                try:
                    copy.save(str(tmp), garbage=4, deflate=True, **save_kwargs)
                finally:
                    copy.close()
                if deterministic:
                    _pin_second_file_id(tmp)
                Path(tmp).replace(target)
        except (OSError, pymupdf.mupdf.FzErrorBase) as exc:
            tmp.unlink(missing_ok=True)
            if self._doc.is_closed:  # closed for the replace, which then failed: keep the edits
                self._doc = self._detached_copy(unsaved)
                self._file_backed = False
            raise SaveFailedError(f"could not write {target}: {exc}") from exc

        return SaveResult(path=target, mode=chosen_mode, bytes_written=target.stat().st_size, note=note)

    def _detached_copy(self, data: bytes) -> pymupdf.Document:
        """An in-memory copy of this document's snapshot bytes, logged in like this one."""
        copy = pymupdf.open(stream=data, filetype="pdf")
        if copy.needs_pass:  # read before authenticate(), see _reopen
            copy.authenticate(self._password_used or "")
        remember_password(copy, self._password_used)
        return copy

    def _reopen(self, target: Path, new_user_password: str | None) -> None:
        """Keep this Document usable after replacing its file: read it back, logged in again."""
        if not self._doc.is_closed:
            self._doc.close()
        self._doc = pymupdf.open(str(target), filetype="pdf")
        password = new_user_password if new_user_password is not None else self._password_used
        # Read needs_pass exactly once, before authenticate(): measured on pymupdf 1.28.2,
        # reading it *after* a successful authenticate() breaks decryption (every page then
        # reads blank, and the next overwrite save wrote that blank document to disk).
        needs_pass = bool(self._doc.needs_pass)
        if needs_pass and not self._doc.authenticate(password or ""):
            raise WrongPasswordError(f"saved {target}, but could not reopen it with the document's password")
        self._password_used = password if needs_pass else self._password_used
        remember_password(self._doc, self._password_used)
        self._file_backed = True

    def _is_original(self, target: Path) -> bool:
        return self.source_path is not None and target.resolve() == self.source_path.resolve()

    def _resolve_target(self, path: str | Path | None, *, overwrite: bool) -> Path:
        if path is None:
            if self.source_path is None:
                raise ValueError("no path given and this document has no source path")
            return self.source_path if overwrite else _default_output_path(self.source_path)
        target = Path(path)
        if self._is_original(target) and not overwrite:
            raise OverwriteRefusedError(
                f"saving to {target} would overwrite the original; pass overwrite=True to allow this"
            )
        return target

    def _has_signature(self) -> bool:
        return any(
            widget.field_type_string == "Signature"
            for page_index in range(self.page_count)
            for widget in self._doc[page_index].widgets()
        )
