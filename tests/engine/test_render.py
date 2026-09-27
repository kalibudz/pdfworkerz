"""COR-02: render pages to raster at any DPI."""

from __future__ import annotations

import pytest

from engine.document import Document
from tests.corpus.build_corpus import Corpus


@pytest.mark.feature("COR-02")
def test_render_page_produces_a_valid_png(corpus: Corpus) -> None:
    with Document.open(corpus.simple) as doc:
        png = doc.render_page(0)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")


@pytest.mark.feature("COR-02")
def test_render_page_scales_with_dpi(corpus: Corpus) -> None:
    with Document.open(corpus.simple) as doc:
        low = doc.render_page(0, dpi=72)
        high = doc.render_page(0, dpi=300)
    # A higher-DPI PNG of the same page must be a larger encoded image.
    assert len(high) > len(low)


@pytest.mark.feature("COR-02")
def test_render_page_is_deterministic(corpus: Corpus) -> None:
    with Document.open(corpus.multi_page) as doc:
        first = doc.render_page(2, dpi=100)
        second = doc.render_page(2, dpi=100)
    assert first == second


@pytest.mark.feature("COR-02")
def test_render_page_respects_page_index(corpus: Corpus) -> None:
    with Document.open(corpus.multi_page) as doc:
        page0 = doc.render_page(0)
        page1 = doc.render_page(1)
    assert page0 != page1  # different page text -> different pixels
