"""SEC-10: sanitize -- remove metadata, XMP, JavaScript, embedded files and hidden text."""

from __future__ import annotations

from pathlib import Path

import pikepdf
import pymupdf
import pytest

from engine.document import Document
from engine.fonts.style import extract_page_spans
from engine.ops.redact import SanitizeOp
from engine.sanitize import sanitize_document
from engine.structure import attach_file, list_attachments


@pytest.fixture
def doc(work_dir: Path) -> Document:
    """A document with metadata, XMP, an open-action JS, a field-level JS action, an
    embedded file, and both visible and invisible (render mode 3) text."""
    raw = pymupdf.open()
    page = raw.new_page()
    page.insert_text((50, 100), "Visible text", fontsize=12, render_mode=0)
    page.insert_text((50, 150), "Invisible OCR layer", fontsize=12, render_mode=3)
    raw.set_metadata({"title": "Secret Title", "author": "Jane Doe"})
    base_path = work_dir / "sanitize.base.pdf"
    raw.save(base_path)
    raw.close()

    path = work_dir / "sanitize.pdf"
    with pikepdf.open(base_path) as pdf:
        with pdf.open_metadata() as meta:
            meta["dc:title"] = "Secret Title"
        pdf.Root.OpenAction = pikepdf.Dictionary(S=pikepdf.Name("/JavaScript"), JS="app.alert('hi');")
        pdf.save(path)

    document = Document.open(path)
    attachment_path = work_dir / "secret.txt"
    attachment_path.write_text("hidden payload")
    attach_file(document, str(attachment_path), name="secret.txt")
    document.save(path, overwrite=True)
    return Document.open(path)


def _text(document: Document, page_index: int = 0) -> str:
    return "".join(span.style.text for span in extract_page_spans(document.raw, page_index))


@pytest.mark.feature("SEC-10", criterion=1)
def test_sanitize_removes_metadata_and_xmp(doc: Document) -> None:
    assert doc.raw.metadata.get("title") == "Secret Title"
    assert doc.raw.xref_xml_metadata() != 0
    report = sanitize_document(doc)
    assert report.metadata_removed
    assert not doc.raw.metadata.get("title")
    assert doc.raw.xref_xml_metadata() == 0


@pytest.mark.feature("SEC-10", criterion=2)
def test_sanitize_removes_javascript_actions(doc: Document) -> None:
    catalog = doc.raw.pdf_catalog()
    kind, value = doc.raw.xref_get_key(catalog, "OpenAction")
    assert kind == "dict" and "JavaScript" in value
    report = sanitize_document(doc)
    assert report.javascript_actions_removed >= 1
    kind, value = doc.raw.xref_get_key(catalog, "OpenAction")
    assert kind == "null"


@pytest.mark.feature("SEC-10", criterion=3)
def test_sanitize_removes_embedded_files(doc: Document) -> None:
    assert len(list_attachments(doc)) == 1
    report = sanitize_document(doc)
    assert report.embedded_files_removed == 1
    assert list_attachments(doc) == []


@pytest.mark.feature("SEC-10", criterion=4)
def test_sanitize_removes_hidden_text_but_keeps_visible_text(doc: Document) -> None:
    assert "Invisible OCR layer" in _text(doc)
    assert "Visible text" in _text(doc)
    report = sanitize_document(doc)
    assert report.hidden_text_chars_removed == len("Invisible OCR layer")
    text = _text(doc)
    assert "Invisible OCR layer" not in text
    assert "Visible text" in text


@pytest.mark.feature("SEC-10", criterion=5)
def test_each_category_can_be_opted_out_independently(doc: Document) -> None:
    report = sanitize_document(
        doc,
        remove_metadata=False,
        remove_javascript=False,
        remove_embedded_files=False,
        remove_hidden_text=True,
    )
    assert not report.metadata_removed
    assert report.javascript_actions_removed == 0
    assert report.embedded_files_removed == 0
    assert report.hidden_text_chars_removed > 0
    assert doc.raw.metadata.get("title") == "Secret Title"
    assert len(list_attachments(doc)) == 1
    catalog = doc.raw.pdf_catalog()
    kind, _value = doc.raw.xref_get_key(catalog, "OpenAction")
    assert kind == "dict"


@pytest.mark.feature("SEC-10", criterion=5)
def test_sanitize_op_default_booleans_remove_everything(doc: Document) -> None:
    report = SanitizeOp().apply(doc)
    assert report.metadata_removed
    assert report.javascript_actions_removed >= 1
    assert report.embedded_files_removed == 1
    assert report.hidden_text_chars_removed > 0
