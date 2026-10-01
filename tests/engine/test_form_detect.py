"""FRM-04: auto-detect fields on flat (non-interactive) forms.

Every fixture here is built directly with pymupdf drawing primitives --
`page.draw_line` for a line/underscore cue, `page.draw_rect` for a checkbox
cue, `page.insert_text` for labels and running prose -- since detection needs
only the visual cues a real authoring tool would leave on an otherwise plain
page, not a real AcroForm structure (see engine/form_detect.py's own
docstring for the algorithm and the false-positive guards this corpus is
tuned against).
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from engine.document import Document
from engine.form_detect import FieldProposal, detect_form_fields
from engine.ops.base import parse_op
from engine.ops.journal import UndoRedoJournal


def _apply(doc: Document, data: dict[str, object]) -> object:
    op = parse_op(data)
    op.check_pages(doc)
    return op.apply(doc)


def _rotated_name_line_doc(tmp_path: Path, rotation: int, name: str = "rotated.pdf") -> Path:
    """A single text-field cue (line + "Name:" label), on a page carrying
    /Rotate=`rotation`. The line/label are drawn in the page's own,
    unrotated content-stream coordinates -- exactly how a real authoring tool
    would build a page before a viewer or scanner stamped a rotation flag on
    it -- so this exercises the page.rotation_matrix reconciliation between
    the rendered (rotated) pixmap and get_texttrace's (unrotated) span boxes."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((50, 95), "Name:", fontsize=10)
    page.draw_line((100, 100), (300, 100), width=1)
    page.set_rotation(rotation)
    path = tmp_path / name
    doc.save(path)
    doc.close()
    return path


def _flat_form_doc(tmp_path: Path, name: str = "flat_form.pdf") -> Path:
    """One page with two text-field cues (line + label) and one checkbox cue
    (small box + label), nothing interactive."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 95), "Name:", fontsize=10)
    page.draw_line((100, 100), (300, 100), width=1)
    page.insert_text((70, 152), "Subscribe", fontsize=10)
    page.draw_rect(pymupdf.Rect(50, 140, 62, 152), width=1)
    page.insert_text((50, 195), "Email:", fontsize=10)
    page.draw_line((110, 200), (350, 200), width=1)
    path = tmp_path / name
    doc.save(path)
    doc.close()
    return path


def _plain_text_doc(tmp_path: Path, name: str = "plain.pdf") -> Path:
    """An ordinary paragraph of prose, no form cues at all."""
    doc = pymupdf.open()
    page = doc.new_page()
    paragraph = "This is an ordinary paragraph of text with no form cues whatsoever. " * 8
    page.insert_textbox(pymupdf.Rect(50, 50, 545, 400), paragraph, fontsize=11)
    path = tmp_path / name
    doc.save(path)
    doc.close()
    return path


def _table_doc(tmp_path: Path, name: str = "table.pdf", rows: int = 6, cols: int = 4) -> Path:
    """A ruled table (lots of rectangles/lines, the classic false-positive bait)."""
    doc = pymupdf.open()
    page = doc.new_page()
    x0, y0 = 50, 100
    cell_w, cell_h = 100, 25
    for row in range(rows + 1):
        y = y0 + row * cell_h
        page.draw_line((x0, y), (x0 + cols * cell_w, y), width=1)
    for col in range(cols + 1):
        x = x0 + col * cell_w
        page.draw_line((x, y0), (x, y0 + rows * cell_h), width=1)
    for row in range(rows):
        for col in range(cols):
            page.insert_text((x0 + col * cell_w + 5, y0 + row * cell_h + 15), f"R{row}C{col}", fontsize=8)
    path = tmp_path / name
    doc.save(path)
    doc.close()
    return path


def _empty_doc(tmp_path: Path, name: str = "empty.pdf") -> Path:
    doc = pymupdf.open()
    doc.new_page()
    path = tmp_path / name
    doc.save(path)
    doc.close()
    return path


# -- criterion 1: lines/underscores and boxes near a label are detected --


@pytest.mark.feature("FRM-04")
def test_line_and_box_cues_near_labels_are_detected(tmp_path: Path) -> None:
    path = _flat_form_doc(tmp_path)
    with Document.open(path) as doc:
        proposals = detect_form_fields(doc, 0)

    text_proposals = [p for p in proposals if p.field_type == "text"]
    checkbox_proposals = [p for p in proposals if p.field_type == "checkbox"]
    assert len(text_proposals) == 2, [p.model_dump() for p in proposals]
    assert len(checkbox_proposals) == 1, [p.model_dump() for p in proposals]
    assert {p.label_text for p in text_proposals} == {"Name:", "Email:"}
    assert checkbox_proposals[0].label_text == "Subscribe"


@pytest.mark.feature("FRM-04")
def test_op_wraps_detection_read_only(tmp_path: Path) -> None:
    path = _flat_form_doc(tmp_path)
    with Document.open(path) as doc:
        proposals = _apply(doc, {"op": "detect_form_fields", "page_index": 0})
    assert len(proposals) == 3  # type: ignore[arg-type]


# -- criterion 2: each proposal carries a position and a best-guess name, for review --


@pytest.mark.feature("FRM-04", criterion=2)
def test_each_proposal_has_rect_and_best_guess_name_from_the_label(tmp_path: Path) -> None:
    path = _flat_form_doc(tmp_path)
    with Document.open(path) as doc:
        proposals = detect_form_fields(doc, 0)

    by_label = {p.label_text: p for p in proposals}
    name_field = by_label["Name:"]
    assert name_field.suggested_name == "name"
    assert len(name_field.rect) == 4
    assert all(isinstance(v, float) for v in name_field.rect)
    assert 0.0 < name_field.confidence <= 1.0

    email_field = by_label["Email:"]
    assert email_field.suggested_name == "email"

    subscribe_field = by_label["Subscribe"]
    assert subscribe_field.suggested_name == "subscribe"
    assert subscribe_field.field_type == "checkbox"


@pytest.mark.feature("FRM-04", criterion=2)
def test_duplicate_labels_get_unique_suggested_names(tmp_path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 95), "Date:", fontsize=10)
    page.draw_line((100, 100), (250, 100), width=1)
    page.insert_text((50, 145), "Date:", fontsize=10)
    page.draw_line((100, 150), (250, 150), width=1)
    path = tmp_path / "dup_labels.pdf"
    doc.save(path)
    doc.close()

    with Document.open(path) as pdf_doc:
        proposals = detect_form_fields(pdf_doc, 0)
    names = sorted(p.suggested_name for p in proposals)
    assert names == ["date", "date_2"]


@pytest.mark.feature("FRM-04", criterion=2)
def test_no_label_found_gets_a_generic_fallback_name(tmp_path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.draw_line((300, 400), (500, 400), width=1)  # no nearby label at all
    path = tmp_path / "unlabeled.pdf"
    doc.save(path)
    doc.close()

    with Document.open(path) as pdf_doc:
        proposals = detect_form_fields(pdf_doc, 0)
    assert len(proposals) == 1
    assert proposals[0].label_text is None
    assert proposals[0].suggested_name == "text_field"
    assert proposals[0].confidence < 0.9  # lower confidence with no label to back it up


# -- criterion 3: accepting creates real fields via FRM-03 in one undo step; nothing
#    is created by detection alone --


@pytest.mark.feature("FRM-04", criterion=3)
def test_detection_alone_creates_nothing(tmp_path: Path) -> None:
    path = _flat_form_doc(tmp_path)
    with Document.open(path) as doc:
        detect_form_fields(doc, 0)
        fields = _apply(doc, {"op": "page_fields", "page_index": 0})
    assert fields == []  # type: ignore[comparison-overlap]


@pytest.mark.feature("FRM-04", criterion=3)
def test_accepting_proposals_creates_fields_in_one_undo_step(tmp_path: Path) -> None:
    path = _flat_form_doc(tmp_path)
    journal = UndoRedoJournal(Document.open(path))
    proposals = detect_form_fields(journal.document, 0)

    created = journal.record(
        parse_op({"op": "create_detected_fields", "page_index": 0, "proposals": [p.model_dump() for p in proposals]})
    )
    assert len(created) == 3  # type: ignore[arg-type]

    fields = _apply(journal.document, {"op": "page_fields", "page_index": 0})
    assert len(fields) == 3  # type: ignore[arg-type]
    assert journal.can_undo

    journal.undo()
    fields_after_undo = _apply(journal.document, {"op": "page_fields", "page_index": 0})
    assert fields_after_undo == []  # type: ignore[comparison-overlap]
    journal.document.close()


@pytest.mark.feature("FRM-04", criterion=3)
def test_accepting_a_subset_only_creates_the_accepted_ones(tmp_path: Path) -> None:
    path = _flat_form_doc(tmp_path)
    journal = UndoRedoJournal(Document.open(path))
    proposals = detect_form_fields(journal.document, 0)
    accepted = [p for p in proposals if p.field_type == "checkbox"]

    journal.record(
        parse_op({"op": "create_detected_fields", "page_index": 0, "proposals": [p.model_dump() for p in accepted]})
    )
    fields = _apply(journal.document, {"op": "page_fields", "page_index": 0})
    assert len(fields) == 1  # type: ignore[arg-type]
    assert fields[0].field_type == "checkbox"  # type: ignore[union-attr]
    journal.document.close()


# -- criterion 4: a page with no visual form cues yields no false proposals --


@pytest.mark.feature("FRM-04", criterion=4)
def test_plain_paragraph_text_yields_no_proposals(tmp_path: Path) -> None:
    path = _plain_text_doc(tmp_path)
    with Document.open(path) as doc:
        proposals = detect_form_fields(doc, 0)
    assert proposals == []


@pytest.mark.feature("FRM-04", criterion=4)
def test_ruled_table_yields_no_false_proposals(tmp_path: Path) -> None:
    path = _table_doc(tmp_path)
    with Document.open(path) as doc:
        proposals = detect_form_fields(doc, 0)
    assert proposals == []


@pytest.mark.feature("FRM-04", criterion=4)
def test_empty_page_yields_no_proposals(tmp_path: Path) -> None:
    path = _empty_doc(tmp_path)
    with Document.open(path) as doc:
        proposals = detect_form_fields(doc, 0)
    assert proposals == []


@pytest.mark.feature("FRM-04", criterion=4)
def test_rotated_90_and_270_pages_yield_no_phantom_proposals(tmp_path: Path) -> None:
    """Regression for the coordinate-space mismatch between the rotated pixmap
    render and get_texttrace's unrotated span boxes: before the fix, a 90-
    degree-rotated page with one real line+label cue produced several phantom
    checkbox proposals near the label's own (mismatched-coordinate) glyphs.
    A content-stream-horizontal line becomes genuinely vertical once rendered
    at 90/270 degrees (confirmed: Hough finds 0 near-horizontal segments in
    that rendered frame), so no text-field proposal is expected either -- the
    fixed behavior at these two rotations is simply "nothing detected",
    not "phantom detections"."""
    for rotation in (90, 270):
        path = _rotated_name_line_doc(tmp_path, rotation, name=f"rotated_{rotation}.pdf")
        with Document.open(path) as doc:
            proposals = detect_form_fields(doc, 0)
        assert proposals == [], f"rotation={rotation}: expected no proposals, got {proposals}"


@pytest.mark.feature("FRM-04", criterion=4)
def test_rotated_0_and_180_pages_still_detect_the_real_line_with_no_phantoms(tmp_path: Path) -> None:
    """A page rotated 0 or 180 degrees keeps a content-stream-horizontal line
    horizontal in the rendered/display frame (a point reflection preserves
    horizontal orientation), so the line cue is still found at both, and --
    the actual regression this guards against -- no extra (phantom) proposals
    appear alongside it. 180 degrees also swaps left/right in the rotated
    frame, which this module's "label is to the left of a line" heuristic
    does not account for (a documented limitation, not a crash or a phantom
    proposal): the proposal itself is still correctly found, just without a
    label stuck onto the wrong side."""
    for rotation in (0, 180):
        path = _rotated_name_line_doc(tmp_path, rotation, name=f"rotated_{rotation}.pdf")
        with Document.open(path) as doc:
            proposals = detect_form_fields(doc, 0)
        assert len(proposals) == 1, f"rotation={rotation}: expected exactly one proposal, got {proposals}"
        proposal = proposals[0]
        assert proposal.field_type == "text"
        # The rect must land within the page's own (rotated) frame, never bleeding
        # outside it the way an unreconciled coordinate system could produce.
        page_rect = pymupdf.open(path)[0].rect
        x0, y0, x1, y1 = proposal.rect
        assert 0.0 <= x0 < x1 <= page_rect.width
        assert 0.0 <= y0 < y1 <= page_rect.height
    # At 0 degrees the label-to-the-left heuristic applies normally.
    path0 = _rotated_name_line_doc(tmp_path, 0, name="rotated_0_check.pdf")
    with Document.open(path0) as doc:
        proposals0 = detect_form_fields(doc, 0)
    assert proposals0[0].label_text == "Name:"
    assert proposals0[0].confidence == 0.9


@pytest.mark.feature("FRM-04", criterion=4)
def test_a_page_rule_spanning_most_of_the_width_is_not_a_field_line(tmp_path: Path) -> None:
    """A full-width horizontal rule (a section divider, or a table/page frame's own
    border) is not one field's own line -- it is excluded by its sheer length."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.draw_line((40, 400), (555, 400), width=1)  # ~515pt on a 595pt-wide page
    path = tmp_path / "full_width_rule.pdf"
    doc.save(path)
    doc.close()

    with Document.open(path) as pdf_doc:
        proposals = detect_form_fields(pdf_doc, 0)
    assert proposals == []


# -- FieldProposal model shape --


@pytest.mark.feature("FRM-04", criterion=2)
def test_field_proposal_model_round_trips_through_json() -> None:
    proposal = FieldProposal(
        field_type="text",
        rect=(10.0, 20.0, 110.0, 34.0),
        confidence=0.9,
        suggested_name="full_name",
        label_text="Full Name:",
    )
    dumped = proposal.model_dump(mode="json")
    restored = FieldProposal.model_validate(dumped)
    assert restored == proposal
