"""FNT-10: fit-to-width."""

from __future__ import annotations

import pymupdf
import pytest

from engine.fonts.fit import (
    MAX_HORIZONTAL_SCALE,
    MIN_HORIZONTAL_SCALE,
    fit_to_width,
)
from engine.fonts.style import TextState


@pytest.fixture(scope="module")
def font() -> pymupdf.Font:
    return pymupdf.Font("helv")


@pytest.mark.feature("FNT-10")
def test_already_matching_width_needs_no_adjustment(font: pymupdf.Font) -> None:
    default_state = TextState()
    target = font.text_length("Hello", fontsize=14)
    result = fit_to_width(font, "Hello", 14, default_state, target)
    assert result.fits is True
    assert result.text_state == default_state


@pytest.mark.feature("FNT-10")
def test_small_mismatch_is_absorbed_by_tracking(font: pymupdf.Font) -> None:
    default_state = TextState()
    target = font.text_length("Cats", fontsize=14)
    result = fit_to_width(font, "Cats!", 14, default_state, target)
    assert result.fits is True
    assert result.text_state.horizontal_scale == 100.0  # scaling untouched
    assert result.text_state.char_spacing != 0.0  # tracking did the work
    assert result.natural_width == pytest.approx(target, abs=0.01)


@pytest.mark.feature("FNT-10")
def test_larger_mismatch_uses_horizontal_scaling(font: pymupdf.Font) -> None:
    default_state = TextState()
    target = font.text_length("Short", fontsize=14)
    result = fit_to_width(font, "A Rather Much Longer Phrase", 14, default_state, target)
    assert result.text_state.char_spacing == default_state.char_spacing  # tracking untouched
    assert result.text_state.horizontal_scale != 100.0


@pytest.mark.feature("FNT-10")
def test_horizontal_scaling_is_clamped_to_the_tolerance_band(font: pymupdf.Font) -> None:
    default_state = TextState()
    target = font.text_length("Hi", fontsize=14)
    result = fit_to_width(font, "A Very Much Longer Sentence Than Hi", 14, default_state, target)
    assert MIN_HORIZONTAL_SCALE <= result.text_state.horizontal_scale <= MAX_HORIZONTAL_SCALE
    assert result.text_state.horizontal_scale == MIN_HORIZONTAL_SCALE  # shrinking hits the floor


@pytest.mark.feature("FNT-10")
def test_extreme_mismatch_reports_it_does_not_fit(font: pymupdf.Font) -> None:
    default_state = TextState()
    target = font.text_length("Hi", fontsize=14)
    result = fit_to_width(font, "A Very Much Longer Sentence Than Hi", 14, default_state, target)
    assert result.fits is False


@pytest.mark.feature("FNT-10")
def test_empty_text_trivially_fits() -> None:
    font = pymupdf.Font("helv")
    result = fit_to_width(font, "", 14, TextState(), target_width=50.0)
    assert result.fits is True
    assert result.natural_width == 0.0


@pytest.mark.feature("FNT-10")
def test_zero_target_width_is_handled_without_a_crash(font: pymupdf.Font) -> None:
    result = fit_to_width(font, "Text", 14, TextState(), target_width=0.0)
    assert result.fits is True  # nothing to fit against; drawn at natural width


@pytest.mark.feature("FNT-10")
def test_fit_respects_an_existing_non_default_text_state(font: pymupdf.Font) -> None:
    """Fitting from a starting state with its own Tc must still solve correctly,
    not assume Tc starts at zero."""
    starting = TextState(char_spacing=1.0)
    natural_at_start = font.text_length("Wide", fontsize=14) + 1.0 * 4  # rough: 4 chars * extra Tc
    result = fit_to_width(font, "Wide", 14, starting, target_width=natural_at_start)
    assert result.natural_width == pytest.approx(natural_at_start, rel=0.05)
