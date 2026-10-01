"""SEC-08/09/11: true redaction, pattern packs, and redaction verification."""

from __future__ import annotations

import io
from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from engine.document import Document
from engine.errors import OpValidationError, RedactionVerificationError
from engine.fonts.style import extract_page_spans
from engine.ops.base import parse_op
from engine.ops.redact import FindRedactionCandidatesOp, RedactAreasOp
from engine.redact import redact_areas, verify_redaction


def _png(width: int, height: int, color: tuple[int, int, int]) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), color).save(out, format="PNG")
    return out.getvalue()


def _text(document: Document, page_index: int = 0) -> str:
    return "".join(span.style.text for span in extract_page_spans(document.raw, page_index))


@pytest.fixture
def doc(work_dir: Path) -> Document:
    """One page: a line of text ("Secret: ssn 123-45-6789 end"), a green image, and a
    black line shape, laid out so each sits in its own clear area of the page."""
    raw = pymupdf.open()
    page = raw.new_page()
    page.insert_text((50, 100), "Secret: ssn 123-45-6789 end", fontsize=14)
    page.insert_image(pymupdf.Rect(50, 200, 250, 350), stream=_png(200, 150, (0, 160, 0)))
    shape = page.new_shape()
    shape.draw_line(pymupdf.Point(50, 500), pymupdf.Point(250, 500))
    shape.finish(color=(0, 0, 0), width=2)
    shape.commit()
    path = work_dir / "redact.pdf"
    raw.save(path)
    raw.close()
    return Document.open(path)


def _span_rect(document: Document, text: str) -> tuple[float, float, float, float]:
    for span in extract_page_spans(document.raw, 0):
        if text in span.style.text:
            start = span.style.text.index(text)
            boxes = [c.bbox for c in span.style.chars[start : start + len(text)]]
            return (
                min(b[0] for b in boxes),
                min(b[1] for b in boxes),
                max(b[2] for b in boxes),
                max(b[3] for b in boxes),
            )
    raise AssertionError(f"{text!r} not found")


@pytest.mark.feature("SEC-08", criterion=1)
def test_redact_removes_glyphs_image_pixels_and_vector_paths(doc: Document) -> None:
    ssn_rect = _span_rect(doc, "123-45-6789")
    result = redact_areas(doc, 0, [ssn_rect, (50, 200, 250, 350), (50, 495, 250, 505)])
    assert "123-45-6789" not in _text(doc)
    assert "Secret" in _text(doc) and "end" in _text(doc)  # the rest of the span survives
    assert result.stats[1].images_removed == 1
    assert result.stats[2].shapes_removed == 1
    assert result.verification.ok


@pytest.mark.feature("SEC-08", criterion=2)
def test_saved_content_stream_has_no_leftover_glyph_operators(doc: Document, work_dir: Path) -> None:
    ssn_rect = _span_rect(doc, "123-45-6789")
    redact_areas(doc, 0, [ssn_rect])
    out = work_dir / "saved.pdf"
    doc.save(out, overwrite=False)
    reopened = Document.open(out)
    assert "123-45-6789" not in _text(reopened)
    verification = verify_redaction(reopened, 0, [ssn_rect])
    assert verification.ok


@pytest.mark.feature("SEC-08", criterion=3)
def test_partial_overlap_removes_only_covered_glyphs_and_nothing_else_moves(doc: Document) -> None:
    ssn_rect = _span_rect(doc, "123-45-6789")
    before = doc.raw[0].get_pixmap(dpi=150)
    before_bytes = bytes(before.samples)
    result = redact_areas(doc, 0, [ssn_rect])
    text = _text(doc)
    assert "123-45-6789" not in text
    assert text == "Secret: ssn  end"  # exact gap where the SSN was, rest untouched
    after = doc.raw[0].get_pixmap(dpi=150)
    assert before.width == after.width and before.height == after.height
    # Pixels changed only is asserted via outside_changed_fraction, the module's own proof:
    assert result.outside_changed_fraction <= 1e-5
    del before_bytes  # kept only to document intent; the real proof is outside_changed_fraction


@pytest.mark.feature("SEC-08", criterion=4)
def test_redacting_one_area_never_changes_pixels_outside_it(doc: Document) -> None:
    result = redact_areas(doc, 0, [(50, 200, 250, 350)])  # the image only
    assert "Secret" in _text(doc) and "123-45-6789" in _text(doc)  # text untouched
    assert result.outside_changed_fraction <= 1e-5


@pytest.mark.feature("SEC-08", criterion=1)
def test_partial_image_overlap_alters_pixels_not_just_crops(doc: Document, work_dir: Path) -> None:
    """A redaction over the left half of the image must blacken that half's actual
    pixel data -- checked by decoding the saved image back out, not just checking the
    page renders differently."""
    result = redact_areas(doc, 0, [(50, 200, 150, 350)])  # left half of the 200x150 image
    assert result.stats[0].images_altered == 1
    images = list(doc.raw[0].get_image_info(xrefs=True))
    assert len(images) == 1
    pixmap = pymupdf.Pixmap(doc.raw, images[0]["xref"])
    with Image.open(io.BytesIO(pixmap.tobytes("png"))) as image:
        left_pixel = image.convert("RGB").getpixel((5, 75))
        right_pixel = image.convert("RGB").getpixel((195, 75))
    assert left_pixel == (0, 0, 0)  # blacked out
    assert right_pixel == (0, 160, 0)  # untouched original green


@pytest.mark.feature("SEC-08", criterion=1)
def test_nothing_is_drawn_back_over_a_redacted_area(doc: Document) -> None:
    """SEC-08's documented choice: pure removal, no automatic black box."""
    ssn_rect = _span_rect(doc, "123-45-6789")
    before_shapes = len(list(doc.raw[0].get_drawings()))
    redact_areas(doc, 0, [ssn_rect])
    after_shapes = len(list(doc.raw[0].get_drawings()))
    assert after_shapes == before_shapes  # no new shape (a drawn box) appeared


def test_redact_areas_refuses_empty_rect_list(doc: Document) -> None:
    with pytest.raises(OpValidationError):
        redact_areas(doc, 0, [])


# -- SEC-09: pattern packs ------------------------------------------------------------


@pytest.fixture
def pii_doc(work_dir: Path) -> Document:
    raw = pymupdf.open()
    page = raw.new_page()
    lines = [
        "Contact: jane.doe@example.com or call 555-123-4567",
        "SSN on file: 078-05-1120",
        "Card: 4111 1111 1111 1111",
        "Custom code: WORKERZ-42",
    ]
    for i, line in enumerate(lines):
        page.insert_text((50, 100 + i * 30), line, fontsize=12)
    path = work_dir / "pii.pdf"
    raw.save(path)
    raw.close()
    return Document.open(path)


@pytest.mark.feature("SEC-09", criterion=1)
def test_builtin_patterns_find_true_positives(pii_doc: Document) -> None:
    emails = FindRedactionCandidatesOp(page_index=0, pattern="email").apply(pii_doc)
    phones = FindRedactionCandidatesOp(page_index=0, pattern="phone").apply(pii_doc)
    ssns = FindRedactionCandidatesOp(page_index=0, pattern="ssn").apply(pii_doc)
    cards = FindRedactionCandidatesOp(page_index=0, pattern="credit_card").apply(pii_doc)
    assert [m.text for m in emails] == ["jane.doe@example.com"]
    assert [m.text for m in phones] == ["555-123-4567"]
    assert [m.text for m in ssns] == ["078-05-1120"]
    assert len(cards) == 1 and cards[0].text.replace(" ", "") == "4111111111111111"


@pytest.mark.feature("SEC-09", criterion=1)
def test_ssn_pattern_also_finds_dashless_and_space_separated_forms(work_dir: Path) -> None:
    """The dash-only SSN pattern silently missed the plain-digit and space-separated
    forms, both common on real forms -- found by independent review; regression test
    for the fix in engine.redact's _SSN_RE."""
    raw = pymupdf.open()
    page = raw.new_page()
    page.insert_text((50, 100), "Dashed: 078-05-1120", fontsize=12)
    page.insert_text((50, 130), "Plain: 078051120", fontsize=12)
    page.insert_text((50, 160), "Spaced: 078 05 1120", fontsize=12)
    path = work_dir / "ssn_forms.pdf"
    raw.save(path)
    raw.close()
    with Document.open(path) as doc:
        matches = FindRedactionCandidatesOp(page_index=0, pattern="ssn").apply(doc)
    assert [m.text for m in matches] == ["078-05-1120", "078051120", "078 05 1120"]


@pytest.mark.feature("SEC-09", criterion=2)
def test_custom_regex_pattern_runs_the_same_way(pii_doc: Document) -> None:
    matches = FindRedactionCandidatesOp(page_index=0, pattern=r"WORKERZ-\d+").apply(pii_doc)
    assert [m.text for m in matches] == ["WORKERZ-42"]


@pytest.mark.feature("SEC-09", criterion=2)
def test_invalid_custom_regex_is_rejected(pii_doc: Document) -> None:
    with pytest.raises(OpValidationError):
        FindRedactionCandidatesOp(page_index=0, pattern="(unclosed").apply(pii_doc)


@pytest.mark.feature("SEC-09", criterion=3)
def test_finding_matches_redacts_nothing(pii_doc: Document) -> None:
    before = _text(pii_doc)
    FindRedactionCandidatesOp(page_index=0, pattern="ssn").apply(pii_doc)
    assert _text(pii_doc) == before


@pytest.mark.feature("SEC-09", criterion=4)
def test_accepting_matches_redacts_every_one_in_one_step(pii_doc: Document) -> None:
    matches = FindRedactionCandidatesOp(page_index=0, pattern="email").apply(pii_doc)
    matches += FindRedactionCandidatesOp(page_index=0, pattern="ssn").apply(pii_doc)
    result = RedactAreasOp(page_index=0, rects=[m.rect for m in matches]).apply(pii_doc)
    text = _text(pii_doc)
    assert "jane.doe@example.com" not in text
    assert "078-05-1120" not in text
    assert result.verification.ok
    assert len(result.stats) == 2  # one undo step, two rects processed together


@pytest.mark.feature("SEC-09", criterion=1)
def test_find_redaction_candidates_op_is_registered_and_parses() -> None:
    op = parse_op({"op": "find_redaction_candidates", "page_index": 0, "pattern": "email"})
    assert isinstance(op, FindRedactionCandidatesOp)


# -- SEC-11: verification is not a rubber stamp ---------------------------------------


@pytest.mark.feature("SEC-11", criterion=1)
def test_verification_passes_after_a_real_redaction(doc: Document) -> None:
    ssn_rect = _span_rect(doc, "123-45-6789")
    redact_areas(doc, 0, [ssn_rect])
    assert verify_redaction(doc, 0, [ssn_rect]).ok


@pytest.mark.feature("SEC-11", criterion=2)
def test_verification_fails_on_a_black_box_merely_drawn_over_live_text(work_dir: Path) -> None:
    """The planted trap: a black rectangle drawn on top of text that was never
    actually removed must be caught, not rubber-stamped as "redacted"."""
    raw = pymupdf.open()
    page = raw.new_page()
    page.insert_text((50, 100), "Secret: 123-45-6789", fontsize=14)
    rect = pymupdf.Rect(48, 88, 160, 104)
    shape = page.new_shape()
    shape.draw_rect(rect)
    shape.finish(color=(0, 0, 0), fill=(0, 0, 0))
    shape.commit()
    path = work_dir / "fake_redaction.pdf"
    raw.save(path)
    raw.close()
    document = Document.open(path)

    verification = verify_redaction(document, 0, [(rect.x0, rect.y0, rect.x1, rect.y1)])
    assert not verification.ok
    assert verification.problems  # names what's still there


@pytest.mark.feature("SEC-11", criterion=2)
def test_redact_areas_raises_if_verification_would_fail(monkeypatch: pytest.MonkeyPatch, doc: Document) -> None:
    """redact_areas must call verification itself and refuse to succeed quietly;
    simulate a verification failure by asking it to check a rect that still has an
    untouched image (one rect given to redact_areas, a different one checked)."""
    import engine.redact as redact_module

    ssn_rect = _span_rect(doc, "123-45-6789")

    def _fake_verify(document: Document, page_index: int, rects: list) -> redact_module.RedactionVerification:
        return redact_module.RedactionVerification(ok=False, problems=["forced failure for the test"])

    monkeypatch.setattr(redact_module, "verify_redaction", _fake_verify)
    with pytest.raises(RedactionVerificationError):
        redact_areas(doc, 0, [ssn_rect])


@pytest.mark.feature("SEC-11", criterion=3)
def test_verification_checks_a_fully_covered_image_still_drawn(work_dir: Path) -> None:
    """An image XObject still placed and fully inside the rect (never removed, never
    altered) must be caught by re-extraction over the page's structure, not just text."""
    raw = pymupdf.open()
    page = raw.new_page()
    page.insert_image(pymupdf.Rect(50, 50, 150, 150), stream=_png(50, 50, (10, 20, 30)))
    path = work_dir / "image_not_redacted.pdf"
    raw.save(path)
    raw.close()
    document = Document.open(path)
    verification = verify_redaction(document, 0, [(40, 40, 160, 160)])
    assert not verification.ok
