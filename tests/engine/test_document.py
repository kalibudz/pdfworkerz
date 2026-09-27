"""COR-01 (open/parse), COR-08 (never overwrite), COR-09 (large documents), OPT-06 (repair)."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest

from engine.document import Document
from engine.errors import DocumentNotFoundError, NotAPdfError, RepairFailedError
from tests.corpus.build_corpus import Corpus


@pytest.mark.feature("COR-01")
def test_open_reads_page_count_and_closes_cleanly(corpus: Corpus) -> None:
    with Document.open(corpus.multi_page) as doc:
        assert doc.page_count == corpus.multi_page_count
        assert not doc.is_repaired
        assert not doc.is_encrypted


@pytest.mark.feature("COR-01")
def test_open_missing_file_raises(work_dir: Path) -> None:
    with pytest.raises(DocumentNotFoundError):
        Document.open(work_dir / "does-not-exist.pdf")


@pytest.mark.feature("COR-01")
def test_open_refuses_a_non_pdf_file_even_with_a_pdf_like_extension(work_dir: Path) -> None:
    fake = work_dir / "not_really.pdf"
    fake.write_text("this is not a PDF")
    with pytest.raises(NotAPdfError):
        Document.open(fake)


@pytest.mark.feature("COR-01")
def test_open_does_not_misuse_a_non_pdf_extension_as_a_type_hint(work_dir: Path) -> None:
    """PyMuPDF can render .txt/.xps/.epub content; open() must still demand strict PDF parsing."""
    text_file = work_dir / "notes.txt"
    text_file.write_text("hello, this happens to be openable by MuPDF's generic loader")
    with pytest.raises(NotAPdfError):
        Document.open(text_file)


@pytest.mark.feature("COR-01")
def test_from_bytes_round_trips_document_state(corpus: Corpus) -> None:
    with Document.open(corpus.simple) as doc:
        data = doc.to_bytes()
    reopened = Document.from_bytes(data)
    try:
        assert reopened.page_count == 1
    finally:
        reopened.close()


@pytest.mark.feature("COR-08")
def test_default_save_never_touches_the_source_file(corpus: Corpus, work_dir: Path) -> None:
    source = work_dir / "input.pdf"
    shutil.copy(corpus.simple, source)
    original_bytes = source.read_bytes()

    with Document.open(source) as doc:
        result = doc.save()

    assert result.path != source
    assert result.path.name == "input.edited.pdf"
    assert source.read_bytes() == original_bytes  # untouched


@pytest.mark.feature("COR-08")
def test_repeated_default_saves_get_distinct_versioned_names(corpus: Corpus, work_dir: Path) -> None:
    source = work_dir / "input.pdf"
    shutil.copy(corpus.simple, source)

    names = set()
    for _ in range(3):
        with Document.open(source) as doc:
            names.add(doc.save().path.name)
    assert names == {"input.edited.pdf", "input.edited.2.pdf", "input.edited.3.pdf"}


@pytest.mark.feature("COR-08")
def test_explicit_path_equal_to_source_requires_overwrite_opt_in(corpus: Corpus, work_dir: Path) -> None:
    from engine.errors import OverwriteRefusedError

    source = work_dir / "input.pdf"
    shutil.copy(corpus.simple, source)
    with Document.open(source) as doc:
        with pytest.raises(OverwriteRefusedError):
            doc.save(source)
        result = doc.save(source, overwrite=True)  # explicit opt-in succeeds
        assert result.path == source


@pytest.mark.feature("COR-09")
def test_opening_a_1000_page_document_is_fast(corpus: Corpus) -> None:
    start = time.perf_counter()
    with Document.open(corpus.large) as doc:
        elapsed = time.perf_counter() - start
        assert doc.page_count == corpus.large_page_count
        assert elapsed < 5.0, f"opening 1000 pages took {elapsed:.2f}s"


@pytest.mark.feature("COR-09")
def test_iter_pages_is_lazy_and_visits_every_page_once(corpus: Corpus) -> None:
    with Document.open(corpus.large) as doc:
        seen = 0
        for _page in doc.iter_pages():
            seen += 1
            if seen == 3:  # never demand the whole generator; laziness means this must be cheap
                break
        assert seen == 3
        total = sum(1 for _ in doc.iter_pages())
        assert total == corpus.large_page_count


@pytest.mark.feature("OPT-06")
def test_open_repairs_a_truncated_file_transparently(corpus: Corpus) -> None:
    with Document.open(corpus.broken_truncated) as doc:
        assert doc.is_repaired
        assert doc.page_count == corpus.multi_page_count


@pytest.mark.feature("OPT-06")
def test_open_raises_a_distinct_error_when_repair_is_impossible(corpus: Corpus) -> None:
    with pytest.raises(RepairFailedError):
        Document.open(corpus.broken_severely)


@pytest.mark.feature("OPT-06")
def test_repair_failure_is_distinguished_from_not_a_pdf_at_all(corpus: Corpus, work_dir: Path) -> None:
    """A damaged PDF and a non-PDF file must raise different, specific errors."""
    not_a_pdf = work_dir / "plain.txt"
    not_a_pdf.write_text("just text")
    with pytest.raises(NotAPdfError):
        Document.open(not_a_pdf)
    with pytest.raises(RepairFailedError):
        Document.open(corpus.broken_severely)
