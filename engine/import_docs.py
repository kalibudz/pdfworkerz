"""CVT-01..06: make a PDF *from* something else.

* Word, Excel and PowerPoint files go through LibreOffice (headless): the only faithful open-source
  reader of those formats. Layout can differ in small ways from Microsoft Office's own PDF.
* Pictures become pages with PyMuPDF (CVT-04); HTML, web addresses, plain text and Markdown are
  laid out by PyMuPDF's own story engine (CVT-05, CVT-06) and need nothing installed.

Every file is checked before any work starts, so a wrong file is refused by name instead of
producing a confusing half result.
"""

from __future__ import annotations

import html as htmllib
import io
import re
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal

import pymupdf
from markdown_it import MarkdownIt
from PIL import Image

from engine.errors import ConversionError
from engine.external import run_tool
from engine.fonts.research import data_dir
from engine.rasters import PAPER_POINTS, NamedFile, Paper, open_picture, page_box

OfficeKind = Literal["word", "excel", "powerpoint"]
OFFICE_EXTENSIONS: dict[OfficeKind, tuple[str, ...]] = {
    "word": (".docx", ".doc", ".odt", ".rtf"),
    "excel": (".xlsx", ".xls", ".ods", ".csv"),
    "powerpoint": (".pptx", ".ppt", ".odp"),
}
_OOXML_PART = {"word": "word/", "excel": "xl/", "powerpoint": "ppt/"}
_KIND_NAME = {"word": "Word document", "excel": "spreadsheet", "powerpoint": "presentation"}
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024
MAX_PAGE_IMAGES = 25
FETCH_TIMEOUT = 20.0
_LO_LOCK = threading.Lock()  # one LibreOffice at a time: two instances sharing a profile corrupt it


# ---------- Word, Excel, PowerPoint (LibreOffice) ----------


def _looks_like(kind: OfficeKind, file: NamedFile) -> bool:
    ext = Path(file.name).suffix.lower()
    if ext not in OFFICE_EXTENSIONS[kind]:
        return False
    head = file.data[:8]
    if ext in (".doc", ".xls", ".ppt"):
        return head == _OLE_MAGIC
    if ext == ".rtf":
        return file.data.lstrip().startswith(b"{\\rtf")
    if ext == ".csv":
        return b"\x00" not in file.data[:4096]
    try:
        with zipfile.ZipFile(io.BytesIO(file.data)) as archive:
            names = archive.namelist()
    except zipfile.BadZipFile:
        return False
    if ext in (".odt", ".ods", ".odp"):
        return "mimetype" in names
    return any(name.startswith(_OOXML_PART[kind]) for name in names)


def office_to_pdf(file: NamedFile, kind: OfficeKind) -> bytes:
    """A Word (.docx .doc .odt .rtf), Excel (.xlsx .xls .ods .csv) or PowerPoint (.pptx .ppt
    .odp) file as PDF. Refused by name if it is not what its kind says."""
    if not _looks_like(kind, file):
        raise ConversionError(
            f"{file.name} is not a readable {_KIND_NAME[kind]} (expected {', '.join(OFFICE_EXTENSIONS[kind])})"
        )
    profile = data_dir() / "libreoffice-profile"
    with tempfile.TemporaryDirectory(prefix="pdfworkerz-office-") as folder:
        source = Path(folder) / f"input{Path(file.name).suffix.lower()}"
        source.write_bytes(file.data)
        output = Path(folder) / "out"
        with _LO_LOCK:
            result = run_tool(
                "soffice",
                [
                    f"-env:UserInstallation={profile.resolve().as_uri()}",
                    "--headless", "--norestore", "--nolockcheck", "--convert-to", "pdf", "--outdir", output, source,
                ],
                timeout=300,
            )  # fmt: skip
        produced = output / "input.pdf"
        if not produced.exists() or produced.stat().st_size == 0:
            detail = (result.stderr or result.stdout).strip()[:300]
            raise ConversionError(
                f"{file.name} could not be converted: LibreOffice produced no PDF ({detail or 'no message'})"
            )
        return produced.read_bytes()


# ---------- pictures ----------


def images_to_pdf(files: Sequence[NamedFile], *, paper: Paper = "fit", margin: float = 0.0) -> bytes:
    """One page per picture, in the order given, each upright (EXIF honoured), centred and
    undistorted. JPEGs go in as they are, with no second compression."""
    if not files:
        raise ConversionError("there are no pictures to convert")
    if margin < 0:
        raise ConversionError("the margin cannot be negative")
    pictures = [(file, open_picture(file)) for file in files]  # every file is checked before any page is made
    document = pymupdf.open()
    for file, picture in pictures:
        width, height = picture.size
        dpi = float(picture.info.get("dpi", (96, 96))[0] or 96)
        page_rect, picture_rect = page_box((width, height), paper, margin, dpi=dpi)
        document.new_page(width=page_rect.width, height=page_rect.height).insert_image(
            picture_rect, stream=_stored_form(file, picture)
        )
    return document.tobytes()


def _stored_form(file: NamedFile, picture: Image.Image) -> bytes:
    """The picture's bytes for the PDF: the original JPEG untouched when nothing had to change
    (it was already upright and opaque), otherwise a fresh lossless PNG."""
    original = Image.open(io.BytesIO(file.data))
    untouched = (
        original.format == "JPEG"
        and original.size == picture.size
        and original.mode in ("RGB", "L")
        and not _exif_turn(original)
    )
    if untouched:
        return file.data
    buffer = io.BytesIO()
    picture.save(buffer, format="PNG")
    return buffer.getvalue()


def _exif_turn(image: Image.Image) -> bool:
    return image.getexif().get(0x0112, 1) not in (0, 1)


# ---------- HTML, text, Markdown (the story engine) ----------

_BASE_CSS = """
body { font-family: sans-serif; line-height: 1.35; }
h1 { font-size: 22pt; margin: 14pt 0 6pt 0; } h2 { font-size: 17pt; margin: 12pt 0 5pt 0; }
h3 { font-size: 14pt; margin: 10pt 0 4pt 0; } h4, h5, h6 { font-size: 12pt; margin: 8pt 0 3pt 0; }
p { margin: 0 0 7pt 0; } li { margin-bottom: 2pt; }
table { border-collapse: collapse; margin: 6pt 0; } th, td { border: 0.6pt solid #888; padding: 3pt 5pt; }
th { background-color: #eeeeee; } code, pre, tt { font-family: monospace; }
pre { margin: 0 0 7pt 0; } blockquote { margin: 0 0 7pt 14pt; color: #444444; }
"""


@dataclass(frozen=True)
class WebPdf:
    pdf: bytes
    notes: list[str] = field(default_factory=list)
    """Anything that could not be carried across (a picture that could not be fetched)."""


def _flow_to_pdf(
    html: str, *, paper: Paper, margin: float, font_size: float, archive: pymupdf.Archive | None = None
) -> bytes:
    if paper == "fit":
        raise ConversionError("choose a paper size (a4, letter or legal) for this kind of document")
    short, long = PAPER_POINTS[paper]
    media = pymupdf.Rect(0, 0, short, long)
    where = pymupdf.Rect(margin, margin, short - margin, long - margin)
    if where.is_empty:
        raise ConversionError("the margin leaves no room on the page")
    story = pymupdf.Story(html=html, user_css=_BASE_CSS, em=font_size, archive=archive)
    out = io.BytesIO()
    writer = pymupdf.DocumentWriter(out)
    more = True
    while more:
        device = writer.begin_page(media)
        more, _ = story.place(where)
        story.draw(device)
        writer.end_page()
    writer.close()
    return out.getvalue()


class _Images(HTMLParser):
    """The ``src`` of every ``<img>`` in a page, in order."""

    def __init__(self) -> None:
        super().__init__()
        self.sources: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "img":
            source = dict(attrs).get("src")
            if source:
                self.sources.append(source)


def html_to_pdf(html: str, *, paper: Paper = "a4", margin: float = 54.0, font_size: float = 11.0) -> bytes:
    """An HTML string as a paginated PDF. Pictures given as ``data:`` addresses are included."""
    return _flow_to_pdf(html, paper=paper, margin=margin, font_size=font_size)


def fetch(url: str, *, limit: int = MAX_DOWNLOAD_BYTES) -> tuple[bytes, str]:
    """The bytes at an http(s) address and the final address after redirects. Any other scheme
    is refused; the download is capped in size and time."""
    parts = urllib.parse.urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ConversionError(f"{url!r} is not a web address; only http:// and https:// are fetched")
    request = urllib.request.Request(url.strip(), headers={"User-Agent": "PDFWorkerz"})
    try:
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT) as response:  # nosec B310
            data = response.read(limit + 1)
            final = str(response.geturl())
    except urllib.error.HTTPError as exc:
        raise ConversionError(f"{url} could not be fetched (the server answered {exc.code} {exc.reason})") from exc
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise ConversionError(f"{url} could not be fetched ({getattr(exc, 'reason', exc)})") from exc
    if len(data) > limit:
        raise ConversionError(f"{url} is larger than {limit // (1024 * 1024)} MB; refusing it")
    return data, final


def _decoded(data: bytes) -> str:
    for encoding in ("utf-8", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def url_to_pdf(url: str, *, paper: Paper = "a4", margin: float = 54.0, font_size: float = 11.0) -> WebPdf:
    """A web page as a PDF: the page, with the pictures it shows (up to a limit), laid out on
    pages. Scripts are not run, so a page that builds itself in the browser comes out empty."""
    data, final = fetch(url)
    page = _decoded(data)
    finder = _Images()
    finder.feed(page)
    archive = pymupdf.Archive()
    notes: list[str] = []
    for number, source in enumerate(dict.fromkeys(finder.sources)):
        if number >= MAX_PAGE_IMAGES:
            notes.append(f"only the first {MAX_PAGE_IMAGES} pictures were fetched")
            break
        if source.startswith("data:"):
            continue
        try:
            picture, _ = fetch(urllib.parse.urljoin(final, htmllib.unescape(source)), limit=MAX_DOWNLOAD_BYTES)
        except ConversionError as exc:
            notes.append(f"a picture was left out: {exc}")
            continue
        name = f"pwimg{number}{re.sub(r'[^.A-Za-z0-9]', '', Path(urllib.parse.urlsplit(source).path).suffix)[:6]}"
        archive.add(picture, name)
        page = page.replace(f'"{source}"', f'"{name}"').replace(f"'{source}'", f"'{name}'")
    return WebPdf(_flow_to_pdf(page, paper=paper, margin=margin, font_size=font_size, archive=archive), notes)


def text_to_pdf(
    text: str,
    *,
    paper: Paper = "a4",
    margin: float = 54.0,
    font_size: float = 10.0,
    font: Literal["mono", "sans", "serif"] = "mono",
) -> bytes:
    """Plain text as pages, keeping its line breaks and indentation, in a monospace font by
    default (so columns of figures line up). Characters outside Latin letters still draw."""
    family = {"mono": "monospace", "sans": "sans-serif", "serif": "serif"}[font]
    body = htmllib.escape(text.replace("\r\n", "\n").replace("\t", "    "))
    return _flow_to_pdf(
        f'<pre style="font-family: {family}; margin: 0;">{body}</pre>', paper=paper, margin=margin, font_size=font_size
    )


def markdown_to_pdf(markdown: str, *, paper: Paper = "a4", margin: float = 54.0, font_size: float = 11.0) -> bytes:
    """Markdown as pages: headings, emphasis, lists, code and tables styled. Raw HTML inside the
    Markdown is not passed through, so a document cannot carry markup of its own into the page."""
    renderer = MarkdownIt("commonmark", {"html": False}).enable("table")
    return _flow_to_pdf(renderer.render(markdown), paper=paper, margin=margin, font_size=font_size)
