"""COR-03: the document inspection report."""

from __future__ import annotations

import pytest

from engine.document import Document
from tests.corpus.build_corpus import Corpus


@pytest.mark.feature("COR-03")
def test_inspect_reports_basic_facts(corpus: Corpus) -> None:
    with Document.open(corpus.multi_page) as doc:
        report = doc.inspect()
    assert report.page_count == corpus.multi_page_count
    assert report.pdf_version.startswith("PDF ")
    assert report.file_size_bytes > 0
    assert report.is_repaired is False


@pytest.mark.feature("COR-03")
def test_inspect_detects_fonts_used_on_the_page(corpus: Corpus) -> None:
    with Document.open(corpus.simple) as doc:
        report = doc.inspect()
    assert len(report.fonts) == 1
    assert report.fonts[0].basefont == "Helvetica"
    assert report.fonts[0].font_type == "Type1"


@pytest.mark.feature("COR-03")
def test_inspect_detects_forms_and_signatures(corpus: Corpus) -> None:
    with Document.open(corpus.form_and_signature) as doc:
        report = doc.inspect()
    assert report.has_forms is True
    assert report.has_signatures is True


@pytest.mark.feature("COR-03")
def test_inspect_reports_no_forms_or_signatures_on_a_plain_document(corpus: Corpus) -> None:
    with Document.open(corpus.simple) as doc:
        report = doc.inspect()
    assert report.has_forms is False
    assert report.has_signatures is False


@pytest.mark.feature("COR-03")
def test_inspect_detects_optional_content_layers(corpus: Corpus) -> None:
    with Document.open(corpus.layered) as doc:
        layered_report = doc.inspect()
    with Document.open(corpus.simple) as doc:
        plain_report = doc.inspect()
    assert layered_report.has_layers is True
    assert plain_report.has_layers is False


@pytest.mark.feature("COR-03")
def test_inspect_reports_repaired_flag_for_a_recovered_document(corpus: Corpus) -> None:
    with Document.open(corpus.broken_truncated) as doc:
        report = doc.inspect()
    assert report.is_repaired is True


@pytest.mark.feature("COR-03")
def test_inspect_includes_encryption_info(corpus: Corpus) -> None:
    with Document.open(corpus.encrypted_aes_256, password=corpus.user_password) as doc:
        report = doc.inspect(password=corpus.user_password)
    assert report.encryption.is_encrypted is True
    assert report.encryption.algorithm == "AES-256"


@pytest.mark.feature("COR-03")
def test_inspect_report_serializes_to_json(corpus: Corpus) -> None:
    with Document.open(corpus.simple) as doc:
        report = doc.inspect()
    payload = report.model_dump_json()
    assert '"page_count":1' in payload.replace(" ", "")
