"""FNT-01 (per-span style extraction) and FNT-02 (per-character text-state trace)."""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path

import pikepdf
import pymupdf
import pytest

from engine.fonts.style import RENDER_MODE_NAMES, TextState, extract_page_spans
from tests.corpus.build_corpus import Corpus


@pytest.mark.feature("FNT-01")
def test_extract_reports_every_span_with_its_basic_style(corpus: Corpus) -> None:
    with pymupdf.open(corpus.bold_italic_standard) as doc:
        spans = extract_page_spans(doc, 0)
    assert [s.style.text for s in spans] == ["Regular text", "Bold text", "Italic text"]
    assert spans[0].style.font == "Helvetica"
    assert spans[1].style.font == "Helvetica-Bold"
    assert spans[2].style.font == "Helvetica-Oblique"


@pytest.mark.feature("FNT-01")
def test_extract_reports_size_color_and_bbox(corpus: Corpus) -> None:
    with pymupdf.open(corpus.simple) as doc:
        spans = extract_page_spans(doc, 0)
    assert len(spans) == 1
    style = spans[0].style
    assert style.size == pytest.approx(14.0)
    assert style.color == pytest.approx((0.0, 0.0, 0.0))
    x0, y0, x1, y1 = style.bbox
    assert x0 < x1 and y0 < y1


@pytest.mark.feature("FNT-01")
def test_extract_reports_per_char_positions_in_reading_order(corpus: Corpus) -> None:
    with pymupdf.open(corpus.simple) as doc:
        spans = extract_page_spans(doc, 0)
    chars = spans[0].style.chars
    assert "".join(c.char for c in chars) == "Hello, PDFWorkerz."
    # each character starts at or after the previous one on this left-to-right span
    for earlier, later in pairwise(chars):
        assert later.origin[0] >= earlier.origin[0]


@pytest.mark.feature("FNT-01")
def test_extract_reports_zero_rotation_for_horizontal_text(corpus: Corpus) -> None:
    with pymupdf.open(corpus.simple) as doc:
        spans = extract_page_spans(doc, 0)
    assert spans[0].style.rotation_degrees == pytest.approx(0.0, abs=1e-6)


@pytest.mark.feature("FNT-02")
def test_extract_correlates_content_stream_text_state(corpus: Corpus) -> None:
    with pymupdf.open(corpus.simple) as doc:
        spans = extract_page_spans(doc, 0)
    state = spans[0].text_state
    assert state is not None
    assert state.char_spacing == 0.0
    assert state.word_spacing == 0.0
    assert state.horizontal_scale == 100.0
    assert state.render_mode == 0
    assert state.render_mode_name == "fill"
    assert state.font_size == pytest.approx(14.0)


@pytest.mark.feature("FNT-02")
def test_extract_reads_explicit_tc_tw_tz_ts_tr(work_dir: Path) -> None:
    path = work_dir / "custom_state.pdf"
    pdf = pikepdf.new()
    page = pdf.add_blank_page()
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.Font,
            Subtype=pikepdf.Name.Type1,
            BaseFont=pikepdf.Name("/Helvetica"),
            Encoding=pikepdf.Name("/WinAnsiEncoding"),
        )
    )
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
    page.Contents = pdf.make_stream(
        b"q\nBT\n2 Tc\n1.5 Tw\n120 Tz\n3 Ts\n2 Tr\n/F1 18 Tf\n1 0 0 1 72 700 Tm\n(Custom State Text) Tj\nET\nQ\n"
    )
    pdf.save(path)
    pdf.close()

    with pymupdf.open(path, filetype="pdf") as doc:
        spans = extract_page_spans(doc, 0)

    assert len(spans) == 1
    state = spans[0].text_state
    assert state is not None
    assert state.char_spacing == pytest.approx(2.0)
    assert state.word_spacing == pytest.approx(1.5)
    assert state.horizontal_scale == pytest.approx(120.0)
    assert state.rise == pytest.approx(3.0)
    assert state.render_mode == 2
    assert state.render_mode_name == "fill_stroke"
    assert state.font_resource == "F1"
    assert state.font_size == pytest.approx(18.0)
    # texttrace bakes Tz into the rendered size (18 * 120% = 21.6); the declared Tf
    # size must stay separately available and correct even so.
    assert spans[0].style.size == pytest.approx(21.6)


@pytest.mark.feature("FNT-02")
def test_fill_and_stroke_render_mode_does_not_duplicate_spans(work_dir: Path) -> None:
    """Regression: Tr 2 makes texttrace emit one fill entry and one stroke entry per
    glyph run with identical geometry; extraction must collapse that to one span."""
    path = work_dir / "fill_stroke.pdf"
    pdf = pikepdf.new()
    page = pdf.add_blank_page()
    font = pdf.make_indirect(
        pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name("/Helvetica"))
    )
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
    page.Contents = pdf.make_stream(b"BT\n2 Tr\n/F1 18 Tf\n1 0 0 1 72 700 Tm\n(Outlined) Tj\nET\n")
    pdf.save(path)
    pdf.close()

    with pymupdf.open(path, filetype="pdf") as doc:
        spans = extract_page_spans(doc, 0)

    assert len(spans) == 1
    assert spans[0].style.text == "Outlined"


@pytest.mark.feature("FNT-02")
def test_render_mode_names_cover_every_defined_mode() -> None:
    assert set(RENDER_MODE_NAMES) == set(range(8))


@pytest.mark.feature("FNT-02")
def test_text_state_defaults_match_the_pdf_spec_defaults() -> None:
    state = TextState()
    assert state.char_spacing == 0.0
    assert state.word_spacing == 0.0
    assert state.horizontal_scale == 100.0
    assert state.rise == 0.0
    assert state.render_mode == 0


@pytest.mark.feature("FNT-02")
def test_q_and_qq_restore_text_state_after_a_mid_run_change(work_dir: Path) -> None:
    """A Tc change between two adjacent same-style Tj calls doesn't split them into
    separate texttrace spans -- MuPDF merges "before"+"inside" into one span, since
    Tc doesn't affect what's rendered. Q correctly restores Tc for what follows."""
    path = work_dir / "qq_state.pdf"
    pdf = pikepdf.new()
    page = pdf.add_blank_page()
    font = pdf.make_indirect(
        pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name("/Helvetica"))
    )
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
    page.Contents = pdf.make_stream(
        b"BT\n/F1 12 Tf\n1 0 0 1 72 700 Tm\n(before) Tj\nq\n5 Tc\n(inside) Tj\nQ\n(after) Tj\nET\n"
    )
    pdf.save(path)
    pdf.close()

    with pymupdf.open(path, filetype="pdf") as doc:
        spans = extract_page_spans(doc, 0)

    states = {s.style.text: s.text_state for s in spans}
    assert set(states) == {"beforeinside", "after"}
    # the merged span is attributed the state of its first glyph (Tc was 0 there)
    assert states["beforeinside"] is not None and states["beforeinside"].char_spacing == 0.0
    # Q correctly restored Tc to 0 for what follows, proving the state stack works
    assert states["after"] is not None and states["after"].char_spacing == 0.0


@pytest.mark.feature("FNT-02")
def test_tc_changes_are_correctly_tracked_across_separate_spans(work_dir: Path) -> None:
    """A font-size change forces a new texttrace span, giving a clean per-span check
    that Tc set between two runs is picked up, and that Q restores it afterward."""
    path = work_dir / "tc_spans.pdf"
    pdf = pikepdf.new()
    page = pdf.add_blank_page()
    font = pdf.make_indirect(
        pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name("/Helvetica"))
    )
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
    page.Contents = pdf.make_stream(
        b"BT\n/F1 12 Tf\n1 0 0 1 72 700 Tm\n(normal) Tj\n"
        b"q\n5 Tc\n/F1 14 Tf\n(spaced) Tj\nQ\n"
        b"/F1 12 Tf\n(normal again) Tj\nET\n"
    )
    pdf.save(path)
    pdf.close()

    with pymupdf.open(path, filetype="pdf") as doc:
        spans = extract_page_spans(doc, 0)

    states = {s.style.text: s.text_state for s in spans}
    assert states["normal"] is not None and states["normal"].char_spacing == 0.0
    assert states["spaced"] is not None and states["spaced"].char_spacing == 5.0
    assert states["normal again"] is not None and states["normal again"].char_spacing == 0.0
