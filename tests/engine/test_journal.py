"""COR-05: the undo/redo journal.

The journal is generic over any Op that mutates a Document; it doesn't need
to know what an operation does, only that applying it changes the
document's bytes. No edit Op exists yet (those start in phase P2), so this
suite exercises the journal with a small test-double Op defined below,
``_InsertLineOp`` -- a stand-in for a real editing Op, not a feature in its
own right.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Literal

import pytest

from engine.document import Document
from engine.errors import NothingToUndoError
from engine.ops.base import Op, register_op
from engine.ops.journal import UndoRedoJournal
from tests.corpus.build_corpus import Corpus


@register_op
class _InsertLineOp(Op):
    """Test double only: inserts a line of text. Stands in for a P2 edit Op."""

    op: Literal["_test_insert_line"] = "_test_insert_line"
    text: str

    def apply(self, document: Document) -> None:
        page = next(document.iter_pages())
        page.insert_text((72, 400), self.text)


@pytest.fixture
def journal(corpus: Corpus) -> Iterator[UndoRedoJournal]:
    doc = Document.open(corpus.simple)
    j = UndoRedoJournal(doc)
    try:
        yield j
    finally:
        j.document.close()


@pytest.mark.feature("COR-05")
def test_new_journal_has_nothing_to_undo_or_redo(journal: UndoRedoJournal) -> None:
    assert journal.can_undo is False
    assert journal.can_redo is False
    assert journal.history == []


@pytest.mark.feature("COR-05")
def test_undo_or_redo_with_empty_stack_raises(journal: UndoRedoJournal) -> None:
    with pytest.raises(NothingToUndoError):
        journal.undo()
    with pytest.raises(NothingToUndoError):
        journal.redo()


@pytest.mark.feature("COR-05")
def test_record_applies_the_op_and_tracks_history(journal: UndoRedoJournal) -> None:
    journal.record(_InsertLineOp(text="first edit"))
    assert journal.can_undo is True
    assert journal.can_redo is False
    texts = [op.text for op in journal.history]  # type: ignore[attr-defined]
    assert texts == ["first edit"]
    assert "first edit" in next(journal.document.iter_pages()).get_text()


@pytest.mark.feature("COR-05")
def test_undo_restores_the_exact_prior_state(journal: UndoRedoJournal) -> None:
    before_text = next(journal.document.iter_pages()).get_text()
    journal.record(_InsertLineOp(text="added text"))
    journal.undo()
    after_text = next(journal.document.iter_pages()).get_text()
    assert after_text == before_text
    assert "added text" not in after_text


@pytest.mark.feature("COR-05")
def test_redo_reapplies_the_undone_op(journal: UndoRedoJournal) -> None:
    journal.record(_InsertLineOp(text="added text"))
    journal.undo()
    journal.redo()
    assert "added text" in next(journal.document.iter_pages()).get_text()
    assert journal.can_redo is False


@pytest.mark.feature("COR-05")
def test_recording_a_new_op_clears_the_redo_stack(journal: UndoRedoJournal) -> None:
    journal.record(_InsertLineOp(text="one"))
    journal.undo()
    assert journal.can_redo is True
    journal.record(_InsertLineOp(text="two"))
    assert journal.can_redo is False  # the redo branch (re-doing "one") is gone


@pytest.mark.feature("COR-05")
def test_multi_step_undo_redo_sequence_is_consistent(journal: UndoRedoJournal) -> None:
    journal.record(_InsertLineOp(text="a"))
    journal.record(_InsertLineOp(text="b"))
    journal.record(_InsertLineOp(text="c"))

    journal.undo()
    journal.undo()
    text = next(journal.document.iter_pages()).get_text()
    assert "a" in text and "b" not in text and "c" not in text

    journal.redo()
    text = next(journal.document.iter_pages()).get_text()
    assert "a" in text and "b" in text and "c" not in text

    assert len(journal.history) == 2  # "a" and "b" are applied; "c" was never redone


@pytest.mark.feature("COR-05")
def test_history_is_capped_at_max_history() -> None:
    from tests.corpus.build_corpus import build_corpus

    corpus = build_corpus()
    doc = Document.open(corpus.simple)
    small_journal = UndoRedoJournal(doc, max_history=3)
    try:
        for i in range(5):
            small_journal.record(_InsertLineOp(text=f"line {i}"))
        assert len(small_journal.history) == 3
        texts = [op.text for op in small_journal.history]  # type: ignore[attr-defined]
        assert texts == ["line 2", "line 3", "line 4"]
    finally:
        small_journal.document.close()
