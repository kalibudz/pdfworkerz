"""P5 slice 3: page design (DES-01..06)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pymupdf
import pytest

from engine.document import Document
from engine.errors import OpValidationError
from engine.ops.base import parse_op
from engine.ops.journal import UndoRedoJournal
from tests.corpus.build_corpus import ENCRYPTED_TEXT, Corpus


def _doc(tmp_path: Path, count: int = 3, rotations: tuple[int, ...] = ()) -> Document:
    doc = pymupdf.open()
    for n in range(count):
        page = doc.new_page()
        page.insert_text((72, 300), f"Body {n + 1}")
        if n < len(rotations):
            page.set_rotation(rotations[n])
    doc.save(tmp_path / "d.pdf")
    return Document.open(tmp_path / "d.pdf")


def _apply(doc: Document, data: dict[str, object]) -> object:
    op = parse_op(data)
    op.check_pages(doc)
    return op.apply(doc)


def _visible_words(page: pymupdf.Page) -> dict[str, pymupdf.Rect]:
    """Each word with its box as the reader sees the page (rotation applied)."""
    return {w[4]: pymupdf.Rect(w[:4]) * page.rotation_matrix for w in page.get_text("words")}


def _ink(page: pymupdf.Page, test: object) -> list[tuple[int, int]]:
    pix = page.get_pixmap(dpi=36)
    return [(x, y) for y in range(pix.height) for x in range(pix.width) if test(pix.pixel(x, y))]  # type: ignore[operator]


# -- DES-01 page numbers --


@pytest.mark.feature("DES-01")
def test_page_numbers_with_format_start_and_skipped_pages(tmp_path: Path) -> None:
    doc = _doc(tmp_path, 4)
    labels = _apply(
        doc, {"op": "page_numbers", "skip_page_indices": [0], "template": "Page {n} of {total}", "start": 1}
    )
    assert labels == ["Page 1 of 3", "Page 2 of 3", "Page 3 of 3"]
    assert "Page" not in doc.raw[0].get_text()  # the cover is skipped
    words = _visible_words(doc.raw[1])
    assert words["Page"].y0 > 780  # at the bottom
    center = (words["Page"].x0 + words["3"].x1) / 2
    assert abs(center - 297.5) < 2  # centered


@pytest.mark.feature("DES-01")
def test_page_numbers_read_upright_on_rotated_and_cropped_pages(tmp_path: Path) -> None:
    doc = _doc(tmp_path, 4, rotations=(0, 90, 180, 270))
    doc.raw[0].set_cropbox(pymupdf.Rect(50, 50, 545, 792))
    _apply(doc, {"op": "page_numbers", "position": "bottom-right", "template": "#{n}"})
    for index in range(4):
        page = doc.raw[index]
        label = f"#{index + 1}"
        box = _visible_words(page)[label]
        visible = page.rect
        assert box in visible and box.x1 > visible.width - 60 and box.y1 > visible.height - 60, (index, box)
        line = page.get_text("dict")["blocks"][-1]["lines"][0]
        direction = pymupdf.Point(line["dir"]) * page.rotation_matrix - pymupdf.Point(0, 0) * page.rotation_matrix
        assert direction.x > 0.99  # reads left to right for the reader


@pytest.mark.feature("DES-01")
def test_page_numbers_refuse_a_bad_template_or_position(tmp_path: Path) -> None:
    doc = _doc(tmp_path)
    with pytest.raises(OpValidationError, match="placeholders are"):
        _apply(doc, {"op": "page_numbers", "template": "{page}"})
    with pytest.raises(OpValidationError, match="unknown position"):
        _apply(doc, {"op": "page_numbers", "position": "middle"})
    with pytest.raises(OpValidationError, match="no pages"):
        _apply(doc, {"op": "page_numbers", "page_indices": [0], "skip_page_indices": [0]})


# -- DES-02 Bates --


@pytest.mark.feature("DES-02")
def test_bates_numbers_every_page_in_sequence(tmp_path: Path) -> None:
    doc = _doc(tmp_path, 3)
    first_last = _apply(doc, {"op": "bates", "prefix": "ACME-", "start": 41, "digits": 5})
    assert first_last == ("ACME-00041", "ACME-00043")
    assert [doc.raw[i].search_for(f"ACME-0004{i + 1}") != [] for i in range(3)] == [True, True, True]
    with pytest.raises(OpValidationError, match="can't hold"):
        _apply(doc, {"op": "bates", "start": 99, "digits": 2})


# -- DES-03 headers and footers --


@pytest.mark.feature("DES-03")
def test_header_and_footer_fields_and_placeholders(tmp_path: Path) -> None:
    doc = _doc(tmp_path, 2)
    doc.raw.set_metadata({"title": "Annual Report"})
    count = _apply(
        doc,
        {
            "op": "header_footer",
            "header": ["{title}", "", "{date}"],
            "footer": ["{file}", "Page {page} of {total}", ""],
            "date": "2026-09-29",
        },
    )
    assert count == 2
    page = doc.raw[1]
    words = _visible_words(page)
    assert words["Annual"].y0 < 60 and words["Annual"].x0 < 60  # header left
    assert words["2026-09-29"].x1 > 540  # header right
    assert words["d.pdf"].y0 > 780  # footer left
    assert "Page 2 of 2" in page.get_text()
    with pytest.raises(OpValidationError, match="give some"):
        _apply(doc, {"op": "header_footer"})


# -- DES-04 watermarks --


@pytest.mark.feature("DES-04")
@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_text_watermark_is_centered_and_rises_left_to_right(tmp_path: Path, rotation: int) -> None:
    doc = _doc(tmp_path, 1, rotations=(rotation,))
    doc.raw[0].clean_contents()
    _apply(doc, {"op": "watermark", "text": "DRAFT", "opacity": 1, "color": "red", "size": 80})
    page = doc.raw[0]
    ink = _ink(page, lambda p: p[0] > 150 and p[1] < 100)
    xs, ys = [x for x, _ in ink], [y for _, y in ink]
    width, height = page.rect.width / 2, page.rect.height / 2  # the render is at 36 dpi: half size
    assert abs((min(xs) + max(xs)) / 2 - width / 2) < 6 and abs((min(ys) + max(ys)) / 2 - height / 2) < 6
    third = (max(xs) - min(xs)) / 3
    left = [y for x, y in ink if x < min(xs) + third]
    right = [y for x, y in ink if x > max(xs) - third]
    assert sum(left) / len(left) > sum(right) / len(right)  # 45 degrees counterclockwise


@pytest.mark.feature("DES-04")
def test_text_watermark_opacity_and_behind_content(tmp_path: Path) -> None:
    doc = _doc(tmp_path, 1)
    page = doc.raw[0]
    page.draw_rect(pymupdf.Rect(0, 0, 595, 842), color=None, fill=(0, 0, 1))  # an opaque full-page drawing
    _apply(doc, {"op": "watermark", "text": "BEHIND", "color": "red", "opacity": 1, "behind": True, "rotation": 0})
    assert "BEHIND" in page.get_text()  # still real text
    assert not _ink(page, lambda p: p[0] > 150)  # hidden by the drawing on top
    _apply(doc, {"op": "watermark", "text": "OVER", "color": "red", "opacity": 0.5, "rotation": 0})
    reds = _ink(page, lambda p: p[0] > 60)
    assert reds and all(p_red < 250 for p_red in [page.get_pixmap(dpi=36).pixel(x, y)[0] for x, y in reds[:50]])


@pytest.mark.feature("DES-04")
@pytest.mark.parametrize("rotation", [0, 90])
def test_image_watermark_is_faded_centered_and_upright(tmp_path: Path, rotation: int) -> None:
    image = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 20), False)
    image.set_rect(image.irect, (255, 0, 0))
    image.set_rect(pymupdf.IRect(0, 0, 10, 20), (0, 0, 255))  # blue on the left: shows orientation
    image.save(tmp_path / "logo.png")
    doc = _doc(tmp_path, 1, rotations=(rotation,))
    _apply(doc, {"op": "watermark", "image": str(tmp_path / "logo.png"), "opacity": 0.5})
    page = doc.raw[0]
    pix = page.get_pixmap(dpi=36)
    red = _ink(page, lambda p: p[0] > 200 and p[1] < 200)
    blue = _ink(page, lambda p: p[2] > 200 and p[0] < 200)
    xs, ys = [x for x, _ in red], [y for _, y in red]
    assert max(xs) - min(xs) > max(ys) - min(ys)  # still wider than tall
    assert sum(x for x, _ in blue) / len(blue) < min(xs) + 5  # blue still on the left
    assert pix.pixel(xs[len(xs) // 2], ys[len(ys) // 2])[1] > 100  # faded: red mixed with white
    with pytest.raises(OpValidationError, match="does not exist"):
        _apply(doc, {"op": "watermark", "image": str(tmp_path / "missing.png")})


# -- DES-05 backgrounds --


@pytest.mark.feature("DES-05")
def test_background_color_sits_behind_the_text(tmp_path: Path) -> None:
    doc = _doc(tmp_path, 2, rotations=(90,))
    _apply(doc, {"op": "background", "color": "#ffff00", "page_indices": [0]})
    page = doc.raw[0]
    pix = page.get_pixmap(dpi=36)
    assert pix.pixel(2, 2) == (255, 255, 0) and pix.pixel(pix.width - 3, pix.height - 3) == (255, 255, 0)
    assert "Body 1" in page.get_text()
    assert _ink(page, lambda p: p[0] < 200)  # the text is drawn over the color (yellow is 255 red)
    assert doc.raw[1].get_pixmap(dpi=36).pixel(2, 2) == (255, 255, 255)


@pytest.mark.feature("DES-05")
def test_background_image_fills_the_page(tmp_path: Path) -> None:
    image = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 10, 10), False)
    image.set_rect(image.irect, (0, 200, 0))
    image.save(tmp_path / "bg.png")
    doc = _doc(tmp_path, 1)
    _apply(doc, {"op": "background", "image": str(tmp_path / "bg.png")})
    pix = doc.raw[0].get_pixmap(dpi=36)
    assert pix.pixel(1, 1) == (0, 200, 0) and pix.pixel(pix.width - 2, pix.height - 2) == (0, 200, 0)
    with pytest.raises(OpValidationError, match="either a color or an image"):
        _apply(doc, {"op": "background"})


# -- DES-06 stamps --


@pytest.mark.feature("DES-06")
def test_preset_stamp_is_a_movable_stamp_annotation(tmp_path: Path) -> None:
    doc = _doc(tmp_path, 2)
    assert _apply(doc, {"op": "stamp", "name": "Approved", "page_indices": [1]}) == 1
    annots = [(a.type[1], a.rect) for a in doc.raw[1].annots()]
    assert [kind for kind, _ in annots] == ["Stamp"]
    assert annots[0][1].x1 > 500 and annots[0][1].y0 < 60  # top right
    assert not list(doc.raw[0].annots())
    with pytest.raises(OpValidationError, match="unknown stamp"):
        _apply(doc, {"op": "stamp", "name": "maybe"})


@pytest.mark.feature("DES-06")
def test_custom_text_stamp_draws_a_bordered_box(tmp_path: Path) -> None:
    doc = _doc(tmp_path, 1, rotations=(270,))
    _apply(doc, {"op": "stamp", "text": "PAID", "position": "bottom-left", "color": "green"})
    page = doc.raw[0]
    word = _visible_words(page)["PAID"]
    assert word.x0 < 200 and word.y1 > page.rect.height - 100  # bottom left as the reader sees it
    assert page.get_drawings()  # the border


# -- journaling and encryption --


@pytest.mark.feature("DES-04")
def test_design_ops_undo_and_keep_encryption(corpus: Corpus, tmp_path: Path) -> None:
    source = tmp_path / "enc.pdf"
    shutil.copy(corpus.encrypted_aes_256, source)
    journal = UndoRedoJournal(Document.open(source, password=corpus.user_password))
    journal.record(parse_op({"op": "watermark", "text": "SECRET"}))
    journal.record(parse_op({"op": "page_numbers"}))
    journal.document.save(tmp_path / "out.pdf")
    with pymupdf.open(tmp_path / "out.pdf") as saved:
        assert saved.needs_pass and saved.authenticate(corpus.user_password)
        assert "SECRET" in saved[0].get_text() and ENCRYPTED_TEXT in saved[0].get_text()
    journal.undo()
    journal.undo()
    assert "SECRET" not in journal.document.raw[0].get_text()
    journal.document.close()


# -- command bar --


@pytest.mark.feature("DES-01")
@pytest.mark.feature("DES-02")
@pytest.mark.feature("DES-03")
@pytest.mark.feature("DES-04")
@pytest.mark.feature("DES-05")
@pytest.mark.feature("DES-06")
def test_design_commands_plan_the_matching_ops(tmp_path: Path) -> None:
    from engine.commands import parse_command

    def ops(text: str) -> list[dict[str, object]]:
        return parse_command(text, page_count=4).ops

    assert ops('number pages from 3 except page 1 at bottom-right as "Page {n}"') == [
        {"op": "page_numbers", "start": 3, "position": "bottom-right", "skip_page_indices": [0], "template": "Page {n}"}
    ]
    assert ops('bates "ACME-" from 41') == [{"op": "bates", "prefix": "ACME-", "start": 41}]
    assert ops('footer "Confidential" left on pages 1-2') == [
        {"op": "header_footer", "footer": ["Confidential", "", ""], "page_indices": [0, 1]}
    ]
    assert ops('watermark "DRAFT" behind') == [{"op": "watermark", "text": "DRAFT", "behind": True}]
    assert ops("background #fff8e0 on page 1") == [{"op": "background", "color": "#fff8e0", "page_indices": [0]}]
    assert ops('stamp "Approved" on page 2') == [
        {"op": "stamp", "position": "top-right", "name": "approved", "page_indices": [1]}
    ]
    assert ops('stamp "PAID" at center') == [{"op": "stamp", "position": "center", "text": "PAID"}]
    # and each plan runs
    doc = _doc(tmp_path, 4)
    for command in ["number pages", 'bates "X-"', 'header "Top"', 'watermark "W"', "background grey", 'stamp "draft"']:
        for op in parse_command(command, page_count=4).ops:
            _apply(doc, op)


@pytest.mark.feature("DES-06")
@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_preset_stamp_reads_upright_on_rotated_pages(tmp_path: Path, rotation: int) -> None:
    doc = _doc(tmp_path, 1, rotations=(rotation,))
    _apply(doc, {"op": "stamp", "name": "approved"})
    page = doc.raw[0]
    box = _visible_words(page)["APPROVED"]
    assert box in page.rect and box.x1 > page.rect.width - 60 and box.y0 < 100  # top right as seen
    line = next(
        ln for b in page.get_text("dict")["blocks"] for ln in b.get("lines", []) if "APPROVED" in ln["spans"][0]["text"]
    )
    direction = pymupdf.Point(line["dir"]) * page.rotation_matrix - pymupdf.Point(0, 0) * page.rotation_matrix
    assert direction.x > 0.99


@pytest.mark.feature("DES-05")
def test_background_image_is_upright_on_a_rotated_page(tmp_path: Path) -> None:
    image = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 20), False)
    image.set_rect(image.irect, (255, 255, 255))
    image.set_rect(pymupdf.IRect(0, 0, 8, 20), (255, 0, 0))  # red down the left edge
    image.save(tmp_path / "edge.png")
    doc = _doc(tmp_path, 1, rotations=(90,))
    _apply(doc, {"op": "background", "image": str(tmp_path / "edge.png")})
    pix = doc.raw[0].get_pixmap(dpi=36)
    assert pix.pixel(2, pix.height // 2)[1] < 50  # red at the left as the reader sees it
    assert pix.pixel(pix.width // 2, 2)[1] > 200  # not along the top
