"""UI-12: files the browser sends up, held in a private temporary folder for this server run.

A person picks or drops a file instead of typing a path on the machine running the server. The
bytes are streamed to disk (never held whole in memory), capped in size, and given a name that
cannot point anywhere but inside the upload folder. Nothing outlives the server: the folder is
removed when the store is collected.
"""

from __future__ import annotations

import re
import tempfile
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from engine.errors import PdfWorkerzError

DEFAULT_MAX_UPLOAD_BYTES = 512 * 1024 * 1024
_UNSAFE = re.compile(r'[\x00-\x1f<>:"/\\|?*]')


class UploadTooLargeError(PdfWorkerzError):
    """The file is larger than the server accepts."""


class UploadNotFoundError(PdfWorkerzError):
    """No upload has that id (it may belong to an earlier server run)."""


class UploadNameError(PdfWorkerzError):
    """The file has no usable name."""


@dataclass(frozen=True)
class StoredUpload:
    upload_id: str
    name: str
    path: Path
    size: int


def safe_name(raw: str) -> str:
    """The file's own name, with any folders, control characters and characters Windows forbids
    removed -- ``..\\..\\evil.pdf`` becomes ``evil.pdf`` -- and never empty."""
    name = _UNSAFE.sub("_", Path(raw.replace("\\", "/")).name).strip(" .")
    if not name:
        raise UploadNameError("the file has no usable name")
    return name[:200]


class UploadStore:
    """Uploaded files for one server run."""

    def __init__(self, max_bytes: int = DEFAULT_MAX_UPLOAD_BYTES) -> None:
        self.max_bytes = max_bytes
        self._folder = tempfile.TemporaryDirectory(prefix="pdfworkerz-uploads-")
        self._items: dict[str, StoredUpload] = {}

    @property
    def folder(self) -> Path:
        return Path(self._folder.name)

    async def save(self, raw_name: str, chunks: AsyncIterator[bytes]) -> StoredUpload:
        """Write the stream to a new file; refuse (and remove it) past the size cap."""
        name = safe_name(raw_name)
        upload_id = uuid.uuid4().hex
        target = self.folder / upload_id / name
        target.parent.mkdir()
        size = 0
        with target.open("wb") as out:
            async for chunk in chunks:
                size += len(chunk)
                if size > self.max_bytes:
                    out.close()
                    target.unlink()
                    raise UploadTooLargeError(
                        f"{name} is larger than the {self.max_bytes // (1024 * 1024)} MB this server accepts"
                    )
                out.write(chunk)
        stored = StoredUpload(upload_id, name, target, size)
        self._items[upload_id] = stored
        return stored

    def add_bytes(self, name: str, data: bytes) -> StoredUpload:
        """Keep a file the server made itself (a converted PDF) alongside the uploads."""
        upload_id = uuid.uuid4().hex
        target = self.folder / upload_id / safe_name(name)
        target.parent.mkdir()
        target.write_bytes(data)
        stored = StoredUpload(upload_id, target.name, target, len(data))
        self._items[upload_id] = stored
        return stored

    def get(self, upload_id: str) -> StoredUpload:
        try:
            return self._items[upload_id]
        except KeyError:
            raise UploadNotFoundError(f"no uploaded file with id {upload_id!r}; send it again") from None
