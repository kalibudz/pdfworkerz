"""The Document model: open, authenticate, repair, render and save (COR-01,
COR-02, COR-06, COR-07, COR-08, COR-09, SEC-01, SEC-02, SEC-03, SEC-07, OPT-06).

Every capability in this module has exactly one code path (SPEC.md section
4.2 rule 1): the CLI, the future server and every test call the same
:class:`Document` methods. PyMuPDF (`pymupdf`) is the source of truth for
the open document; pikepdf is used only for the lower-level encryption
inspection that PyMuPDF does not expose (see engine.security).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Self

import pymupdf

from engine.errors import (
    CertificateEncryptedError,
    DocumentNotFoundError,
    NotAPdfError,
    OverwriteRefusedError,
    PasswordRequiredError,
    RepairFailedError,
    WrongPasswordError,
)
from engine.inspect import InspectionReport, inspect_document
from engine.security import detect_certificate_encryption

DEFAULT_RENDER_DPI = 150
_EDITED_SUFFIX = "edited"
_PDF_HEADER = b"%PDF-"
# Verified at runtime (pymupdf 1.28.2): PDF_ENCRYPT_KEEP == 0, telling save() to
# reuse the document's existing encryption. It's missing from pymupdf's stub, so
# mypy sees no such attribute even though it's real -- see pyproject.toml's
# mypy override note for engine.*/cli.*.
_PDF_ENCRYPT_KEEP: int = pymupdf.PDF_ENCRYPT_KEEP  # type: ignore[attr-defined]


@dataclass(frozen=True)
class SaveResult:
    """What COR-06/COR-07/COR-08 report back after a save."""

    path: Path
    mode: str  # "incremental" | "full"
    bytes_written: int


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

    def __init__(self, fitz_doc: pymupdf.Document, *, source_path: Path | None, password_used: str | None) -> None:
        self._doc = fitz_doc
        self.source_path = source_path
        self._password_used = password_used

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

        return cls(fitz_doc, source_path=path, password_used=password_used)

    @classmethod
    def from_bytes(cls, data: bytes, *, source_path: Path | None = None) -> Self:
        """Reopen a document from an in-memory snapshot (used by the undo/redo journal)."""
        return cls(pymupdf.open(stream=data, filetype="pdf"), source_path=source_path, password_used=None)

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

    def to_bytes(self, **save_kwargs: object) -> bytes:
        """Serialize the current document state to bytes without touching disk."""
        return self._doc.tobytes(**save_kwargs)

    # -- write -------------------------------------------------------------

    def save(
        self,
        path: str | Path | None = None,
        *,
        overwrite: bool = False,
        mode: str = "auto",
        owner_password: str | None = None,
        user_password: str | None = None,
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
        target = self._resolve_target(path, overwrite=overwrite)
        chosen_mode = mode if mode != "auto" else self._auto_save_mode()

        save_kwargs: dict[str, object] = {"encryption": _PDF_ENCRYPT_KEEP}
        if owner_password is not None or user_password is not None:
            save_kwargs["owner_pw"] = owner_password or ""
            save_kwargs["user_pw"] = user_password or ""

        if chosen_mode == "incremental":
            if target != self.source_path:
                raise ValueError("incremental save requires writing back to the original path")
            self._doc.save(str(target), incremental=True, **save_kwargs)
        elif target == self.source_path:
            # PyMuPDF refuses a full (non-incremental) save back to the file it read from
            # -- the writer can't rewrite a file it's still reading. Write to a sibling
            # temp file and atomically replace the original (SPEC.md section 10: "atomic
            # writes, temp file then rename"), so a crash mid-write never corrupts it.
            tmp = target.with_name(f".{target.name}.pdfworkerz-tmp")
            self._doc.save(str(tmp), garbage=4, deflate=True, **save_kwargs)
            self._doc.close()  # release the read handle on `target` before replacing it (required on Windows)
            Path(tmp).replace(target)
            self._doc = pymupdf.open(str(target), filetype="pdf")  # keep this Document usable after an overwrite
        else:
            self._doc.save(str(target), garbage=4, deflate=True, **save_kwargs)

        return SaveResult(path=target, mode=chosen_mode, bytes_written=target.stat().st_size)

    def _resolve_target(self, path: str | Path | None, *, overwrite: bool) -> Path:
        if path is None:
            if self.source_path is None:
                raise ValueError("no path given and this document has no source path")
            return self.source_path if overwrite else _default_output_path(self.source_path)
        target = Path(path)
        if target == self.source_path and not overwrite:
            raise OverwriteRefusedError(
                f"saving to {target} would overwrite the original; pass overwrite=True to allow this"
            )
        return target

    def _auto_save_mode(self) -> str:
        for page_index in range(self.page_count):
            for widget in self._doc[page_index].widgets():
                if widget.field_type_string == "Signature":
                    return "incremental"
        return "full"
