"""Regression tests for the independent P1-P3 review (2026-09-28).

Each test reproduces one confirmed defect: an edit or save that corrupted,
lost or silently changed content while still reporting success.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pikepdf
import pymupdf
import pytest

from engine.document import Document
from engine.errors import OpValidationError, SaveNotPossibleError
from engine.fonts.style import extract_page_spans
from engine.ops.journal import UndoRedoJournal
from engine.ops.text import ReplaceSpanTextOp, ReplaceTextOp
from tests.corpus.build_corpus import ENCRYPTED_TEXT, Corpus

ROOT = Path(__file__).resolve().parents[2]


def _helvetica_pdf(path: Path, content: bytes) -> Path:
    pdf = pikepdf.new()
    font = pdf.make_indirect(
        pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica)
    )
    page = pdf.add_blank_page(page_size=(612, 792))
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
    page.Contents = pdf.make_stream(content)
    pdf.save(path)
    return path


def _texts(doc: Document) -> list[str]:
    return [span.style.text for span in extract_page_spans(doc.raw, 0)]


# -- #1: the draw size must include the text matrix and CTM, not just Tf --


@pytest.mark.feature("EDT-01")
@pytest.mark.parametrize(
    "content",
    [
        b"BT /F1 1 Tf 12 0 0 12 72 700 Tm (Hello world) Tj ET",
        b"q 0.1 0 0 0.1 0 0 cm BT /F1 120 Tf 720 7000 Td (Hello world) Tj ET Q",
        b"BT /F1 1 Tf 0.05 Tc 12 0 0 12 72 700 Tm (Hello world) Tj ET",
    ],
    ids=["Tm-scaled", "cm-scaled", "Tm-scaled-with-Tc"],
)
def test_replacement_keeps_the_rendered_size_of_a_matrix_scaled_span(work_dir: Path, content: bytes) -> None:
    doc = Document.open(_helvetica_pdf(work_dir / "scaled.pdf", content))
    before = extract_page_spans(doc.raw, 0)[0].style
    ReplaceSpanTextOp(page_index=0, span_index=0, new_text=before.text, require_tier="exact").apply(doc)
    # Non-default Tc is drawn one character at a time, so measure across every span now on the page.
    after = [span.style for span in extract_page_spans(doc.raw, 0)]
    assert "".join(style.text for style in after) == before.text
    assert all(style.size == pytest.approx(before.size, abs=0.05) for style in after)
    # Same text, font and size: the redrawn line must be as wide as the original, Tc included.
    width = max(style.bbox[2] for style in after) - min(style.bbox[0] for style in after)
    assert width == pytest.approx(before.bbox[2] - before.bbox[0], abs=0.5)
    doc.close()


# -- #2: redacting one line must never remove a neighbouring line --


def _three_lines(path: Path, leading: float) -> Path:
    doc = pymupdf.open()
    page = doc.new_page()
    for i, text in enumerate(["Line one gyp Tall", "Line, two. gyp_ jumps", "Line three Tall Hj"]):
        page.insert_text((72, 100 + i * leading), text, fontsize=12, fontname="helv")
    doc.save(path)
    return path


@pytest.mark.feature("EDT-01")
@pytest.mark.parametrize("leading", [11.0, 13.0, 14.4])
def test_editing_a_line_leaves_tightly_spaced_neighbours_intact(work_dir: Path, leading: float) -> None:
    doc = Document.open(_three_lines(work_dir / "tight.pdf", leading))
    ReplaceSpanTextOp(page_index=0, span_index=1, new_text="Line TWO", require_tier="fallback").apply(doc)
    assert sorted(_texts(doc)) == sorted(["Line one gyp Tall", "Line TWO", "Line three Tall Hj"])
    doc.close()


@pytest.mark.feature("EDT-01")
def test_an_edit_whose_redaction_would_hit_other_text_is_refused_and_rolled_back(work_dir: Path) -> None:
    # Two different spans drawn over each other: no redaction can remove one without the other.
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "OVERLAP", fontsize=12, fontname="helv")
    page.insert_text((72, 100), "overlap", fontsize=12, fontname="tiro")
    doc.save(work_dir / "overlap.pdf")

    document = Document.open(work_dir / "overlap.pdf")
    journal = UndoRedoJournal(document)
    with pytest.raises(OpValidationError, match="also remove"):
        journal.record(ReplaceSpanTextOp(page_index=0, span_index=0, new_text="changed", require_tier="fallback"))
    assert sorted(_texts(journal.document)) == ["OVERLAP", "overlap"]
    journal.document.close()


# -- #3: two different fonts edited on one page must not share a font resource --


@pytest.mark.feature("EDT-01")
def test_editing_spans_in_two_embedded_fonts_keeps_each_font(work_dir: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_font(fontname="VR", fontfile=str(ROOT / "assets" / "fonts" / "Vera.ttf"))
    page.insert_font(fontname="VB", fontfile=str(ROOT / "assets" / "fonts" / "VeraBd.ttf"))
    page.insert_text((72, 100), "Regular line", fontsize=14, fontname="VR")
    page.insert_text((72, 200), "Bold line", fontsize=14, fontname="VB")
    doc.subset_fonts()
    doc.save(work_dir / "two_fonts.pdf")

    document = Document.open(work_dir / "two_fonts.pdf")
    first = ReplaceSpanTextOp(page_index=0, span_index=0, new_text="Regular QZX", require_tier="exact").apply(document)
    bold_index = _texts(document).index("Bold line")
    second = ReplaceSpanTextOp(page_index=0, span_index=bold_index, new_text="Bold WKJ", require_tier="exact").apply(
        document
    )
    assert first.verification is not None and first.verification.text_matches
    assert second.verification is not None and second.verification.text_matches
    fonts = {span.style.text: span.style.font for span in extract_page_spans(document.raw, 0)}
    assert "Bold" in fonts["Bold WKJ"] and "Bold" not in fonts["Regular QZX"]
    document.close()


# -- #4: a replacement that contains its own match must terminate --


@pytest.mark.feature("EDT-02")
def test_replacing_with_text_that_contains_the_match_replaces_each_occurrence_once(work_dir: Path) -> None:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "Hello Hello world", fontsize=12, fontname="helv")
    doc.save(work_dir / "hello.pdf")

    document = Document.open(work_dir / "hello.pdf")
    results = ReplaceTextOp(match="Hello", replacement="Hello there", require_tier="fallback", verify=False).apply(
        document
    )
    assert len(results) == 1  # one span, both of its matches replaced in one redraw
    assert _texts(document) == ["Hello there Hello there world"]
    document.close()


# -- #5: undo, redo and a failed Op must keep the document encrypted --


def _open_encrypted_copy(corpus: Corpus, work_dir: Path) -> Document:
    source = work_dir / "aes256.pdf"
    shutil.copy(corpus.encrypted_aes_256, source)
    return Document.open(source, password=corpus.user_password)


def _saved_needs_password(path: Path) -> bool:
    with pymupdf.open(path) as saved:
        return bool(saved.needs_pass)


def _saved_text(path: Path, password: str) -> str:
    with pymupdf.open(path) as saved:
        assert saved.authenticate(password)
        return str(saved[0].get_text()).strip()


@pytest.mark.feature("SEC-03")
def test_an_edited_encrypted_document_is_saved_encrypted(corpus: Corpus, work_dir: Path) -> None:
    # Any plain tobytes() inside an edit used to strip the encryption from every later save.
    doc = _open_encrypted_copy(corpus, work_dir)
    ReplaceTextOp(match="quarterly", replacement="annual", require_tier="fallback").apply(doc)
    doc.save(work_dir / "edited.pdf")
    assert _saved_needs_password(work_dir / "edited.pdf")
    assert _saved_text(work_dir / "edited.pdf", corpus.user_password) == "Confidential: annual figures"
    doc.close()


@pytest.mark.feature("SEC-03")
@pytest.mark.feature("COR-05")
def test_undo_and_redo_keep_the_original_encryption(corpus: Corpus, work_dir: Path) -> None:
    journal = UndoRedoJournal(_open_encrypted_copy(corpus, work_dir))
    journal.record(ReplaceTextOp(match="quarterly", replacement="annual", require_tier="fallback", verify=False))
    journal.undo()
    assert journal.document.is_encrypted
    journal.document.save(work_dir / "after_undo.pdf")
    assert _saved_needs_password(work_dir / "after_undo.pdf")
    assert _saved_text(work_dir / "after_undo.pdf", corpus.user_password) == ENCRYPTED_TEXT

    journal.redo()
    journal.document.save(work_dir / "after_redo.pdf")
    assert _saved_needs_password(work_dir / "after_redo.pdf")
    assert _saved_text(work_dir / "after_redo.pdf", corpus.user_password) == "Confidential: annual figures"
    journal.document.close()


@pytest.mark.feature("SEC-03")
@pytest.mark.feature("COR-05")
def test_a_failed_op_keeps_the_original_encryption(corpus: Corpus, work_dir: Path) -> None:
    journal = UndoRedoJournal(_open_encrypted_copy(corpus, work_dir))
    with pytest.raises(OpValidationError):
        journal.record(ReplaceSpanTextOp(page_index=0, span_index=9999, new_text="x"))
    journal.document.save(work_dir / "after_failure.pdf")
    assert _saved_needs_password(work_dir / "after_failure.pdf")
    assert _saved_text(work_dir / "after_failure.pdf", corpus.user_password) == ENCRYPTED_TEXT
    journal.document.close()


# -- #6: a signed document must be savable with the defaults --


@pytest.mark.feature("COR-06")
def test_default_save_of_a_signed_document_writes_a_new_file(corpus: Corpus, work_dir: Path) -> None:
    source = work_dir / "signed.pdf"
    shutil.copy(corpus.form_and_signature, source)
    doc = Document.open(source)
    result = doc.save()
    assert result.path != source and result.path.exists()
    assert result.mode == "full"
    assert "signature" in result.note  # the caller is told the signature no longer covers the file
    doc.close()


@pytest.mark.feature("COR-06")
def test_incremental_overwrite_after_undo_is_refused_clearly_or_done(corpus: Corpus, work_dir: Path) -> None:
    source = work_dir / "signed.pdf"
    shutil.copy(corpus.form_and_signature, source)
    journal = UndoRedoJournal(Document.open(source))
    journal.record(ReplaceTextOp(match="zzzz-not-present", replacement="b"))
    journal.undo()
    # The journal reloaded from memory, so an incremental append to the file is no longer possible.
    with pytest.raises(SaveNotPossibleError):
        journal.document.save(overwrite=True, mode="incremental")
    result = journal.document.save(overwrite=True)  # auto: falls back to a full save and says so
    assert result.mode == "full" and "signature" in result.note
    journal.document.close()


# -- #7: an overwrite save of an encrypted file must leave the document usable --


@pytest.mark.feature("COR-07")
@pytest.mark.feature("SEC-03")
def test_overwrite_save_of_an_encrypted_document_keeps_it_open(corpus: Corpus, work_dir: Path) -> None:
    doc = _open_encrypted_copy(corpus, work_dir)
    doc.save(overwrite=True)
    assert doc.page_count >= 1
    doc.render_page(0, dpi=36)  # used to raise "document closed or encrypted"
    assert _saved_needs_password(work_dir / "aes256.pdf")
    doc.close()


# -- #13: the overwrite guard must compare resolved paths --


@pytest.mark.feature("COR-08")
def test_overwrite_guard_sees_through_relative_and_absolute_spellings(corpus: Corpus, work_dir: Path) -> None:
    source = work_dir / "orig.pdf"
    shutil.copy(corpus.simple, source)
    cwd = Path.cwd()
    os.chdir(work_dir)
    try:
        doc = Document.open("orig.pdf")
        from engine.errors import OverwriteRefusedError

        with pytest.raises(OverwriteRefusedError):
            doc.save(source.resolve())
        doc.close()
    finally:
        os.chdir(cwd)


# -- #12: bounds checks on a rotated page use its unrotated coordinates --


def _rotated_page(path: Path) -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text((72, 780), "bottom text", fontsize=12)
    page.set_rotation(90)
    doc.save(path)
    return path


@pytest.mark.feature("EDT-05")
def test_moving_text_on_a_rotated_page_checks_the_real_page_bounds(work_dir: Path) -> None:
    from engine.ops.text import MoveTextBlockOp

    doc = Document.open(_rotated_page(work_dir / "rotated.pdf"))
    MoveTextBlockOp(page_index=0, span_index=0, dy=1, require_tier="fallback").apply(doc)  # was refused
    with pytest.raises(OpValidationError, match="off the page"):
        MoveTextBlockOp(page_index=0, span_index=0, dx=650, require_tier="fallback").apply(doc)  # was accepted
    doc.close()


@pytest.mark.feature("EDT-10")
def test_a_link_low_on_a_rotated_page_is_accepted(work_dir: Path) -> None:
    from engine.ops.links import AddLinkOp

    doc = Document.open(_rotated_page(work_dir / "rotated.pdf"))
    AddLinkOp(page_index=0, rect=(72, 690, 200, 710), uri="https://example.com").apply(doc)
    assert len(doc.raw[0].get_links()) == 1
    doc.close()


# -- #10: verification flags a change outside the edited area --


@pytest.mark.feature("FNT-12")
def test_verification_is_clean_for_an_ordinary_edit(work_dir: Path) -> None:
    doc = Document.open(_three_lines(work_dir / "three.pdf", 14.4))
    result = ReplaceSpanTextOp(page_index=0, span_index=1, new_text="Line TWO", require_tier="fallback").apply(doc)
    assert result.verification is not None
    assert result.verification.outside_changed_fraction == 0.0
    assert result.verification.looks_right
    doc.close()


@pytest.mark.feature("FNT-12")
def test_verification_flags_a_change_outside_the_edited_area(work_dir: Path) -> None:
    from engine.edit import _capture, _verify_edit

    doc = Document.open(_three_lines(work_dir / "three.pdf", 14.4))
    before = _capture(doc, 0, verify=True)
    assert before is not None
    doc.raw[0].draw_rect(pymupdf.Rect(300, 400, 400, 500), color=(1, 0, 0), fill=(1, 0, 0))  # collateral damage
    result = _verify_edit(doc, 0, before, "Line one", removed=[])
    assert result.outside_changed_fraction > 0.0
    assert not result.looks_right
    doc.close()


# -- EDT-02 whole word, #15 page prefilter, #16 missing text state --


@pytest.mark.feature("EDT-02")
def test_whole_word_matches_only_whole_words_including_at_punctuation(work_dir: Path) -> None:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "cat category (cat) scat", fontsize=12, fontname="helv")
    doc.save(work_dir / "cats.pdf")
    document = Document.open(work_dir / "cats.pdf")
    ReplaceTextOp(match="cat", replacement="dog", whole_word=True, require_tier="fallback").apply(document)
    assert _texts(document) == ["dog category (dog) scat"]
    document.close()


@pytest.mark.feature("EDT-02")
def test_pages_without_a_match_are_skipped_without_changing_results(corpus: Corpus) -> None:
    doc = Document.open(corpus.multi_page)
    results = ReplaceTextOp(match="Page 3 of", replacement="Sheet 3 of", require_tier="fallback").apply(doc)
    assert len(results) == 1
    assert doc.raw[2].get_text().strip().startswith("Sheet 3 of")
    assert doc.raw[1].get_text().strip().startswith("Page 2 of")
    doc.close()


@pytest.mark.feature("EDT-01")
def test_an_edit_without_readable_text_state_says_so(work_dir: Path) -> None:
    from engine.edit import replace_span_text
    from engine.ops.text import _font_index

    doc = Document.open(_three_lines(work_dir / "three.pdf", 14.4))
    span = extract_page_spans(doc.raw, 0)[0].model_copy(update={"text_state": None})
    result = replace_span_text(doc, 0, span, "Line ONE", font_index=_font_index(), verify=False)
    assert "could not be read" in result.note
    doc.close()
