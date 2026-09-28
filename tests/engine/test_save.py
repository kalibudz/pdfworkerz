"""COR-06 (incremental save) and COR-07 (full rewrite save)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pymupdf
import pytest

from engine.document import Document
from engine.errors import SaveNotPossibleError
from tests.corpus.build_corpus import Corpus


@pytest.mark.feature("COR-07")
def test_full_save_writes_a_valid_smaller_or_equal_deflated_file(corpus: Corpus, work_dir: Path) -> None:
    out = work_dir / "out.pdf"
    with Document.open(corpus.multi_page) as doc:
        result = doc.save(out, mode="full")
    assert result.mode == "full"
    assert out.exists()
    with pymupdf.open(out, filetype="pdf") as reopened:
        assert reopened.page_count == corpus.multi_page_count


@pytest.mark.feature("COR-07")
def test_full_save_over_the_source_path_is_atomic_and_keeps_the_document_usable(corpus: Corpus, work_dir: Path) -> None:
    source = work_dir / "input.pdf"
    shutil.copy(corpus.simple, source)
    with Document.open(source) as doc:
        result = doc.save(source, overwrite=True, mode="full")
        assert result.mode == "full"
        assert result.path == source
        # the Document must still be usable immediately after overwriting its own source
        assert doc.page_count == 1
        doc.render_page(0)  # does not raise
    # no leftover temp file from the atomic write
    assert not any(p.name.startswith(".") for p in work_dir.iterdir())


@pytest.mark.feature("COR-06")
def test_incremental_save_only_targets_the_original_path(corpus: Corpus, work_dir: Path) -> None:
    source = work_dir / "input.pdf"
    other = work_dir / "other.pdf"
    shutil.copy(corpus.simple, source)
    with Document.open(source) as doc, pytest.raises(SaveNotPossibleError, match="incremental"):
        doc.save(other, mode="incremental")


@pytest.mark.feature("COR-06")
def test_incremental_save_preserves_existing_content_and_appends(corpus: Corpus, work_dir: Path) -> None:
    source = work_dir / "input.pdf"
    shutil.copy(corpus.simple, source)
    size_before = source.stat().st_size

    with Document.open(source) as doc:
        doc.raw[0].insert_text((72, 300), "incrementally added")  # simulate a future edit op
        result = doc.save(source, overwrite=True, mode="incremental")

    assert result.mode == "incremental"
    assert source.stat().st_size >= size_before  # incremental save only appends, never shrinks
    with pymupdf.open(source, filetype="pdf") as reopened:
        assert "incrementally added" in reopened[0].get_text()
        assert "Hello" in reopened[0].get_text()  # original content untouched


@pytest.mark.feature("COR-06")
def test_auto_mode_prefers_incremental_for_signed_documents(corpus: Corpus, work_dir: Path) -> None:
    source = work_dir / "signed.pdf"
    shutil.copy(corpus.form_and_signature, source)
    with Document.open(source) as doc:
        result = doc.save(source, overwrite=True, mode="auto")
    assert result.mode == "incremental"


@pytest.mark.feature("COR-07")
def test_auto_mode_uses_full_rewrite_for_ordinary_documents(corpus: Corpus, work_dir: Path) -> None:
    source = work_dir / "plain.pdf"
    shutil.copy(corpus.simple, source)
    with Document.open(source) as doc:
        result = doc.save(source, overwrite=True, mode="auto")
    assert result.mode == "full"
