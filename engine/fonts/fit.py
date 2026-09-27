"""FNT-10: fit-to-width.

A replacement rarely has exactly the same natural width as the text it
replaces. Rather than silently running long, short, or overlapping the
next character, SPEC.md section 5.4 sets an order of adjustment:

1. If the natural width is already within tolerance of the target, draw
   it unchanged.
2. Otherwise adjust tracking (Tc): solve directly for the Tc that makes
   the total width match, since width is linear in Tc for a fixed
   horizontal scale. If the needed change is small (within
   ``MAX_TRACKING_DELTA_PT``), use it -- it's the least visually
   disruptive option.
3. Otherwise adjust horizontal scaling (Tz) instead, solved the same way
   and clamped to ``MIN_HORIZONTAL_SCALE``/``MAX_HORIZONTAL_SCALE`` (94-106%,
   per SPEC.md), leaving Tc at its original value.
4. If even clamped Tz doesn't reach the target within
   ``OVERFLOW_TOLERANCE``, report ``fits=False`` rather than forcing a
   distorted result -- the caller should consider reflow (FNT-11, not yet
   built) or accept the mismatch and say so.
"""

from __future__ import annotations

from dataclasses import dataclass

import pymupdf

from engine.fonts.style import TextState, advance_for_char

MAX_TRACKING_DELTA_PT = 2.0
MIN_HORIZONTAL_SCALE = 94.0
MAX_HORIZONTAL_SCALE = 106.0
OVERFLOW_TOLERANCE = 0.02
_CLOSE_ENOUGH = 0.005


@dataclass(frozen=True)
class FitResult:
    text_state: TextState
    """The text state to draw with: Tc and/or Tz adjusted to fit, when possible."""
    natural_width: float
    """What the text actually measures at `text_state`."""
    target_width: float
    fits: bool
    """False when even Tz at its tolerance boundary doesn't reach `target_width`
    within OVERFLOW_TOLERANCE."""


def _measure(font: pymupdf.Font, text: str, font_size: float, text_state: TextState) -> float:
    widths = font.char_lengths(text, fontsize=font_size) if text else []
    return sum(advance_for_char(width, char, text_state) for char, width in zip(text, widths, strict=True))


def fit_to_width(
    font: pymupdf.Font, text: str, font_size: float, text_state: TextState, target_width: float
) -> FitResult:
    """Find the Tc/Tz adjustment (if any) that makes `text` occupy `target_width`."""
    natural = _measure(font, text, font_size, text_state)

    if not text or target_width <= 0 or abs(natural - target_width) <= abs(target_width) * _CLOSE_ENOUGH:
        return FitResult(text_state=text_state, natural_width=natural, target_width=target_width, fits=True)

    widths = font.char_lengths(text, fontsize=font_size)
    glyph_count = len(text)
    space_count = text.count(" ")
    scale = text_state.horizontal_scale / 100.0
    sum_widths = sum(widths)

    # Tier 1: solve for Tc alone (Tz, Tw unchanged).
    # target = scale * (sum_widths + n*Tc + space_count*Tw)  =>  Tc = (target/scale - sum_widths - space_count*Tw) / n
    if scale > 0:
        needed_tc = (target_width / scale - sum_widths - space_count * text_state.word_spacing) / glyph_count
        if abs(needed_tc - text_state.char_spacing) <= MAX_TRACKING_DELTA_PT:
            tracked = text_state.model_copy(update={"char_spacing": needed_tc})
            return FitResult(
                text_state=tracked,
                natural_width=_measure(font, text, font_size, tracked),
                target_width=target_width,
                fits=True,
            )

    # Tier 2: solve for Tz alone (Tc, Tw unchanged), clamped to the tolerance band.
    base = sum_widths + glyph_count * text_state.char_spacing + space_count * text_state.word_spacing
    if base <= 0:
        return FitResult(text_state=text_state, natural_width=natural, target_width=target_width, fits=False)

    wanted_scale_pct = 100.0 * target_width / base
    clamped_scale_pct = max(MIN_HORIZONTAL_SCALE, min(MAX_HORIZONTAL_SCALE, wanted_scale_pct))
    scaled = text_state.model_copy(update={"horizontal_scale": clamped_scale_pct})
    achieved = _measure(font, text, font_size, scaled)
    fits = abs(achieved - target_width) <= abs(target_width) * OVERFLOW_TOLERANCE
    return FitResult(text_state=scaled, natural_width=achieved, target_width=target_width, fits=fits)
