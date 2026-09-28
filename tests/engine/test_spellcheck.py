"""EDT-11: offline spell-check with Hunspell dictionaries."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from engine.document import Document
from engine.errors import OpValidationError
from engine.fonts.style import extract_page_spans
from engine.ops.base import parse_op
from engine.ops.spellcheck import CorrectWordOp, SpellCheckOp
from engine.spellcheck import available_languages, check_word, suggest


@pytest.fixture
def doc(work_dir: Path) -> Document:
    raw = pymupdf.open()
    page = raw.new_page()
    page.insert_text((72, 100), "We recieve teh report on Monday.", fontsize=12, fontname="helv")
    page.insert_text((72, 130), "NASA and PDFWorkerz shipped v2 in 2024; it's fine.", fontsize=12, fontname="helv")
    page.insert_text((72, 160), "A well-known colour", fontsize=12, fontname="helv")
    path = work_dir / "spelling.pdf"
    raw.save(path)
    raw.close()
    return Document.open(path)


@pytest.mark.feature("EDT-11")
def test_the_bundled_english_dictionary_is_available() -> None:
    assert "en_US" in available_languages()
    assert check_word("paragraph")
    assert not check_word("recieve")
    assert suggest("recieve")[0] == "receive"


@pytest.mark.feature("EDT-11")
def test_check_page_finds_misspellings_with_positions_and_suggestions(doc: Document) -> None:
    found = SpellCheckOp(page_index=0).apply(doc)
    words = [m.word for m in found]
    assert words == ["recieve", "teh", "colour"]  # en_US: "colour" is British

    recieve = found[0]
    span = extract_page_spans(doc.raw, 0)[recieve.span_index]
    assert span.style.text[recieve.start : recieve.end] == "recieve"
    assert "receive" in recieve.suggestions
    assert span.style.bbox[0] < recieve.bbox[0] < recieve.bbox[2] < span.style.bbox[2]  # just the word


@pytest.mark.feature("EDT-11")
def test_acronyms_mixed_case_names_numbers_and_contractions_are_not_flagged(doc: Document) -> None:
    flagged = {m.word for m in SpellCheckOp(page_index=0).apply(doc)}
    assert not flagged & {"NASA", "PDFWorkerz", "v2", "2024", "it's", "well-known"}


@pytest.mark.feature("EDT-11")
def test_ignored_words_are_accepted_case_insensitively(doc: Document) -> None:
    found = SpellCheckOp(page_index=0, ignore=["TEH", "colour"]).apply(doc)
    assert [m.word for m in found] == ["recieve"]


@pytest.mark.feature("EDT-11")
def test_correct_word_replaces_only_that_word_in_style(doc: Document) -> None:
    recieve = SpellCheckOp(page_index=0).apply(doc)[0]
    result = CorrectWordOp(
        page_index=0,
        span_index=recieve.span_index,
        start=recieve.start,
        end=recieve.end,
        word=recieve.word,
        replacement="receive",
    ).apply(doc)
    assert result.tier == "exact"
    texts = [span.style.text for span in extract_page_spans(doc.raw, 0)]
    assert "We receive teh report on Monday." in texts
    assert [m.word for m in SpellCheckOp(page_index=0).apply(doc)] == ["teh", "colour"]


@pytest.mark.feature("EDT-11")
def test_a_stale_correction_is_refused(doc: Document) -> None:
    teh = SpellCheckOp(page_index=0).apply(doc)[1]
    with pytest.raises(OpValidationError, match="no longer"):
        CorrectWordOp(
            page_index=0, span_index=teh.span_index, start=teh.start + 1, end=teh.end + 1, word="teh", replacement="the"
        ).apply(doc)


@pytest.mark.feature("EDT-11")
def test_an_unknown_language_is_refused(doc: Document) -> None:
    with pytest.raises(OpValidationError, match="no dictionary"):
        SpellCheckOp(page_index=0, language="xx_XX").apply(doc)


@pytest.mark.feature("EDT-11")
def test_spell_ops_round_trip_through_json() -> None:
    for op in (
        SpellCheckOp(page_index=1, ignore=["foo"]),
        CorrectWordOp(page_index=0, span_index=1, start=2, end=5, word="teh", replacement="the"),
    ):
        assert parse_op(op.model_dump()) == op
