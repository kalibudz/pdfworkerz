"""Proves the golden corpus generator itself works (INF-06)."""

from __future__ import annotations

from pathlib import Path

import pikepdf
import pymupdf
import pytest

from tests.corpus.build_corpus import build_corpus


@pytest.mark.feature("INF-06")
def test_build_corpus_creates_every_fixture(tmp_path: Path) -> None:
    corpus = build_corpus(tmp_path, force=True)
    for field in corpus.__dataclass_fields__:
        value = getattr(corpus, field)
        if isinstance(value, Path):
            assert value.exists(), field
            assert value.stat().st_size > 0, field


@pytest.mark.feature("INF-06")
def test_build_corpus_is_idempotent_without_force(tmp_path: Path) -> None:
    first = build_corpus(tmp_path)
    mtime = first.simple.stat().st_mtime_ns
    build_corpus(tmp_path)  # no force: should not touch existing files
    assert first.simple.stat().st_mtime_ns == mtime


@pytest.mark.feature("INF-06")
def test_multi_page_and_large_page_counts_match_ground_truth(tmp_path: Path) -> None:
    corpus = build_corpus(tmp_path)
    with pymupdf.open(corpus.multi_page) as doc:
        assert doc.page_count == corpus.multi_page_count == 5
    with pymupdf.open(corpus.large) as doc:
        assert doc.page_count == corpus.large_page_count == 1000


@pytest.mark.feature("INF-06")
def test_encrypted_fixtures_use_the_documented_password_and_revision(tmp_path: Path) -> None:
    corpus = build_corpus(tmp_path)
    cases = [
        (corpus.encrypted_rc4_40, 2, 40),
        (corpus.encrypted_rc4_128, 3, 128),
        (corpus.encrypted_aes_128, 4, 128),
        (corpus.encrypted_aes_256, 6, 256),
    ]
    for path, expected_r, expected_bits in cases:
        with pikepdf.open(path, password=corpus.user_password) as pdf:
            assert pdf.is_encrypted
            assert expected_r == pdf.encryption.R
            assert pdf.encryption.bits == expected_bits
        with pytest.raises(pikepdf.PasswordError):
            pikepdf.open(path, password="definitely-wrong")


@pytest.mark.feature("INF-06")
def test_owner_only_fixture_has_no_user_password_but_is_restricted(tmp_path: Path) -> None:
    corpus = build_corpus(tmp_path)
    with pikepdf.open(corpus.owner_only) as pdf:  # empty password, as any viewer would try first
        assert pdf.is_encrypted
        assert pdf.user_password_matched
        assert not pdf.allow.modify_other
        assert not pdf.allow.extract


@pytest.mark.feature("INF-06")
def test_broken_truncated_fixture_is_recoverable(tmp_path: Path) -> None:
    corpus = build_corpus(tmp_path)
    with pymupdf.open(corpus.broken_truncated, filetype="pdf") as doc:
        assert doc.is_repaired
        assert doc.page_count == corpus.multi_page_count


@pytest.mark.feature("INF-06")
def test_broken_severely_fixture_cannot_be_opened(tmp_path: Path) -> None:
    corpus = build_corpus(tmp_path)
    with pytest.raises(pymupdf.FileDataError):
        pymupdf.open(corpus.broken_severely, filetype="pdf")


@pytest.mark.feature("INF-06")
def test_certificate_stub_fixture_is_flagged_as_unsupported_filter(tmp_path: Path) -> None:
    corpus = build_corpus(tmp_path)
    with pytest.raises(pikepdf.PdfError, match="encryption filter"):
        pikepdf.open(corpus.certificate_stub)


@pytest.mark.feature("INF-06")
def test_form_and_signature_fixture_has_both_field_kinds(tmp_path: Path) -> None:
    corpus = build_corpus(tmp_path)
    with pymupdf.open(corpus.form_and_signature) as doc:
        assert doc.is_form_pdf
        types = {w.field_type_string for w in doc[0].widgets()}
        assert types == {"Text", "Signature"}


@pytest.mark.feature("INF-06")
def test_layered_fixture_has_an_optional_content_group(tmp_path: Path) -> None:
    corpus = build_corpus(tmp_path)
    with pymupdf.open(corpus.layered) as doc:
        assert doc.get_ocgs()


@pytest.mark.feature("INF-06")
def test_cjk_fixture_has_chinese_text_in_a_composite_font(tmp_path: Path) -> None:
    corpus = build_corpus(tmp_path)
    with pymupdf.open(corpus.cjk) as doc:
        assert doc[0].get_text().strip() == "你好世界"
        _xref, _ext, font_type, basefont, *_rest = doc[0].get_fonts(full=True)[0]
        assert font_type == "Type0"
        assert "Noto Sans CJK" in basefont


@pytest.mark.feature("INF-06")
def test_paragraph_fixture_has_a_three_line_block_and_a_separate_one_line_block(tmp_path: Path) -> None:
    corpus = build_corpus(tmp_path)
    with pymupdf.open(corpus.paragraph) as doc:
        assert doc.page_count == 1
        text = doc[0].get_text().strip().splitlines()
        assert len(text) == 4  # 3 lines of the paragraph + the separate one-line paragraph
        assert text[3] == "A separate paragraph starts here."
