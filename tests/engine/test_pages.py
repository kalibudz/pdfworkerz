"""P5 slice 1: page organization (ORG-01..08, ORG-13)."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from engine.document import Document
from engine.errors import OpValidationError, OverwriteRefusedError
from engine.ops.base import parse_op
from engine.ops.journal import UndoRedoJournal


def _numbered(path: Path, count: int, *, label: str = "Page") -> Path:
    doc = pymupdf.open()
    for n in range(1, count + 1):
        doc.new_page().insert_text((72, 72), f"{label} {n}", fontsize=14)
    doc.save(path)
    return path


def _labels(doc: Document) -> list[str]:
    return [doc.raw[i].get_text().strip() for i in range(doc.page_count)]


def _apply(doc: Document, data: dict[str, object]) -> object:
    op = parse_op(data)
    op.check_pages(doc)
    return op.apply(doc)


@pytest.fixture
def five(tmp_path: Path) -> Document:
    return Document.open(_numbered(tmp_path / "five.pdf", 5))


# -- ORG-01 merge --


@pytest.mark.feature("ORG-01")
def test_merge_appends_or_inserts_another_pdf(tmp_path: Path, five: Document) -> None:
    other = _numbered(tmp_path / "other.pdf", 2, label="Extra")
    assert _apply(five, {"op": "merge", "path": str(other)}) == 2
    assert _labels(five)[-2:] == ["Extra 1", "Extra 2"]
    _apply(five, {"op": "merge", "path": str(other), "at": 0})
    assert _labels(five)[:3] == ["Extra 1", "Extra 2", "Page 1"]


@pytest.mark.feature("ORG-01")
def test_merge_refuses_a_missing_file(five: Document) -> None:
    with pytest.raises(OpValidationError, match="does not exist"):
        _apply(five, {"op": "merge", "path": "no/such/file.pdf"})


# -- ORG-02 split --


@pytest.mark.feature("ORG-02")
def test_split_every_n_pages(tmp_path: Path, five: Document) -> None:
    out = tmp_path / "parts"
    out.mkdir()
    parts = _apply(five, {"op": "split", "out_dir": str(out), "every": 2})
    assert [p["pages"] for p in parts] == [[0, 1], [2, 3], [4]]  # type: ignore[index]
    with pymupdf.open(out / "five.part2.pdf") as part:
        assert [page.get_text().strip() for page in part] == ["Page 3", "Page 4"]
    assert five.page_count == 5  # the document itself is unchanged


@pytest.mark.feature("ORG-02")
def test_split_by_ranges_and_refuses_to_overwrite(tmp_path: Path, five: Document) -> None:
    op = {"op": "split", "out_dir": str(tmp_path), "ranges": [[0], [4, 3]]}
    parts = _apply(five, op)
    assert [p["pages"] for p in parts] == [[0], [4, 3]]  # type: ignore[index]
    with pymupdf.open(tmp_path / "five.part2.pdf") as part:
        assert [page.get_text().strip() for page in part] == ["Page 5", "Page 4"]
    with pytest.raises(OverwriteRefusedError):
        _apply(five, op)


@pytest.mark.feature("ORG-02")
def test_split_at_top_level_bookmarks(tmp_path: Path, five: Document) -> None:
    five.raw.set_toc([[1, "Intro", 1], [1, "Body", 3], [2, "Detail", 4]])
    parts = _apply(five, {"op": "split", "out_dir": str(tmp_path), "by_bookmarks": True})
    assert [p["pages"] for p in parts] == [[0, 1], [2, 3, 4]]  # type: ignore[index]


@pytest.mark.feature("ORG-02")
def test_split_by_maximum_size_keeps_every_part_under_it(tmp_path: Path, five: Document) -> None:
    from engine.pages import _bytes_for

    limit = _bytes_for(five, [0, 1]) + 10
    parts = _apply(five, {"op": "split", "out_dir": str(tmp_path), "max_bytes": limit})
    assert sum(len(p["pages"]) for p in parts) == 5  # type: ignore[index,misc]
    assert all((tmp_path / f"five.part{n}.pdf").stat().st_size <= limit for n in range(1, len(parts) + 1))  # type: ignore[arg-type]


@pytest.mark.feature("ORG-02")
def test_split_needs_exactly_one_way(tmp_path: Path, five: Document) -> None:
    with pytest.raises(OpValidationError, match="exactly one"):
        _apply(five, {"op": "split", "out_dir": str(tmp_path), "every": 2, "by_bookmarks": True})


# -- ORG-03 reorder / move --


@pytest.mark.feature("ORG-03")
def test_move_pages_after_another(five: Document) -> None:
    _apply(five, {"op": "move_pages", "page_indices": [4, 3], "to": 1})
    assert _labels(five) == ["Page 1", "Page 4", "Page 5", "Page 2", "Page 3"]


@pytest.mark.feature("ORG-03")
def test_reorder_needs_every_page_once(five: Document) -> None:
    _apply(five, {"op": "reorder_pages", "order": [4, 3, 2, 1, 0]})
    assert _labels(five)[0] == "Page 5"
    with pytest.raises(OpValidationError, match="every page exactly once"):
        _apply(five, {"op": "reorder_pages", "order": [0, 0, 1, 2, 3]})


# -- ORG-04 rotate --


@pytest.mark.feature("ORG-04")
def test_rotate_adds_to_the_existing_rotation(five: Document) -> None:
    _apply(five, {"op": "rotate_pages", "page_indices": [0, 2], "degrees": 90})
    _apply(five, {"op": "rotate_pages", "page_indices": [0], "degrees": 270})
    assert [five.raw[i].rotation for i in range(3)] == [0, 0, 90]
    with pytest.raises(OpValidationError, match="multiples of 90"):
        _apply(five, {"op": "rotate_pages", "page_indices": [0], "degrees": 45})


# -- ORG-05 delete --


@pytest.mark.feature("ORG-05")
def test_delete_pages_and_never_all(five: Document) -> None:
    _apply(five, {"op": "delete_pages", "page_indices": [1, 3]})
    assert _labels(five) == ["Page 1", "Page 3", "Page 5"]
    with pytest.raises(OpValidationError, match="at least one"):
        _apply(five, {"op": "delete_pages", "page_indices": [0, 1, 2]})


@pytest.mark.feature("ORG-05")
def test_an_out_of_range_page_is_refused_before_anything_changes(five: Document) -> None:
    with pytest.raises(OpValidationError, match="out of range"):
        _apply(five, {"op": "delete_pages", "page_indices": [1, 9]})
    assert five.page_count == 5


# -- ORG-06 extract --


@pytest.mark.feature("ORG-06")
def test_extract_pages_to_a_new_file(tmp_path: Path, five: Document) -> None:
    out = tmp_path / "picked.pdf"
    _apply(five, {"op": "extract_pages", "page_indices": [4, 0], "out": str(out)})
    with pymupdf.open(out) as picked:
        assert [page.get_text().strip() for page in picked] == ["Page 5", "Page 1"]
    assert five.page_count == 5
    with pytest.raises(OverwriteRefusedError):
        _apply(five, {"op": "extract_pages", "page_indices": [0], "out": str(out)})


# -- ORG-07 insert --


@pytest.mark.feature("ORG-07")
def test_insert_blank_pages_of_a_given_size(five: Document) -> None:
    _apply(five, {"op": "insert_pages", "at": 1, "count": 2, "size": "letter"})
    assert five.page_count == 7 and _labels(five)[1:3] == ["", ""]
    assert (five.raw[1].rect.width, five.raw[1].rect.height) == (612, 792)


@pytest.mark.feature("ORG-07")
def test_insert_pages_from_another_file(tmp_path: Path, five: Document) -> None:
    other = _numbered(tmp_path / "other.pdf", 3, label="Other")
    _apply(five, {"op": "insert_pages", "at": 5, "from_path": str(other), "from_pages": [2, 0]})
    assert _labels(five)[5:] == ["Other 3", "Other 1"]


# -- ORG-08 duplicate --


@pytest.mark.feature("ORG-08")
def test_duplicate_pages_right_after_each(five: Document) -> None:
    _apply(five, {"op": "duplicate_pages", "page_indices": [0, 2], "copies": 2})
    assert _labels(five)[:7] == ["Page 1", "Page 1", "Page 1", "Page 2", "Page 3", "Page 3", "Page 3"]
    five.raw[1].insert_text((72, 200), "only on the copy")
    assert "only on the copy" not in five.raw[0].get_text()  # independent copies, not links


# -- ORG-13 blank pages --


@pytest.mark.feature("ORG-13")
def test_blank_pages_are_found_and_removed(tmp_path: Path) -> None:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Content")
    doc.new_page()  # blank
    speck = doc.new_page()
    speck.draw_rect(pymupdf.Rect(300, 400, 301, 401), color=(0.9, 0.9, 0.9))  # scanner-dust speck
    drawing = doc.new_page()
    drawing.draw_rect(pymupdf.Rect(100, 100, 400, 400), color=(0, 0, 0), fill=(0, 0, 0))  # not blank: no text but ink
    doc.save(tmp_path / "gaps.pdf")
    document = Document.open(tmp_path / "gaps.pdf")
    assert _apply(document, {"op": "find_blank_pages"}) == [1, 2]
    assert document.page_count == 4
    assert _apply(document, {"op": "remove_blank_pages"}) == [1, 2]
    assert document.page_count == 2


# -- journaling --


@pytest.mark.feature("ORG-05")
def test_page_operations_undo_like_any_edit(five: Document) -> None:
    journal = UndoRedoJournal(five)
    journal.record(parse_op({"op": "delete_pages", "page_indices": [0]}))
    journal.record(parse_op({"op": "rotate_pages", "page_indices": [0], "degrees": 180}))
    assert journal.document.page_count == 4 and journal.document.raw[0].rotation == 180
    journal.undo()
    journal.undo()
    assert _labels(journal.document)[0] == "Page 1" and journal.document.raw[0].rotation == 0
    journal.document.close()
