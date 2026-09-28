"""Text drawn inside Form XObjects: extraction, classification and editing.

Found testing a real, templated bank statement: its header, address block,
dates and page numbers were all drawn inside a Form XObject, so every edit to
that text crashed (the font lookup only searched the page's own resources)
and no span on any page got a text state (the content-stream walker never
followed ``Do``, so its glyph count never matched texttrace's).
"""

from __future__ import annotations

import io
import shutil
from collections.abc import Iterator
from itertools import pairwise
from pathlib import Path

import pikepdf
import pymupdf
import pytest

from engine.document import Document
from engine.edit import resolve_font_for_span
from engine.errors import FontResourceNotFoundError, PdfWorkerzError
from engine.fonts.classify import classify_font, classify_font_xref
from engine.fonts.match import FontCandidate, build_font_index
from engine.fonts.style import SpanTrace, extract_page_spans, walk_raw_glyph_codes
from engine.ops.text import DeleteTextOp, ReplaceTextOp, RestyleTextOp
from engine.verify import pixel_diff, render_to_array
from server.app import _status_for
from tests.corpus.build_corpus import Corpus


@pytest.fixture(scope="module")
def font_index() -> list[FontCandidate]:
    return build_font_index(include_system=False)


@pytest.fixture
def form_doc(corpus: Corpus, work_dir: Path) -> Iterator[Document]:
    source = work_dir / "form_xobject.pdf"
    shutil.copy(corpus.form_xobject, source)
    doc = Document.open(source)
    yield doc
    doc.close()


HEADER_BASELINE_Y = 792 - 600  # the fixture's Tm y, in PyMuPDF's top-down coordinates


def _span(doc: Document, text: str) -> SpanTrace:
    return next(s for s in extract_page_spans(doc.raw, 0) if s.style.text == text)


def _header_line(doc: Document) -> list[SpanTrace]:
    """The spans on the header's baseline. The header has Tc 2, so an edit
    redraws it through the per-character path (engine.edit.draw_styled_text),
    which texttrace reports as one span per glyph -- reassemble by position."""
    spans = [s for s in extract_page_spans(doc.raw, 0) if s.style.chars]
    on_line = [s for s in spans if abs(s.style.chars[0].origin[1] - HEADER_BASELINE_Y) < 1]
    return sorted(on_line, key=lambda s: s.style.chars[0].origin[0])


@pytest.mark.feature("FNT-02")
def test_every_span_gets_a_text_state_including_form_text(form_doc: Document) -> None:
    spans = extract_page_spans(form_doc.raw, 0)
    assert [s.style.text for s in spans] == ["Page text before", "Header in form", "Page text after"]
    assert all(s.text_state is not None for s in spans)


@pytest.mark.feature("FNT-02")
def test_text_state_set_inside_a_form_does_not_leak_out(form_doc: Document) -> None:
    spans = {s.style.text: s for s in extract_page_spans(form_doc.raw, 0)}
    assert spans["Header in form"].text_state is not None
    assert spans["Header in form"].text_state.char_spacing == 2.0
    assert spans["Header in form"].text_state.font_size == 14.0
    assert spans["Page text after"].text_state is not None
    assert spans["Page text after"].text_state.char_spacing == 0.0
    assert spans["Page text after"].text_state.font_size == 12.0


@pytest.mark.feature("FNT-02")
def test_raw_glyph_codes_include_form_glyphs(form_doc: Document) -> None:
    with pikepdf.open(io.BytesIO(form_doc.to_bytes())) as pdf:
        codes = walk_raw_glyph_codes(pdf.pages[0])
    total_chars = sum(len(s.style.chars) for s in extract_page_spans(form_doc.raw, 0))
    assert len(codes) == total_chars
    assert bytes(codes[len("Page text before") :][: len("Header in form")]) == b"Header in form"


@pytest.mark.feature("FNT-02")
def test_a_self_referencing_form_does_not_recurse_forever(work_dir: Path) -> None:
    pdf = pikepdf.new()
    page = pdf.add_blank_page()
    form = pdf.make_stream(b"/Fm0 Do", Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Form, BBox=[0, 0, 10, 10])
    form.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Fm0=form))
    page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Fm0=form))
    page.Contents = pdf.make_stream(b"q /Fm0 Do Q")
    path = work_dir / "cycle.pdf"
    pdf.save(path)

    with pikepdf.open(path) as reopened:
        assert walk_raw_glyph_codes(reopened.pages[0]) == []


@pytest.mark.feature("FNT-03")
def test_classify_by_xref_resolves_a_colliding_resource_name(form_doc: Document) -> None:
    """Page and form both call their font /F1; only the xref tells them apart."""
    form_font_xref = next(
        xref for xref, _e, _t, basefont, *_r in form_doc.raw[0].get_fonts(full=True) if basefont == "Courier"
    )
    with pikepdf.open(io.BytesIO(form_doc.to_bytes())) as pdf:
        assert classify_font(pdf, 0, "F1").base_font == "Helvetica"
        assert classify_font_xref(pdf, form_font_xref, "F1").base_font == "Courier"


@pytest.mark.feature("EDT-01")
def test_resolve_font_for_form_text_uses_the_forms_own_font(
    form_doc: Document, font_index: list[FontCandidate]
) -> None:
    span = _span(form_doc, "Header in form")
    resolution = resolve_font_for_span(form_doc, 0, span, "New header", font_index=font_index)
    assert resolution.tier == "exact"
    assert "cour" in (resolution.fontname or "").lower()


@pytest.mark.feature("EDT-02")
def test_replace_text_inside_a_form(form_doc: Document) -> None:
    results = ReplaceTextOp(match="Header in form", replacement="Edited header", require_tier="exact").apply(form_doc)
    assert len(results) == 1
    assert results[0].verification is not None and results[0].verification.text_matches
    assert "".join(s.style.text for s in _header_line(form_doc)) == "Edited header"
    texts = [s.style.text for s in extract_page_spans(form_doc.raw, 0)]
    assert "Page text before" in texts and "Page text after" in texts


@pytest.mark.feature("EDT-02")
def test_replacing_form_text_keeps_its_character_spacing(form_doc: Document) -> None:
    """The form draws with Tc 2. Before Form XObjects were walked, that was
    invisible to the engine and edits silently fell back to Tc 0."""
    ReplaceTextOp(match="Header in form", replacement="ABCD").apply(form_doc)
    xs = [s.style.chars[0].origin[0] for s in _header_line(form_doc)]
    advances = [b - a for a, b in pairwise(xs)]
    assert advances == pytest.approx([14 * 0.6 + 2.0] * 3)  # Courier: 600/1000 em, plus Tc 2


@pytest.mark.feature("EDT-04")
def test_delete_text_inside_a_form(form_doc: Document) -> None:
    DeleteTextOp(match="Header in form").apply(form_doc)
    texts = [s.style.text for s in extract_page_spans(form_doc.raw, 0)]
    assert "Header in form" not in texts
    assert texts == ["Page text before", "Page text after"]


@pytest.mark.feature("EDT-06")
def test_restyle_text_inside_a_form(form_doc: Document) -> None:
    RestyleTextOp(match="Header in form", color=(1.0, 0.0, 0.0)).apply(form_doc)
    line = _header_line(form_doc)
    assert "".join(s.style.text for s in line) == "Header in form"
    assert all(s.style.color == pytest.approx((1.0, 0.0, 0.0)) for s in line)


@pytest.mark.feature("EDT-02")
def test_editing_a_shared_form_on_one_page_leaves_the_other_page_unchanged(corpus: Corpus, work_dir: Path) -> None:
    source = work_dir / "shared.pdf"
    shutil.copy(corpus.shared_form_xobject, source)
    with Document.open(source) as doc:
        page1_before = render_to_array(doc.raw, 1)
        ReplaceTextOp(match="Shared header", replacement="Page 0 only", page_index=0).apply(doc)
        assert doc.raw[0].get_text().strip() == "Page 0 only"
        assert doc.raw[1].get_text().strip() == "Shared header"
        assert pixel_diff(page1_before, render_to_array(doc.raw, 1)).matches


@pytest.mark.feature("EDT-01")
def test_an_unlocatable_font_raises_a_clean_engine_error(form_doc: Document, font_index: list[FontCandidate]) -> None:
    span = _span(form_doc, "Header in form")
    orphan = span.model_copy(update={"style": span.style.model_copy(update={"font": "NoSuchFontAnywhere"})})
    with pytest.raises(FontResourceNotFoundError) as excinfo:
        resolve_font_for_span(form_doc, 0, orphan, "x", font_index=font_index)
    assert isinstance(excinfo.value, PdfWorkerzError)
    assert _status_for(excinfo.value) == 422


@pytest.mark.feature("INF-06")
def test_form_fixtures_draw_their_text_through_form_xobjects(corpus: Corpus) -> None:
    with pymupdf.open(corpus.form_xobject) as doc:
        assert "Header in form" in doc[0].get_text()
    with pikepdf.open(corpus.shared_form_xobject) as pdf:
        first, second = (p.Resources.XObject.Fm0.objgen for p in pdf.pages)
        assert first == second
