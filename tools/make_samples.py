"""Write a small set of synthetic PDFs to try PDFWorkerz on by hand.

    python tools/make_samples.py                # to C:\\pdfworkerz-docs\\samples (or ~/pdfworkerz-docs/samples)
    python tools/make_samples.py --out DIR

Every document is invented: made-up names, companies and numbers, marked SAMPLE.
Open one in the web UI (paste its path into "Open a PDF"), try edits, and report
anything that looks wrong -- with the session's recipe (Export recipe) the exact
edit sequence can be replayed with `pdfworkerz run`.

- letter.pdf     paragraphs to edit, re-wrap and move
- form.pdf       labels and boxes (shapes) to align, distribute and nudge
- flyer.pdf      images, shapes and headings to arrange, copy and paste
- mixed-fonts.pdf  named, Arial-subset and nameless embedded fonts: edit a
                   line, then move it (the case that once fell to a look-alike font)
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

import pymupdf
from fontTools import subset
from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parent.parent
VERA = REPO / "assets" / "fonts" / "Vera.ttf"
ROBOTO = REPO / "assets" / "fonts" / "Roboto-Regular.ttf"
ARIAL = Path("C:/Windows/Fonts/arial.ttf")


def _default_out() -> Path:
    return (
        Path("C:/pdfworkerz-docs/samples") if sys.platform == "win32" else Path.home() / "pdfworkerz-docs" / "samples"
    )


def _stamp(page: pymupdf.Page) -> None:
    page.insert_text((page.rect.width - 110, 30), "SAMPLE", fontname="hebo", fontsize=14, color=(0.8, 0.1, 0.1))


def _png(color: tuple[int, int, int], label: str, size: tuple[int, int] = (240, 160)) -> bytes:
    image = Image.new("RGB", size, color)
    ImageDraw.Draw(image).text((12, 12), label, fill=(255, 255, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _letter(path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    _stamp(page)
    page.insert_text((72, 90), "Northwind Garden Supplies", fontname="hebo", fontsize=16)
    page.insert_text((72, 108), "14 Example Lane, Sampletown", fontname="helv", fontsize=10)
    paragraphs = [
        [
            "Dear Ms. Placeholder,",
        ],
        [
            "Thank you for your order of twelve terracotta planters. They",
            "left our warehouse this morning and should reach you within",
            "three working days.",
        ],
        [
            "If anything arrives damaged, reply to this letter and we will",
            "send a replacement at no cost.",
        ],
        [
            "Kind regards,",
            "Jordan Example",
        ],
    ]
    y = 170.0
    for paragraph in paragraphs:
        for line in paragraph:
            page.insert_text((72, y), line, fontname="tiro", fontsize=12)
            y += 16
        y += 14
    doc.save(path)


def _form(path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    _stamp(page)
    page.insert_text((72, 90), "Membership application (sample form)", fontname="hebo", fontsize=15)
    labels = ["Full name", "Street address", "Town", "Phone", "Email"]
    # Deliberately a little uneven, to try align, distribute and nudge on.
    for i, label in enumerate(labels):
        top = 130 + i * 52 + (i % 2) * 6
        page.insert_text((72 + (i % 3) * 4, top + 14), label, fontname="helv", fontsize=11)
        shape = page.new_shape()
        shape.draw_rect(pymupdf.Rect(190 + (i % 2) * 7, top, 520, top + 26))
        shape.finish(color=(0.2, 0.2, 0.2), width=1)
        shape.commit()
    for i, choice in enumerate(["Individual", "Family", "Student"]):
        x = 80 + i * 150 + (i == 2) * 18
        shape = page.new_shape()
        shape.draw_rect(pymupdf.Rect(x, 430, x + 14, 444))
        shape.finish(color=(0, 0, 0), width=1)
        shape.commit()
        page.insert_text((x + 22, 442), choice, fontname="helv", fontsize=11)
    doc.save(path)


def _flyer(path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    _stamp(page)
    page.insert_text((80, 100), "Spring Plant Fair", fontname="hebo", fontsize=28, color=(0.1, 0.4, 0.2))
    page.insert_text((80, 130), "Saturday, 10am - 4pm, Sampletown Hall", fontname="helv", fontsize=13)
    page.insert_image(pymupdf.Rect(80, 170, 260, 290), stream=_png((60, 140, 80), "photo 1"))
    page.insert_image(pymupdf.Rect(300, 185, 480, 305), stream=_png((170, 110, 40), "photo 2"))
    for x, color in ((90, (0.9, 0.6, 0.1)), (230, (0.2, 0.5, 0.8)), (390, (0.7, 0.2, 0.4))):
        shape = page.new_shape()
        shape.draw_oval(pymupdf.Rect(x, 360, x + 90, 420))
        shape.finish(color=color, fill=color, width=1)
        shape.commit()
    page.insert_text((95, 460), "Seedlings", fontname="helv", fontsize=12)
    page.insert_text((238, 466), "Workshops", fontname="helv", fontsize=12)
    page.insert_text((402, 455), "Raffle", fontname="helv", fontsize=12)
    doc.save(path)


def _nameless(font: Path, text: str) -> bytes:
    """A subset with no name table, the way some generators embed fonts."""
    tt = TTFont(font)
    options = subset.Options()
    options.drop_tables += ["name"]
    subsetter = subset.Subsetter(options)
    subsetter.populate(text=text)
    subsetter.subset(tt)
    if "name" in tt:
        del tt["name"]
    buffer = io.BytesIO()
    tt.save(buffer)
    return buffer.getvalue()


def _mixed_fonts(path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    _stamp(page)
    page.insert_font(fontname="R", fontbuffer=ROBOTO.read_bytes())
    page.insert_text((72, 90), "Invoice (sample)", fontname="R", fontsize=18)
    arial = ARIAL if ARIAL.exists() else VERA
    page.insert_font(fontname="A", fontbuffer=arial.read_bytes())
    page.insert_font(fontname="L", fontbuffer=_nameless(VERA, "ABCDEFGHIJKLMNOPQRSTUVWXYZ "))
    page.insert_font(fontname="D", fontbuffer=_nameless(VERA, "0123456789,."))
    rows = [("POTTING SOIL", "1,250.00"), ("PLANTERS", "3,480.50"), ("DELIVERY", "95.00")]
    for i, (item, amount) in enumerate(rows):
        y = 150 + i * 24
        page.insert_text((72, y), item, fontname="L", fontsize=11)
        page.insert_text((420, y), amount, fontname="D", fontsize=11)
    page.insert_text((72, 240), "Total due", fontname="A", fontsize=12)
    page.insert_text((420, 240), "4,825.50", fontname="A", fontsize=12)
    doc.subset_fonts()  # the named fonts become ABCDEF+ subsets, as generators write them
    doc.save(path)


SAMPLES = {"letter.pdf": _letter, "form.pdf": _form, "flyer.pdf": _flyer, "mixed-fonts.pdf": _mixed_fonts}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=_default_out(), help="folder to write the samples to")
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    for name, make in SAMPLES.items():
        make(args.out / name)
        print(args.out / name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
