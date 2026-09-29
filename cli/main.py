"""The pdfworkerz CLI (COR-10).

Every command here is a thin wrapper around the same engine calls the
future server and UI will use (SPEC.md section 4.2 rule 1: one code path
per capability) -- ``inspect`` and ``render`` go through the registered
:class:`~engine.ops.base.Op` classes so the CLI, recipes and command bar
stay on the exact same execution path once those exist.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Annotated, Any, Literal

import typer

from engine import __version__
from engine.document import Document
from engine.edit import EditResult
from engine.errors import PdfWorkerzError
from engine.fonts import research as font_research
from engine.fonts.choose import available_families
from engine.fonts.style import extract_page_spans
from engine.ops.base import InspectOp, Op, PageSpansOp, RenderPageOp
from engine.ops.images import CropImageOp, DeleteImageOp, InsertImageOp, MoveImageOp, PageImagesOp, ReplaceImageOp
from engine.ops.links import AddLinkOp, PageLinksOp, RemoveLinkOp
from engine.ops.shapes import DeleteShapeOp, DrawShapeOp, EditShapeOp, PageShapesOp
from engine.ops.spellcheck import SpellCheckOp
from engine.ops.text import (
    CopyStyleOp,
    DeleteTextOp,
    InsertTextOp,
    MoveTextBlockOp,
    ReplaceTextOp,
    RestyleTextOp,
    _font_index,
    find_span_index,
)
from server.app import create_app, run_server

app = typer.Typer(add_completion=False, no_args_is_help=True, help="PDFWorkerz: free, offline, token-free PDF editing.")

TierOption = Annotated[
    Literal["exact", "approximate", "fallback"],
    typer.Option("--require-tier", help="Weakest font match to accept without failing."),
]


def _fail(exc: PdfWorkerzError) -> typer.Exit:
    typer.echo(f"error: {exc}", err=True)
    return typer.Exit(code=1)


def _parse_color(value: str) -> tuple[float, float, float]:
    parts = value.split(",")
    if len(parts) != 3:
        raise typer.BadParameter('color must be "r,g,b" with each in 0-1, e.g. "1,0,0" for red')
    try:
        r, g, b = (float(p) for p in parts)
    except ValueError as exc:
        raise typer.BadParameter('color must be "r,g,b" with each a number in 0-1') from exc
    return (r, g, b)


def _parse_point(value: str) -> tuple[float, float]:
    parts = value.split(",")
    if len(parts) != 2:
        raise typer.BadParameter('position must be "x,y", e.g. "72,700"')
    try:
        x, y = (float(p) for p in parts)
    except ValueError as exc:
        raise typer.BadParameter('position must be "x,y" with each a number') from exc
    return (x, y)


def _parse_rect(value: str) -> tuple[float, float, float, float]:
    parts = value.split(",")
    if len(parts) != 4:
        raise typer.BadParameter('rect must be "x0,y0,x1,y1", e.g. "72,700,200,715"')
    try:
        x0, y0, x1, y1 = (float(p) for p in parts)
    except ValueError as exc:
        raise typer.BadParameter('rect must be "x0,y0,x1,y1" with each a number') from exc
    return (x0, y0, x1, y1)


def _one_of(positional: str | None, option: str | None, name: str) -> str:
    """A value given either positionally or as --<name>, but not both.

    The option form exists for text that starts with "-" (e.g. "- End of
    Statement -"), which Click would otherwise parse as an option; the
    positional form keeps working for everything else."""
    if positional is not None and option is not None:
        raise typer.BadParameter(f"give the {name} either positionally or as --{name}, not both")
    value = option if option is not None else positional
    if value is None:
        raise typer.BadParameter(f"missing the {name} (positionally, or as --{name})")
    return value


MatchOption = Annotated[
    str | None, typer.Option("--match", help='Text to find, as an option -- for text starting with "-"')
]
FontOption = Annotated[str | None, typer.Option(help="Font family (see `pdfworkerz fonts`)")]
BoldOption = Annotated[bool | None, typer.Option("--bold/--no-bold", help="Bold on or off; default keeps it")]
ItalicOption = Annotated[bool | None, typer.Option("--italic/--no-italic", help="Italic on or off; default keeps it")]
WholeWordOption = Annotated[
    bool, typer.Option(help='Only match whole words ("cat" matches in "a cat" but not in "category")')
]


def _report(results: list[EditResult]) -> None:
    if not results:
        typer.echo("no matches found; nothing changed")
        return
    for i, result in enumerate(results, start=1):
        flag = " (needs review)" if result.requires_approval else ""
        verified = ""
        if result.verification is not None:
            verified = " [text confirmed]" if result.verification.text_matches else " [WARNING: text not confirmed]"
        typer.echo(f"  {i}. {result.tier} match, confidence {result.confidence:.2f}{flag}{verified}: {result.note}")
    typer.echo(f"{len(results)} edit(s) applied")


def _save(document: Document, path: Path, out: Path | None, overwrite: bool) -> Path:
    result = document.save(path, overwrite=True) if overwrite else document.save(out)
    return result.path


def _run(op: Op, document: Document) -> Any:
    """Apply an Op after the same page-range check the journal and server do, so an
    out-of-range --page is a clear error, not pymupdf's raw IndexError."""
    op.check_pages(document)
    return op.apply(document)


@app.command()
def inspect(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PDF file to inspect")],
    password: Annotated[str | None, typer.Option(help="User password, if the file is encrypted")] = None,
) -> None:
    """Print the document inspection report (COR-03) as JSON."""
    try:
        with Document.open(path, password=password) as document:
            report = _run(InspectOp(password=password or ""), document)
    except PdfWorkerzError as exc:
        raise _fail(exc) from exc
    typer.echo(report.model_dump_json(indent=2))


@app.command()
def render(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PDF file to render")],
    page: Annotated[int, typer.Argument(help="0-based page index")] = 0,
    dpi: Annotated[int, typer.Option(help="Render resolution in DPI")] = 150,
    out: Annotated[Path | None, typer.Option(help="Output PNG path; defaults to <name>.p<page>.png")] = None,
    password: Annotated[str | None, typer.Option(help="User password, if the file is encrypted")] = None,
) -> None:
    """Render one page to a PNG file (COR-02)."""
    try:
        with Document.open(path, password=password) as document:
            png_bytes = _run(RenderPageOp(page_index=page, dpi=dpi), document)
    except PdfWorkerzError as exc:
        raise _fail(exc) from exc
    output = out or path.with_name(f"{path.stem}.p{page}.png")
    output.write_bytes(png_bytes)
    typer.echo(str(output))


@app.command()
def spans(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PDF file to inspect")],
    page: Annotated[int, typer.Argument(help="0-based page index")] = 0,
    password: Annotated[str | None, typer.Option(help="User password, if the file is encrypted")] = None,
) -> None:
    """Print every text span on one page (FNT-01/FNT-02) as JSON -- the same
    data UI-02's click-to-edit overlay and UI-03's inspector panel use."""
    try:
        with Document.open(path, password=password) as document:
            traces = _run(PageSpansOp(page_index=page), document)
    except PdfWorkerzError as exc:
        raise _fail(exc) from exc
    typer.echo(json.dumps([trace.model_dump(mode="json") for trace in traces], indent=2))


@app.command()
def replace(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PDF file to edit")],
    match: Annotated[str | None, typer.Argument(help="Text to find (literal, unless --regex)")] = None,
    replacement: Annotated[str | None, typer.Argument(help="Text to put in its place")] = None,
    match_option: MatchOption = None,
    replacement_option: Annotated[
        str | None, typer.Option("--replacement", help='Replacement text, as an option -- for text starting with "-"')
    ] = None,
    regex: Annotated[bool, typer.Option(help="Treat match as a regular expression")] = False,
    case_insensitive: Annotated[bool, typer.Option(help="Match regardless of case")] = False,
    whole_word: WholeWordOption = False,
    page: Annotated[int | None, typer.Option(help="Only this 0-based page; default is every page")] = None,
    require_tier: TierOption = "approximate",
    fit: Annotated[
        bool, typer.Option(help="Match the original text's width (FNT-10); breaks chaining, see docs")
    ] = False,
    out: Annotated[Path | None, typer.Option(help="Output path; defaults to a new <name>.edited.pdf")] = None,
    overwrite: Annotated[bool, typer.Option(help="Write back to the original file instead")] = False,
    password: Annotated[str | None, typer.Option(help="User password, if the file is encrypted")] = None,
) -> None:
    """Find text (EDT-02) and replace it, matching the original style."""
    if match_option is not None and replacement is None and replacement_option is None:
        match, replacement = (
            None,
            match,
        )  # `replace doc.pdf --match "- x -" "new"`: the lone positional is the replacement
    find = _one_of(match, match_option, "match")
    replace_with = _one_of(replacement, replacement_option, "replacement")
    try:
        with Document.open(path, password=password) as document:
            op = ReplaceTextOp(
                match=find,
                replacement=replace_with,
                mode="regex" if regex else "literal",
                case_sensitive=not case_insensitive,
                whole_word=whole_word,
                page_index=page,
                require_tier=require_tier,
                fit=fit,
            )
            results = _run(op, document)
            saved_to = _save(document, path, out, overwrite)
    except PdfWorkerzError as exc:
        raise _fail(exc) from exc
    _report(results)
    typer.echo(f"saved -> {saved_to}")


@app.command()
def delete(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PDF file to edit")],
    match: Annotated[str | None, typer.Argument(help="Text to find and remove (literal, unless --regex)")] = None,
    match_option: MatchOption = None,
    regex: Annotated[bool, typer.Option(help="Treat match as a regular expression")] = False,
    case_insensitive: Annotated[bool, typer.Option(help="Match regardless of case")] = False,
    whole_word: WholeWordOption = False,
    page: Annotated[int | None, typer.Option(help="Only this 0-based page; default is every page")] = None,
    require_tier: TierOption = "approximate",
    out: Annotated[Path | None, typer.Option(help="Output path; defaults to a new <name>.edited.pdf")] = None,
    overwrite: Annotated[bool, typer.Option(help="Write back to the original file instead")] = False,
    password: Annotated[str | None, typer.Option(help="User password, if the file is encrypted")] = None,
) -> None:
    """Find and remove text (EDT-04), closing the gap in its style-matched span."""
    find = _one_of(match, match_option, "match")
    try:
        with Document.open(path, password=password) as document:
            op = DeleteTextOp(
                match=find,
                mode="regex" if regex else "literal",
                case_sensitive=not case_insensitive,
                whole_word=whole_word,
                page_index=page,
                require_tier=require_tier,
            )
            results = _run(op, document)
            saved_to = _save(document, path, out, overwrite)
    except PdfWorkerzError as exc:
        raise _fail(exc) from exc
    _report(results)
    typer.echo(f"saved -> {saved_to}")


@app.command()
def restyle(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PDF file to edit")],
    match: Annotated[str | None, typer.Argument(help="Text to find (literal, unless --regex)")] = None,
    match_option: MatchOption = None,
    size: Annotated[float | None, typer.Option(help="New font size in points")] = None,
    color: Annotated[str | None, typer.Option(help='New color as "r,g,b", each 0-1, e.g. "1,0,0" for red')] = None,
    font: FontOption = None,
    bold: BoldOption = None,
    italic: ItalicOption = None,
    regex: Annotated[bool, typer.Option(help="Treat match as a regular expression")] = False,
    case_insensitive: Annotated[bool, typer.Option(help="Match regardless of case")] = False,
    page: Annotated[int | None, typer.Option(help="Only this 0-based page; default is every page")] = None,
    require_tier: TierOption = "approximate",
    out: Annotated[Path | None, typer.Option(help="Output path; defaults to a new <name>.edited.pdf")] = None,
    overwrite: Annotated[bool, typer.Option(help="Write back to the original file instead")] = False,
    password: Annotated[str | None, typer.Option(help="User password, if the file is encrypted")] = None,
) -> None:
    """Change the font, weight, slant, size and/or color of matching text (EDT-06), keeping its wording."""
    find = _one_of(match, match_option, "match")
    parsed_color = _parse_color(color) if color is not None else None
    try:
        with Document.open(path, password=password) as document:
            op = RestyleTextOp(
                match=find,
                size=size,
                color=parsed_color,
                font=font,
                bold=bold,
                italic=italic,
                mode="regex" if regex else "literal",
                case_sensitive=not case_insensitive,
                page_index=page,
                require_tier=require_tier,
            )
            results = _run(op, document)
            saved_to = _save(document, path, out, overwrite)
    except PdfWorkerzError as exc:
        raise _fail(exc) from exc
    _report(results)
    typer.echo(f"saved -> {saved_to}")


@app.command()
def insert(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PDF file to edit")],
    text: Annotated[str, typer.Argument(help="Text to add")],
    position: Annotated[str, typer.Option(help='Baseline point to draw at, as "x,y"')],
    reference: Annotated[
        str | None, typer.Option(help="Literal text of the span whose style to copy (or give --font and --size)")
    ] = None,
    size: Annotated[float | None, typer.Option(help="Font size in points")] = None,
    color: Annotated[str | None, typer.Option(help='Color as "r,g,b", each 0-1')] = None,
    font: FontOption = None,
    bold: BoldOption = None,
    italic: ItalicOption = None,
    page: Annotated[int, typer.Option(help="0-based page index")] = 0,
    require_tier: TierOption = "approximate",
    out: Annotated[Path | None, typer.Option(help="Output path; defaults to a new <name>.edited.pdf")] = None,
    overwrite: Annotated[bool, typer.Option(help="Write back to the original file instead")] = False,
    password: Annotated[str | None, typer.Option(help="User password, if the file is encrypted")] = None,
) -> None:
    """Add new text (EDT-03) in the style of an existing span (--reference), in an explicit style
    (--font and --size), or both (the explicit options override). Nothing existing is touched."""
    parsed_position = _parse_point(position)
    try:
        with Document.open(path, password=password) as document:
            op = InsertTextOp(
                page_index=page,
                text=text,
                position=parsed_position,
                reference_match=reference,
                font=font,
                size=size,
                color=_parse_color(color) if color is not None else None,
                bold=bold,
                italic=italic,
                require_tier=require_tier,
            )
            result = _run(op, document)
            saved_to = _save(document, path, out, overwrite)
    except PdfWorkerzError as exc:
        raise _fail(exc) from exc
    _report([result])
    typer.echo(f"saved -> {saved_to}")


@app.command("copy-style")
def copy_style(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PDF file to edit")],
    source: Annotated[str, typer.Option(help="Literal text of the span whose style to copy")],
    target: Annotated[str, typer.Option(help="Literal text of the span to restyle")],
    page: Annotated[int, typer.Option(help="0-based page of the source span")] = 0,
    target_page: Annotated[int | None, typer.Option(help="0-based page of the target span; defaults to --page")] = None,
    require_tier: TierOption = "approximate",
    out: Annotated[Path | None, typer.Option(help="Output path; defaults to a new <name>.edited.pdf")] = None,
    overwrite: Annotated[bool, typer.Option(help="Write back to the original file instead")] = False,
    password: Annotated[str | None, typer.Option(help="User password, if the file is encrypted")] = None,
) -> None:
    """Format painter (EDT-07): give the target text the source text's font, size and color."""
    target_page_index = page if target_page is None else target_page
    try:
        with Document.open(path, password=password) as document:
            op = CopyStyleOp(
                page_index=page,
                span_index=find_span_index(document, page, source),
                target_page_index=target_page_index,
                target_span_index=find_span_index(document, target_page_index, target),
                require_tier=require_tier,
            )
            result = _run(op, document)
            saved_to = _save(document, path, out, overwrite)
    except PdfWorkerzError as exc:
        raise _fail(exc) from exc
    _report([result])
    typer.echo(f"saved -> {saved_to}")


@app.command("move-block")
def move_block(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PDF file to edit")],
    match: Annotated[str, typer.Option(help="Literal text found in any line of the block")],
    dx: Annotated[float, typer.Option(help="Points to move right (negative: left)")] = 0.0,
    dy: Annotated[float, typer.Option(help="Points to move down (negative: up)")] = 0.0,
    width: Annotated[float | None, typer.Option(help="Re-wrap the block to this width, in points")] = None,
    page: Annotated[int, typer.Option(help="0-based page index")] = 0,
    require_tier: TierOption = "approximate",
    out: Annotated[Path | None, typer.Option(help="Output path; defaults to a new <name>.edited.pdf")] = None,
    overwrite: Annotated[bool, typer.Option(help="Write back to the original file instead")] = False,
    password: Annotated[str | None, typer.Option(help="User password, if the file is encrypted")] = None,
) -> None:
    """Move a paragraph and/or re-wrap it to a new width (EDT-05), keeping its text and style."""
    try:
        with Document.open(path, password=password) as document:
            op = MoveTextBlockOp(
                page_index=page,
                span_index=find_span_index(document, page, match),
                dx=dx,
                dy=dy,
                width=width,
                require_tier=require_tier,
            )
            results = _run(op, document)
            saved_to = _save(document, path, out, overwrite)
    except PdfWorkerzError as exc:
        raise _fail(exc) from exc
    _report(results)
    typer.echo(f"saved -> {saved_to}")


@app.command()
def links(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PDF file to inspect")],
    page: Annotated[int, typer.Argument(help="0-based page index")] = 0,
    password: Annotated[str | None, typer.Option(help="User password, if the file is encrypted")] = None,
) -> None:
    """Print every link on one page (EDT-10) as JSON; `index` is what remove-link takes."""
    try:
        with Document.open(path, password=password) as document:
            found = _run(PageLinksOp(page_index=page), document)
    except PdfWorkerzError as exc:
        raise _fail(exc) from exc
    typer.echo(json.dumps([link.model_dump(mode="json") for link in found], indent=2))


@app.command("add-link")
def add_link(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PDF file to edit")],
    uri: Annotated[str | None, typer.Option(help="Link target: an http(s) or mailto URI")] = None,
    to_page: Annotated[int | None, typer.Option(help="Link target: a 0-based page of this document")] = None,
    over: Annotated[str | None, typer.Option(help="Make the text span containing this literal text clickable")] = None,
    rect: Annotated[str | None, typer.Option(help='Or an explicit clickable area, "x0,y0,x1,y1" in points')] = None,
    page: Annotated[int, typer.Option(help="0-based page index")] = 0,
    out: Annotated[Path | None, typer.Option(help="Output path; defaults to a new <name>.edited.pdf")] = None,
    overwrite: Annotated[bool, typer.Option(help="Write back to the original file instead")] = False,
    password: Annotated[str | None, typer.Option(help="User password, if the file is encrypted")] = None,
) -> None:
    """Add a hyperlink (EDT-10) over existing text (--over) or an explicit area (--rect)."""
    if (over is None) == (rect is None):
        raise typer.BadParameter("give exactly one of --over or --rect")
    try:
        with Document.open(path, password=password) as document:
            if rect is not None:
                area = _parse_rect(rect)
            else:
                span_index = find_span_index(document, page, str(over))
                area = extract_page_spans(document.raw, page)[span_index].style.bbox
            added = _run(AddLinkOp(page_index=page, rect=area, uri=uri, target_page=to_page), document)
            saved_to = _save(document, path, out, overwrite)
    except PdfWorkerzError as exc:
        raise _fail(exc) from exc
    typer.echo(f"added link {added.index} on page {page}")
    typer.echo(f"saved -> {saved_to}")


@app.command("remove-link")
def remove_link(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PDF file to edit")],
    index: Annotated[int, typer.Argument(help="The link's index, as `pdfworkerz links` prints it")],
    page: Annotated[int, typer.Option(help="0-based page index")] = 0,
    out: Annotated[Path | None, typer.Option(help="Output path; defaults to a new <name>.edited.pdf")] = None,
    overwrite: Annotated[bool, typer.Option(help="Write back to the original file instead")] = False,
    password: Annotated[str | None, typer.Option(help="User password, if the file is encrypted")] = None,
) -> None:
    """Remove one hyperlink (EDT-10); the text under it is untouched."""
    try:
        with Document.open(path, password=password) as document:
            _run(RemoveLinkOp(page_index=page, index=index), document)
            saved_to = _save(document, path, out, overwrite)
    except PdfWorkerzError as exc:
        raise _fail(exc) from exc
    typer.echo(f"removed link {index} on page {page}")
    typer.echo(f"saved -> {saved_to}")


OutOption = Annotated[Path | None, typer.Option(help="Output path; defaults to a new <name>.edited.pdf")]
OverwriteOption = Annotated[bool, typer.Option(help="Write back to the original file instead")]
PasswordOption = Annotated[str | None, typer.Option(help="User password, if the file is encrypted")]
PageOption = Annotated[int, typer.Option(help="0-based page index")]
ImageFileArg = Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PNG/JPEG (or other) image file")]


def _apply_and_save(path: Path, op: Op, *, out: Path | None, overwrite: bool, password: str | None) -> Path:
    """Open, apply one Op, save -- the shared body of the simpler edit commands."""
    try:
        with Document.open(path, password=password) as document:
            _run(op, document)
            return _save(document, path, out, overwrite)
    except PdfWorkerzError as exc:
        raise _fail(exc) from exc


def _image_b64(image_file: Path) -> str:
    return base64.b64encode(image_file.read_bytes()).decode("ascii")


@app.command()
def images(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PDF file to inspect")],
    page: Annotated[int, typer.Argument(help="0-based page index")] = 0,
    password: PasswordOption = None,
) -> None:
    """Print every image placement on one page (EDT-08) as JSON; `index` is what the image commands take."""
    try:
        with Document.open(path, password=password) as document:
            found = _run(PageImagesOp(page_index=page), document)
    except PdfWorkerzError as exc:
        raise _fail(exc) from exc
    typer.echo(json.dumps([info.model_dump(mode="json") for info in found], indent=2))


@app.command("insert-image")
def insert_image_command(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PDF file to edit")],
    image_file: ImageFileArg,
    rect: Annotated[str, typer.Option(help='Area to place it in, "x0,y0,x1,y1" in points')],
    stretch: Annotated[bool, typer.Option(help="Fill the area exactly instead of keeping proportions")] = False,
    page: PageOption = 0,
    out: OutOption = None,
    overwrite: OverwriteOption = False,
    password: PasswordOption = None,
) -> None:
    """Add an image to a page (EDT-08), fitted inside --rect."""
    op = InsertImageOp(
        page_index=page, rect=_parse_rect(rect), image_base64=_image_b64(image_file), keep_proportion=not stretch
    )
    typer.echo(f"saved -> {_apply_and_save(path, op, out=out, overwrite=overwrite, password=password)}")


@app.command("replace-image")
def replace_image_command(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PDF file to edit")],
    index: Annotated[int, typer.Argument(help="The image's index, as `pdfworkerz images` prints it")],
    image_file: ImageFileArg,
    page: PageOption = 0,
    out: OutOption = None,
    overwrite: OverwriteOption = False,
    password: PasswordOption = None,
) -> None:
    """Swap one image for another, fitted into the same area (EDT-08)."""
    op = ReplaceImageOp(page_index=page, index=index, image_base64=_image_b64(image_file))
    typer.echo(f"saved -> {_apply_and_save(path, op, out=out, overwrite=overwrite, password=password)}")


@app.command("move-image")
def move_image_command(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PDF file to edit")],
    index: Annotated[int, typer.Argument(help="The image's index, as `pdfworkerz images` prints it")],
    rect: Annotated[str, typer.Option(help='New area, "x0,y0,x1,y1" in points')],
    page: PageOption = 0,
    out: OutOption = None,
    overwrite: OverwriteOption = False,
    password: PasswordOption = None,
) -> None:
    """Move and/or resize one image to exactly --rect (EDT-08)."""
    op = MoveImageOp(page_index=page, index=index, rect=_parse_rect(rect))
    typer.echo(f"saved -> {_apply_and_save(path, op, out=out, overwrite=overwrite, password=password)}")


@app.command("crop-image")
def crop_image_command(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PDF file to edit")],
    index: Annotated[int, typer.Argument(help="The image's index, as `pdfworkerz images` prints it")],
    rect: Annotated[str, typer.Option(help='Part to keep, "x0,y0,x1,y1" in page points')],
    page: PageOption = 0,
    out: OutOption = None,
    overwrite: OverwriteOption = False,
    password: PasswordOption = None,
) -> None:
    """Cut one image down to the part inside --rect (EDT-08)."""
    op = CropImageOp(page_index=page, index=index, rect=_parse_rect(rect))
    typer.echo(f"saved -> {_apply_and_save(path, op, out=out, overwrite=overwrite, password=password)}")


@app.command("delete-image")
def delete_image_command(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PDF file to edit")],
    index: Annotated[int, typer.Argument(help="The image's index, as `pdfworkerz images` prints it")],
    page: PageOption = 0,
    out: OutOption = None,
    overwrite: OverwriteOption = False,
    password: PasswordOption = None,
) -> None:
    """Remove one image placement (EDT-08); other uses of the same image stay."""
    op = DeleteImageOp(page_index=page, index=index)
    typer.echo(f"saved -> {_apply_and_save(path, op, out=out, overwrite=overwrite, password=password)}")


def _parse_points(value: str) -> list[tuple[float, float]]:
    return [_parse_point(pair) for pair in value.split()]


@app.command()
def shapes(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PDF file to inspect")],
    page: Annotated[int, typer.Argument(help="0-based page index")] = 0,
    password: PasswordOption = None,
) -> None:
    """Print every vector path on one page (EDT-09) as JSON; `index` is what the shape commands take."""
    try:
        with Document.open(path, password=password) as document:
            found = _run(PageShapesOp(page_index=page), document)
    except PdfWorkerzError as exc:
        raise _fail(exc) from exc
    typer.echo(json.dumps([info.model_dump(mode="json") for info in found], indent=2))


@app.command("draw-shape")
def draw_shape_command(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PDF file to edit")],
    kind: Annotated[Literal["line", "rect", "ellipse", "polyline", "polygon"], typer.Argument(help="What to draw")],
    points: Annotated[str, typer.Option(help='Space-separated "x,y" points, e.g. "72,700 200,760"')],
    stroke: Annotated[str | None, typer.Option(help='Outline color "r,g,b" (0-1); default black')] = None,
    no_stroke: Annotated[bool, typer.Option(help="No outline (needs --fill)")] = False,
    fill: Annotated[str | None, typer.Option(help='Fill color "r,g,b" (0-1)')] = None,
    width: Annotated[float, typer.Option(help="Outline width in points")] = 1.0,
    dashed: Annotated[bool, typer.Option(help="Dashed outline")] = False,
    page: PageOption = 0,
    out: OutOption = None,
    overwrite: OverwriteOption = False,
    password: PasswordOption = None,
) -> None:
    """Draw a line, rectangle, ellipse, polyline or polygon (EDT-09)."""
    stroke_color = None if no_stroke else (_parse_color(stroke) if stroke else (0.0, 0.0, 0.0))
    op = DrawShapeOp(
        page_index=page,
        kind=kind,
        points=_parse_points(points),
        stroke_color=stroke_color,
        fill_color=_parse_color(fill) if fill else None,
        line_width=width,
        dashed=dashed,
    )
    typer.echo(f"saved -> {_apply_and_save(path, op, out=out, overwrite=overwrite, password=password)}")


@app.command("edit-shape")
def edit_shape_command(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PDF file to edit")],
    index: Annotated[int, typer.Argument(help="The shape's index, as `pdfworkerz shapes` prints it")],
    rect: Annotated[str | None, typer.Option(help='New bounding box "x0,y0,x1,y1" (moves/resizes)')] = None,
    stroke: Annotated[str | None, typer.Option(help='New outline color "r,g,b" (0-1)')] = None,
    fill: Annotated[str | None, typer.Option(help='New fill color "r,g,b" (0-1)')] = None,
    no_fill: Annotated[bool, typer.Option(help="Remove the fill")] = False,
    width: Annotated[float | None, typer.Option(help="New outline width in points")] = None,
    page: PageOption = 0,
    out: OutOption = None,
    overwrite: OverwriteOption = False,
    password: PasswordOption = None,
) -> None:
    """Move, resize or restyle one existing shape (EDT-09)."""
    op = EditShapeOp(
        page_index=page,
        index=index,
        rect=_parse_rect(rect) if rect else None,
        stroke_color=_parse_color(stroke) if stroke else None,
        fill_color=_parse_color(fill) if fill else None,
        no_fill=no_fill,
        line_width=width,
    )
    typer.echo(f"saved -> {_apply_and_save(path, op, out=out, overwrite=overwrite, password=password)}")


@app.command("delete-shape")
def delete_shape_command(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PDF file to edit")],
    index: Annotated[int, typer.Argument(help="The shape's index, as `pdfworkerz shapes` prints it")],
    page: PageOption = 0,
    out: OutOption = None,
    overwrite: OverwriteOption = False,
    password: PasswordOption = None,
) -> None:
    """Remove one vector path (EDT-09); text, images and other shapes stay."""
    op = DeleteShapeOp(page_index=page, index=index)
    typer.echo(f"saved -> {_apply_and_save(path, op, out=out, overwrite=overwrite, password=password)}")


@app.command()
def spellcheck(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PDF file to check")],
    page: Annotated[int | None, typer.Option(help="Only this 0-based page; default is every page")] = None,
    language: Annotated[str, typer.Option(help="Dictionary to check against (assets/dictionaries)")] = "en_US",
    ignore: Annotated[list[str] | None, typer.Option(help="A word to accept anyway (repeatable)")] = None,
    password: PasswordOption = None,
) -> None:
    """List misspelled words (EDT-11), offline, with suggestions. Fix one with `replace`."""
    try:
        with Document.open(path, password=password) as document:
            pages = range(document.page_count) if page is None else [page]
            total = 0
            for page_index in pages:
                found = _run(SpellCheckOp(page_index=page_index, language=language, ignore=ignore or []), document)
                for miss in found:
                    hint = f" -> {', '.join(miss.suggestions)}" if miss.suggestions else ""
                    typer.echo(f"page {page_index}: {miss.word}{hint}")
                total += len(found)
    except PdfWorkerzError as exc:
        raise _fail(exc) from exc
    typer.echo(f"{total} possible misspelling(s)")


@app.command()
def repair(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PDF file to repair")],
    out: Annotated[Path, typer.Option(help="Where to write the repaired, clean copy")],
    password: Annotated[str | None, typer.Option(help="User password, if the file is encrypted")] = None,
) -> None:
    """Open a damaged PDF, repairing it automatically, and save a clean copy (OPT-06)."""
    try:
        with Document.open(path, password=password) as document:
            was_repaired = document.is_repaired
            document.save(out, overwrite=True, mode="full")
    except PdfWorkerzError as exc:
        raise _fail(exc) from exc
    verb = "repaired and saved" if was_repaired else "no repair was needed; saved a clean copy"
    typer.echo(f"{verb} -> {out}")


@app.command()
def serve(
    port: Annotated[int, typer.Option(help="TCP port to listen on")] = 8000,
) -> None:
    """Start the local HTTP API server (COR-11) that the web UI talks to.

    Binds to 127.0.0.1 only (SPEC.md section 4.2 rule 5) and prints a
    random session token that every request must send back in the
    X-Session-Token header.
    """
    server_app = create_app()
    typer.echo(f"PDFWorkerz server starting at http://127.0.0.1:{port}")
    typer.echo(f"Session token: {server_app.state.session_token}")
    run_server(server_app, port=port)


@app.command()
def version() -> None:
    """Print the installed PDFWorkerz engine version."""
    typer.echo(__version__)


if __name__ == "__main__":
    app()


@app.command()
def fonts(
    research: Annotated[
        bool, typer.Option("--research", help="Show fonts that edits could only approximate, to add later")
    ] = False,
    clear_research: Annotated[bool, typer.Option("--clear-research", help="Empty the fonts-to-research list")] = False,
) -> None:
    """List the font families you can choose (EDT-03/EDT-06), or the fonts to research."""
    if clear_research:
        font_research.clear()
        typer.echo("fonts-to-research list cleared")
        return
    if research:
        rows = font_research.load()
        if not rows:
            typer.echo("no fonts to research yet")
        for row in rows:
            typer.echo(
                f"{row.font}  ({row.best_tier}, seen {row.times_seen}x, e.g. {row.example_document}): {row.note}"
            )
        typer.echo(f"list file: {font_research.research_file()}")
        return
    for family in available_families(_font_index()):
        typer.echo(family)
