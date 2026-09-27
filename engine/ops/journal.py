"""Undo/redo journal (COR-05).

Generic over any :class:`~engine.ops.base.Op`: it does not know or care what
an operation does, only that applying it mutates the document. Undo and
redo work by snapshotting the document's bytes before and after each
recorded operation and reloading from those snapshots -- correct
regardless of what future edit operations (P2 onward) turn out to do, and
simple enough to verify completely now, before any of them exist.

History is capped at ``max_history`` entries (oldest dropped first) so a
long editing session cannot grow the journal without bound.
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.document import Document
from engine.errors import NothingToUndoError
from engine.ops.base import Op

DEFAULT_MAX_HISTORY = 100


@dataclass(frozen=True)
class JournalEntry:
    op: Op
    before: bytes
    after: bytes


class UndoRedoJournal:
    """Wraps a :class:`Document`, recording every Op applied through it."""

    def __init__(self, document: Document, *, max_history: int = DEFAULT_MAX_HISTORY) -> None:
        self._document = document
        self._max_history = max_history
        self._undo_stack: list[JournalEntry] = []
        self._redo_stack: list[JournalEntry] = []

    @property
    def document(self) -> Document:
        """The current, live document. Reassigned in place by undo/redo, so always re-read this property."""
        return self._document

    @property
    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo_stack)

    @property
    def history(self) -> list[Op]:
        """The Ops applied so far, oldest first (exportable as a recipe once CMD-07 lands)."""
        return [entry.op for entry in self._undo_stack]

    def record(self, op: Op) -> object:
        """Apply ``op`` to the current document and record it for undo."""
        before = self._document.to_bytes()
        result = op.apply(self._document)
        after = self._document.to_bytes()

        self._undo_stack.append(JournalEntry(op=op, before=before, after=after))
        if len(self._undo_stack) > self._max_history:
            del self._undo_stack[0]
        self._redo_stack.clear()
        return result

    def undo(self) -> Op:
        """Revert the most recent Op. Returns the Op that was undone."""
        if not self._undo_stack:
            raise NothingToUndoError("no operation to undo")
        entry = self._undo_stack.pop()
        self._reload(entry.before)
        self._redo_stack.append(entry)
        return entry.op

    def redo(self) -> Op:
        """Reapply the most recently undone Op. Returns the Op that was redone."""
        if not self._redo_stack:
            raise NothingToUndoError("no operation to redo")
        entry = self._redo_stack.pop()
        self._reload(entry.after)
        self._undo_stack.append(entry)
        return entry.op

    def _reload(self, data: bytes) -> None:
        source_path = self._document.source_path
        self._document.close()
        self._document = Document.from_bytes(data, source_path=source_path)
