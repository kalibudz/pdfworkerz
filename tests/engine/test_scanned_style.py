"""FNT-16: estimating the style of scanned text -- font class, weight, slant, size, colour."""

from __future__ import annotations

import cv2
import numpy as np
import pymupdf
import pytest

from engine.scanned_style import FACES, estimate_style

DPI = 300
TEXTS = ("Invoice Number 20481", "Payment due within thirty days", "Total 1,250.00")


def scanned_line(
    text: str,
    *,
    face: str = "helv",
    size: float = 12,
    color: tuple[int, int, int] = (0, 0, 0),
    fontfile: str | None = None,
    seed: int = 1,
) -> np.ndarray:
    """One line of ``text`` set in a known face, size and colour, rasterised at 300 dpi and
    then made to look scanned: blurred, with sensor noise. Cropped around the line."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    name = face
    if fontfile:
        page.insert_font(fontname="Custom", fontfile=fontfile)
        name = "Custom"
    page.insert_text((72, 200), text, fontsize=size, fontname=name, color=tuple(c / 255 for c in color))
    pix = page.get_pixmap(dpi=DPI, alpha=False)
    full = cv2.cvtColor(np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3), cv2.COLOR_RGB2BGR)
    scale = DPI / 72
    width = (
        pymupdf.Font(fontfile=fontfile).text_length(text, size)
        if fontfile
        else pymupdf.Font(face).text_length(text, size)
    )
    top, bottom = int((200 - size * 1.2) * scale), int((200 + size * 0.5) * scale)
    crop = full[top:bottom, int(60 * scale) : int((72 + width + 24) * scale)]
    noise = np.random.default_rng(seed).normal(0, 6, crop.shape)
    return np.clip(cv2.GaussianBlur(crop, (0, 0), 0.8).astype(np.float32) + noise, 0, 255).astype(np.uint8)


@pytest.mark.parametrize("size", [8, 10, 12, 14, 18, 24])
@pytest.mark.parametrize("text", TEXTS)
@pytest.mark.feature("FNT-16", criterion=1)
def test_the_size_in_points_is_found_within_a_point(text: str, size: int) -> None:
    for face in ("helv", "tiro", "cour"):
        style = estimate_style(scanned_line(text, face=face, size=size), text, dpi=DPI)
        assert style is not None
        assert abs(style.size_pt - size) <= 1.0, (face, size, style.size_pt)


@pytest.mark.parametrize(
    ("ink", "name"),
    [((0, 0, 0), "black"), ((10, 30, 140), "dark blue"), ((150, 20, 20), "dark red"), ((20, 110, 40), "dark green")],
)
@pytest.mark.feature("FNT-16", criterion=2)
def test_ink_colours_are_told_apart(ink: tuple[int, int, int], name: str) -> None:
    style = estimate_style(scanned_line(TEXTS[0], face="tiro", color=ink), TEXTS[0], dpi=DPI)
    assert style is not None
    assert max(abs(a - b) for a, b in zip(style.color, ink, strict=True)) <= 25, (name, style.color)
    assert min(style.background) >= 245, "the paper is read as white"


@pytest.mark.feature("FNT-16", criterion=2)
def test_black_blue_and_red_are_never_confused() -> None:
    estimates = {
        name: estimate_style(scanned_line(TEXTS[1], color=ink), TEXTS[1], dpi=DPI).color  # type: ignore[union-attr]
        for name, ink in {"black": (0, 0, 0), "blue": (10, 30, 140), "red": (150, 20, 20)}.items()
    }
    black, blue, red = estimates["black"], estimates["blue"], estimates["red"]
    assert blue[2] > blue[0] + 80 and red[0] > red[2] + 80 and max(black) < 40


@pytest.mark.parametrize("text", TEXTS)
@pytest.mark.feature("FNT-16", criterion=3)
def test_every_face_class_weight_and_slant_is_recognised(text: str) -> None:
    for (font_class, bold, italic), face in FACES.items():
        for size in (10, 14):
            style = estimate_style(scanned_line(text, face=face, size=size), text, dpi=DPI)
            assert style is not None
            assert (style.font_class, style.bold, style.italic) == (font_class, bold, italic), (face, size, style)
            assert style.base_font == face


@pytest.mark.feature("FNT-16", criterion=4)
def test_a_clear_match_is_high_confidence_and_a_short_word_is_not() -> None:
    clear = estimate_style(scanned_line(TEXTS[0]), TEXTS[0], dpi=DPI)
    assert clear is not None and clear.confidence == "high" and clear.score > 0.9
    short = estimate_style(scanned_line("Hi"), "Hi", dpi=DPI)
    assert short is not None and short.confidence != "high", "two letters are too little to claim a face"


@pytest.mark.feature("FNT-16", criterion=4)
def test_a_face_that_is_not_one_of_the_candidates_is_never_called_high_confidence() -> None:
    for fontfile in ("assets/fonts/OpenSans-Regular.ttf", "assets/fonts/OpenSans-Bold.ttf", "assets/fonts/Vera.ttf"):
        style = estimate_style(scanned_line(TEXTS[1], fontfile=fontfile), TEXTS[1], dpi=DPI)
        assert style is not None and style.confidence != "high", fontfile
        assert 8 <= style.size_pt <= 16, "and its size is still in the right region"


@pytest.mark.feature("FNT-16", criterion=4)
def test_nothing_to_measure_gives_no_estimate() -> None:
    blank = np.full((60, 400, 3), 255, np.uint8)
    assert estimate_style(blank, "text", dpi=DPI) is None
    assert estimate_style(scanned_line(TEXTS[0]), "   ", dpi=DPI) is None
