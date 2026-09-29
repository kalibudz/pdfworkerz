"""P5 slice 5: document structure (DOC-01..05, COR-12)."""

from __future__ import annotations

import io
import shutil
from pathlib import Path

import pikepdf
import pymupdf
import pytest

from engine.document import Document
from engine.errors import OpValidationError, OverwriteRefusedError
from engine.ops.base import parse_op
from engine.ops.journal import UndoRedoJournal
from tests.corpus.build_corpus import Corpus


def _apply(doc: Document, data: dict[str, object]) -> object:
    op = parse_op(data)
    op.check_pages(doc)
    return op.apply(doc)


def _doc(tmp_path: Path, pages: int = 5) -> Document:
    doc = pymupdf.open()
    for n in range(1, pages + 1):
        doc.new_page().insert_text((72, 100), f"Body text of page {n}.", fontsize=11)
    doc.save(tmp_path / "s.pdf")
    return Document.open(tmp_path / "s.pdf")


def _saved(doc: Document, tmp_path: Path, name: str = "out.pdf") -> Path:
    out = tmp_path / name
    doc.save(out)
    return out


# -- DOC-01 metadata --


@pytest.mark.feature("DOC-01")
def test_metadata_info_and_xmp_stay_in_step(tmp_path: Path) -> None:
    doc = _doc(tmp_path)
    result = _apply(
        doc,
        {
            "op": "set_metadata",
            "fields": {"title": "Annual Report", "author": "Kim Lee; Sam Roe", "keywords": "finance, 2026"},
            "xmp": {"dc:rights": "All rights reserved"},
        },
    )
    assert result.title == "Annual Report"  # type: ignore[attr-defined]
    out = _saved(doc, tmp_path)
    with pymupdf.open(out) as saved:
        assert saved.metadata["title"] == "Annual Report" and saved.metadata["keywords"] == "finance, 2026"
    with pikepdf.open(out) as pdf:
        meta = pdf.open_metadata()
        assert meta["dc:title"] == "Annual Report"
        assert list(meta["dc:creator"]) == ["Kim Lee", "Sam Roe"]
        assert meta["pdf:Keywords"] == "finance, 2026"
        assert meta["dc:rights"] == "All rights reserved"
    read = _apply(doc, {"op": "get_metadata"})
    assert read.xmp["dc:title"] == "Annual Report"  # type: ignore[attr-defined]


@pytest.mark.feature("DOC-01")
def test_metadata_clears_fields_and_refuses_unknown_ones(tmp_path: Path) -> None:
    doc = _doc(tmp_path)
    _apply(doc, {"op": "set_metadata", "fields": {"title": "Temp", "subject": "S"}})
    _apply(doc, {"op": "set_metadata", "fields": {"title": ""}})
    meta = _apply(doc, {"op": "get_metadata"})
    assert meta.title == "" and meta.subject == "S" and "dc:title" not in meta.xmp  # type: ignore[attr-defined]
    with pytest.raises(OpValidationError, match="unknown metadata field"):
        _apply(doc, {"op": "set_metadata", "fields": {"colour": "red"}})
    with pytest.raises(OpValidationError, match="need a prefix"):
        _apply(doc, {"op": "set_metadata", "xmp": {"rights": "x"}})


@pytest.mark.feature("DOC-01")
def test_metadata_on_an_encrypted_document_keeps_encryption(corpus: Corpus, tmp_path: Path) -> None:
    source = tmp_path / "enc.pdf"
    shutil.copy(corpus.encrypted_aes_256, source)
    journal = UndoRedoJournal(Document.open(source, password=corpus.user_password))
    journal.record(parse_op({"op": "set_metadata", "fields": {"title": "Secret plan"}}))
    journal.document.save(tmp_path / "out.pdf")
    with pymupdf.open(tmp_path / "out.pdf") as saved:
        assert saved.needs_pass and saved.authenticate(corpus.user_password)
        assert saved.metadata["title"] == "Secret plan"
    with pikepdf.open(tmp_path / "out.pdf", password=corpus.user_password) as pdf:
        assert pdf.open_metadata()["dc:title"] == "Secret plan"
    journal.undo()
    assert journal.document.raw.metadata.get("title") != "Secret plan"
    journal.document.close()


# -- DOC-02 bookmarks --


def _titles(doc: Document) -> list[tuple[int, str, int]]:
    return [(b.level, b.title, b.page_index) for b in _apply(doc, {"op": "list_bookmarks"})]  # type: ignore[attr-defined]


@pytest.mark.feature("DOC-02")
def test_bookmark_outline_editing(tmp_path: Path) -> None:
    doc = _doc(tmp_path)
    _apply(doc, {"op": "set_bookmarks", "entries": [(1, "Intro", 0), (1, "Body", 1), (2, "Detail", 2), (1, "End", 4)]})
    _apply(doc, {"op": "add_bookmark", "title": "Appendix", "page_index": 3, "level": 2, "at": 3})
    assert _titles(doc) == [(1, "Intro", 0), (1, "Body", 1), (2, "Detail", 2), (2, "Appendix", 3), (1, "End", 4)]
    _apply(doc, {"op": "update_bookmark", "index": 1, "title": "Main body", "target_page_index": 2})
    _apply(doc, {"op": "move_bookmark", "index": 1, "to": 2})  # "Main body" and its children after "End"
    assert _titles(doc) == [(1, "Intro", 0), (1, "End", 4), (1, "Main body", 2), (2, "Detail", 2), (2, "Appendix", 3)]
    _apply(doc, {"op": "delete_bookmark", "index": 2})  # takes its children too
    assert _titles(doc) == [(1, "Intro", 0), (1, "End", 4)]
    with pymupdf.open(_saved(doc, tmp_path)) as saved:
        assert saved.get_toc() == [[1, "Intro", 1], [1, "End", 5]]


@pytest.mark.feature("DOC-02")
def test_bookmark_level_change_moves_children_and_bad_outlines_are_refused(tmp_path: Path) -> None:
    doc = _doc(tmp_path)
    _apply(doc, {"op": "set_bookmarks", "entries": [(1, "A", 0), (1, "B", 1), (2, "B.1", 1), (3, "B.1.a", 2)]})
    _apply(doc, {"op": "update_bookmark", "index": 1, "level": 2})
    assert [lvl for lvl, _t, _p in _titles(doc)] == [1, 2, 3, 4]
    with pytest.raises(OpValidationError, match="at most one level deeper"):
        _apply(doc, {"op": "set_bookmarks", "entries": [(2, "orphan", 0)]})
    with pytest.raises(OpValidationError, match="doesn't exist"):
        _apply(doc, {"op": "set_bookmarks", "entries": [(1, "far", 40)]})
    with pytest.raises(OpValidationError, match="no bookmark 9"):
        _apply(doc, {"op": "delete_bookmark", "index": 9})


# -- DOC-03 headings --


def _report(tmp_path: Path) -> Document:
    doc = pymupdf.open()
    content = [
        [("Annual Report", 24), ("Overview", 16), ("Body text " * 8, 11), ("More body text here.", 11)],
        [("Results", 16), ("Revenue", 13), ("Body text " * 8, 11)],
        [("Costs", 13), ("Body text " * 8, 11), ("Outlook", 16), ("Body text " * 6, 11)],
    ]
    for lines in content:
        page = doc.new_page()
        y = 80.0
        for text, size in lines:
            page.insert_text((72, y), text, fontsize=size, fontname="hebo" if size > 11 else "helv")
            y += size * 2
    doc.save(tmp_path / "report.pdf")
    return Document.open(tmp_path / "report.pdf")


@pytest.mark.feature("DOC-03")
def test_bookmarks_generated_from_heading_sizes(tmp_path: Path) -> None:
    doc = _report(tmp_path)
    _apply(doc, {"op": "auto_bookmarks"})
    assert _titles(doc) == [
        (1, "Annual Report", 0),
        (2, "Overview", 0),
        (2, "Results", 1),
        (3, "Revenue", 1),
        (3, "Costs", 2),
        (2, "Outlook", 2),
    ]
    _apply(doc, {"op": "auto_bookmarks", "max_levels": 1})
    assert _titles(doc) == [(1, "Annual Report", 0)]


@pytest.mark.feature("DOC-03")
def test_contents_page_lists_and_links_the_outline(tmp_path: Path) -> None:
    doc = _report(tmp_path)
    _apply(doc, {"op": "auto_bookmarks", "max_levels": 2})
    assert _apply(doc, {"op": "contents_page"}) == 4
    contents = doc.raw[0]
    text = contents.get_text()
    assert text.startswith("Contents") and "Outlook" in text
    links = contents.get_links()
    assert [link["page"] for link in links] == [1, 1, 2, 3]  # every entry shifted past the new page
    assert "Results" in doc.raw[links[2]["page"]].get_text()
    assert _titles(doc)[2] == (2, "Results", 2)  # the outline still points at the right pages


@pytest.mark.feature("DOC-03")
def test_no_headings_is_refused(tmp_path: Path) -> None:
    with pytest.raises(OpValidationError, match="no headings"):
        _apply(_doc(tmp_path), {"op": "auto_bookmarks"})


# -- DOC-04 attachments --


@pytest.mark.feature("DOC-04")
def test_attach_extract_and_remove_files(tmp_path: Path) -> None:
    doc = _doc(tmp_path)
    data = tmp_path / "data.csv"
    data.write_bytes(b"a,b\n1,2\n")
    attached = _apply(doc, {"op": "attach_file", "path": str(data), "description": "source numbers"})
    assert attached.name == "data.csv" and attached.size == 8  # type: ignore[attr-defined]
    with pytest.raises(OpValidationError, match="already an attachment"):
        _apply(doc, {"op": "attach_file", "path": str(data)})
    with pymupdf.open(_saved(doc, tmp_path)) as saved:
        assert saved.embfile_get("data.csv") == data.read_bytes()
        assert saved.embfile_info("data.csv")["description"] == "source numbers"
    out = tmp_path / "back.csv"
    assert _apply(doc, {"op": "extract_attachment", "name": "data.csv", "out": str(out)}) == 8
    assert out.read_bytes() == data.read_bytes()
    with pytest.raises(OverwriteRefusedError):
        _apply(doc, {"op": "extract_attachment", "name": "data.csv", "out": str(out)})
    _apply(doc, {"op": "remove_attachment", "name": "data.csv"})
    assert _apply(doc, {"op": "list_attachments"}) == []
    with pytest.raises(OpValidationError, match="no attachment"):
        _apply(doc, {"op": "remove_attachment", "name": "data.csv"})


# -- DOC-05 page labels --


@pytest.mark.feature("DOC-05")
def test_page_labels_roman_then_decimal_then_prefixed(tmp_path: Path) -> None:
    doc = _doc(tmp_path, 6)
    labels = _apply(
        doc,
        {
            "op": "set_page_labels",
            "ranges": [
                {"start_page": 0, "style": "roman"},
                {"start_page": 2, "style": "decimal"},
                {"start_page": 4, "style": "decimal", "prefix": "A-"},
            ],
        },
    )
    assert labels == ["i", "ii", "1", "2", "A-1", "A-2"]
    with pymupdf.open(_saved(doc, tmp_path)) as saved:
        assert [page.get_label() for page in saved] == labels
    assert _apply(doc, {"op": "set_page_labels", "ranges": []}) == ["1", "2", "3", "4", "5", "6"]
    with pytest.raises(OpValidationError, match="must start at the first page"):
        _apply(doc, {"op": "set_page_labels", "ranges": [{"start_page": 2}]})


# -- COR-12 layers --


@pytest.mark.feature("COR-12")
def test_layers_are_listed_and_toggled(tmp_path: Path) -> None:
    source = pymupdf.open()
    page = source.new_page()
    notes = source.add_ocg("Reviewer notes", on=True)
    grid = source.add_ocg("Grid", on=False)
    page.insert_text((72, 100), "NOTE LAYER TEXT", fontsize=20, oc=notes)
    page.insert_text((72, 200), "GRID LAYER TEXT", fontsize=20, oc=grid)
    source.save(tmp_path / "layers.pdf")
    doc = Document.open(tmp_path / "layers.pdf")
    layers = {layer.name: layer for layer in _apply(doc, {"op": "list_layers"})}  # type: ignore[attr-defined]
    assert (layers["Reviewer notes"].visible, layers["Grid"].visible) == (True, False)
    toggled = _apply(
        doc,
        {"op": "set_layer_visibility", "visibility": {layers["Reviewer notes"].xref: False, layers["Grid"].xref: True}},
    )
    assert {layer.name: layer.visible for layer in toggled} == {"Reviewer notes": False, "Grid": True}  # type: ignore[attr-defined]
    with pymupdf.open(_saved(doc, tmp_path)) as saved:
        ink = saved[0].get_pixmap(dpi=36, clip=pymupdf.Rect(60, 70, 400, 110)).samples
        assert min(ink) > 200  # the notes layer is hidden in the saved file
        ink = saved[0].get_pixmap(dpi=36, clip=pymupdf.Rect(60, 170, 400, 210)).samples
        assert min(ink) < 100  # the grid is shown
    with pytest.raises(OpValidationError, match="no layer"):
        _apply(doc, {"op": "set_layer_visibility", "visibility": {99999: True}})


@pytest.mark.feature("COR-12")
def test_layer_changes_undo(tmp_path: Path) -> None:
    source = pymupdf.open()
    xref = source.add_ocg("Only", on=True)
    source.new_page().insert_text((72, 100), "x", oc=xref)
    source.save(tmp_path / "one.pdf")
    journal = UndoRedoJournal(Document.open(tmp_path / "one.pdf"))
    journal.record(parse_op({"op": "set_layer_visibility", "visibility": {xref: False}}))
    journal.undo()
    assert [layer.visible for layer in _apply(journal.document, {"op": "list_layers"})] == [True]  # type: ignore[attr-defined]
    journal.document.close()


def test_xmp_reader_handles_documents_without_xmp(tmp_path: Path) -> None:
    doc = _doc(tmp_path)
    assert _apply(doc, {"op": "get_metadata"}).xmp == {}  # type: ignore[attr-defined]
    with pikepdf.open(io.BytesIO(doc.raw.tobytes())) as pdf:
        assert "/Metadata" not in pdf.Root


@pytest.mark.feature("DOC-01")
def test_xmp_survives_saving_as_twice(tmp_path: Path) -> None:
    """Regression: a garbage-collecting Save As used to compact the live document and leave its
    /Metadata pointing at a stale object number, so the second save silently lost the XMP."""
    doc = _doc(tmp_path)
    _apply(doc, {"op": "set_metadata", "fields": {"title": "Kept"}})
    _saved(doc, tmp_path, "first.pdf")
    doc.raw[0].insert_text((72, 300), "edited after the first save")
    second = _saved(doc, tmp_path, "second.pdf")
    with pikepdf.open(second) as pdf:
        assert pdf.open_metadata()["dc:title"] == "Kept"
    assert _apply(doc, {"op": "get_metadata"}).xmp["dc:title"] == "Kept"  # type: ignore[attr-defined]


# -- command bar --


@pytest.mark.feature("DOC-01")
@pytest.mark.feature("DOC-02")
@pytest.mark.feature("DOC-03")
@pytest.mark.feature("DOC-04")
def test_structure_commands_plan_and_run(tmp_path: Path) -> None:
    from engine.commands import parse_command

    def ops(text: str) -> list[dict[str, object]]:
        return parse_command(text, page_count=3).ops

    assert ops('set title to "Report"') == [{"op": "set_metadata", "fields": {"title": "Report"}}]
    assert ops('bookmark page 2 as "Results"') == [{"op": "add_bookmark", "title": "Results", "page_index": 1}]
    assert ops("bookmark headings") == [{"op": "auto_bookmarks"}]
    assert ops("insert contents page") == [{"op": "contents_page", "at": 0}]
    assert ops('remove attachment "a.csv"') == [{"op": "remove_attachment", "name": "a.csv"}]
    doc = _report(tmp_path)
    data = tmp_path / "a.csv"
    data.write_bytes(b"1,2\n")
    for command in ['set author "Kim"', "bookmark headings", "insert contents page", f'attach "{data.as_posix()}"']:
        for op in parse_command(command, page_count=doc.page_count).ops:
            _apply(doc, op)
    assert doc.raw.metadata["author"] == "Kim" and doc.page_count == 4
    assert doc.raw.embfile_names() == ["a.csv"]
