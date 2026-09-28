"""INF-07: the pixel-diff visual regression harness."""

from __future__ import annotations

import numpy as np
import pytest

from engine.document import Document
from engine.verify import assert_pixel_similar, pixel_diff, render_to_array
from tests.corpus.build_corpus import Corpus


@pytest.mark.feature("INF-07")
def test_render_to_array_has_expected_shape(corpus: Corpus) -> None:
    with Document.open(corpus.simple) as doc:
        array = render_to_array(doc.raw, 0, dpi=100)
    assert array.ndim == 3
    assert array.dtype == np.uint8
    assert array.shape[2] in (1, 3, 4)


@pytest.mark.feature("INF-07")
def test_identical_renders_have_zero_diff(corpus: Corpus) -> None:
    with Document.open(corpus.simple) as doc:
        first = render_to_array(doc.raw, 0, dpi=100)
        second = render_to_array(doc.raw, 0, dpi=100)
    result = pixel_diff(first, second)
    assert result.matches is True
    assert result.changed_fraction == 0.0
    assert result.changed_bbox is None


@pytest.mark.feature("INF-07")
def test_a_real_edit_is_detected_and_localized(corpus: Corpus) -> None:
    with Document.open(corpus.multi_page) as doc:
        before = render_to_array(doc.raw, 0, dpi=100)
        next(doc.iter_pages()).insert_text((72, 300), "a brand new line")
        after = render_to_array(doc.raw, 0, dpi=100)

    result = pixel_diff(before, after)
    assert result.changed_fraction > 0.0
    assert result.changed_bbox is not None
    x0, y0, x1, y1 = result.changed_bbox
    assert x0 < x1 and y0 < y1  # a real, non-empty box


@pytest.mark.feature("INF-07")
def test_diff_rejects_mismatched_shapes() -> None:
    a = np.zeros((10, 10, 3), dtype=np.uint8)
    b = np.zeros((20, 20, 3), dtype=np.uint8)
    with pytest.raises(ValueError, match="different shapes"):
        pixel_diff(a, b)


@pytest.mark.feature("INF-07")
def test_assert_pixel_similar_passes_within_tolerance() -> None:
    base = np.zeros((100, 100, 3), dtype=np.uint8)
    noisy = base.copy()
    noisy[0, 0] = [1, 1, 1]  # 1 pixel out of 10,000 = 0.01%
    assert_pixel_similar(base, noisy, tolerance=0.001)  # must not raise


@pytest.mark.feature("INF-07")
def test_assert_pixel_similar_fails_outside_tolerance() -> None:
    base = np.zeros((10, 10, 3), dtype=np.uint8)
    very_different = np.full((10, 10, 3), 255, dtype=np.uint8)
    with pytest.raises(AssertionError, match="renders differ"):
        assert_pixel_similar(base, very_different, tolerance=0.001)


@pytest.mark.feature("FNT-12")
def test_changed_outside_ignores_changes_inside_the_allowed_boxes() -> None:
    import numpy as np

    from engine.verify import changed_outside

    before = np.zeros((10, 10, 3), dtype=np.uint8)
    after = before.copy()
    after[2:4, 2:4] = 255  # inside the allowed box
    assert changed_outside(before, after, [(2, 2, 4, 4)]) == 0.0
    after[8, 8] = 255  # outside it
    assert changed_outside(before, after, [(2, 2, 4, 4)]) == pytest.approx(1 / 100)
