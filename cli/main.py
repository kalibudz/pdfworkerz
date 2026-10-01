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
import os
from pathlib import Path
from typing import Annotated, Any, Literal

import typer

from engine import __version__
from engine.commands import CommandError, parse_command
from engine.document import Document
from engine.edit import EditResult
from engine.errors import PdfWorkerzError
from engine.fonts import library as font_library
from engine.fonts import research as font_research
from engine.fonts.choose import available_families
from engine.fonts.style import extract_page_spans
from engine.ops.base import InspectOp, Op, PageSpansOp, RenderPageOp, parse_op
from engine.ops.certs import GenerateCertificateOp
from engine.ops.forms import (
    CreateDetectedFieldsOp,
    CreateFieldOp,
    DeleteFieldOp,
    DetectFormFieldsOp,
    DetectXFAOp,
    EditFieldOp,
    ExportFormDataOp,
    FillFieldsOp,
    FlattenFormOp,
    ImportFormDataOp,
    PageFieldsOp,
    SetTabOrderOp,
)
from engine.ops.images import CropImageOp, DeleteImageOp, InsertImageOp, MoveImageOp, PageImagesOp, ReplaceImageOp
from engine.ops.links import AddLinkOp, PageLinksOp, RemoveLinkOp
from engine.ops.protect import RemovePasswordOp, SetPasswordOp, SetPermissionsOp
from engine.ops.redact import FindRedactionCandidatesOp, RedactAreasOp, SanitizeOp
from engine.ops.shapes import DeleteShapeOp, DrawShapeOp, EditShapeOp, PageShapesOp
from engine.ops.sign import SignDocumentOp, ValidateSignaturesOp
from engine.ops.signatures import PlaceSignatureOp
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
from engine.preview import preview_steps
from engine.recipes import dump_recipe, load_recipe
from server.app import create_app, run_server

app = typer.Typer(add_completion=False, no_args_is_help=True, help="PDFWorkerz: free, offline, token-free PDF editing.")
forms_app = typer.Typer(add_completion=False, no_args_is_help=True, help="AcroForm fields (FRM-01/02/05/06).")
app.add_typer(forms_app, name="forms")

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


@app.command()
def fonts(
    research: Annotated[
        bool, typer.Option("--research", help="Show fonts that edits could only approximate, to add later")
    ] = False,
    clear_research: Annotated[bool, typer.Option("--clear-research", help="Empty the fonts-to-research list")] = False,
    library: Annotated[bool, typer.Option("--library", help="Show the fonts you added to your font library")] = False,
    add: Annotated[
        Path | None, typer.Option("--add", help="Add a .ttf/.otf font file to your font library", metavar="FILE")
    ] = None,
    harvest: Annotated[
        tuple[Path, str] | None,
        typer.Option(
            "--harvest",
            help="Add the complete font a PDF embeds to your font library: --harvest DOC.pdf FONTNAME",
            metavar="DOC FONT",
        ),
    ] = None,
) -> None:
    """List the font families you can choose (EDT-03/EDT-06), the fonts to research, or
    your own font library -- and add fonts to it."""
    try:
        if add is not None:
            entry = font_library.add_font_file(add)
            typer.echo(
                f"added {entry.postscript_name} ({entry.family} {entry.style}) to {font_library.user_fonts_dir()}"
            )
            return
        if harvest is not None:
            path, name = harvest
            with Document.open(path) as document:
                entry = font_library.harvest_from_document(document, name)
            typer.echo(f"added {entry.postscript_name} ({entry.family} {entry.style}), {entry.source}")
            typer.echo(f"library folder: {font_library.user_fonts_dir()}")
            return
    except PdfWorkerzError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if library:
        entries = font_library.list_fonts()
        if not entries:
            typer.echo("your font library is empty")
        for entry in entries:
            typer.echo(f"{entry.postscript_name}  ({entry.family} {entry.style}; from {entry.source})")
        typer.echo(f"library folder: {font_library.user_fonts_dir()}")
        return
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


def _apply_steps(
    path: Path,
    steps: list[tuple[str, dict[str, Any]]],
    *,
    dry_run: bool,
    out: Path | None,
    overwrite: bool,
    password: str | None,
    save_recipe: Path | None = None,
) -> None:
    """Shared by `edit` and `run`: apply (label, op) steps in order, or preview them."""
    try:
        with Document.open(path, password=password) as document:
            applied: list[Op] = []
            if dry_run:
                # Counted on a scratch copy with each earlier step applied (engine.preview).
                preview = preview_steps(document, [data for _label, data in steps])
                for (label, _data), part in zip(steps, preview.ops, strict=False):
                    where = f" on page(s) {', '.join(map(str, part.pages))}" if part.pages else ""
                    typer.echo(
                        f"{label}: "
                        + (f"{part.matches} match(es){where}" if part.matches is not None else "will be applied")
                    )
                for warning in preview.warnings:
                    typer.echo(f"  warning: {warning}")
                typer.echo("dry run: nothing was changed or saved")
                return
            for label, data in steps:
                op = parse_op(data)
                _run(op, document)
                applied.append(op)
                typer.echo(f"done: {label}")
            if save_recipe is not None:
                save_recipe.write_text(dump_recipe(applied, name=path.stem), encoding="utf-8")
                typer.echo(f"recipe -> {save_recipe}")
            result = (
                document.save(path, overwrite=True, deterministic=True)
                if overwrite
                else document.save(out, deterministic=True)
            )
    except PdfWorkerzError as exc:
        raise _fail(exc) from exc
    typer.echo(f"saved -> {result.path}")


@app.command()
def edit(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PDF file to edit")],
    do: Annotated[
        list[str] | None, typer.Option("--do", help='A command, e.g. --do \'replace "2024" with "2025"\' (repeatable)')
    ] = None,
    recipe: Annotated[
        Path | None, typer.Option(exists=True, dir_okay=False, help="A recipe (YAML/JSON) to apply after --do")
    ] = None,
    page: Annotated[int, typer.Option(help="1-based page that 'insert' uses when a command names none")] = 1,
    dry_run: Annotated[bool, typer.Option(help="Show what would change, without changing or saving")] = False,
    save_recipe: Annotated[
        Path | None, typer.Option(help="Also write the applied steps as a recipe to this file")
    ] = None,
    out: Annotated[Path | None, typer.Option(help="Output path; defaults to a new <name>.edited.pdf")] = None,
    overwrite: Annotated[bool, typer.Option(help="Write back to the original file instead")] = False,
    password: Annotated[str | None, typer.Option(help="User password, if the file is encrypted")] = None,
) -> None:
    """Edit with typed commands (CMD-08) and/or a recipe. Commands never guess: one that
    doesn't parse is refused with suggestions, and nothing is changed."""
    if not do and recipe is None:
        raise typer.BadParameter("give at least one --do command or a --recipe")
    steps: list[tuple[str, dict[str, Any]]] = []
    try:
        with Document.open(path, password=password) as probe:
            page_count = probe.page_count
        for command in do or []:
            plan = parse_command(command, page_count=page_count, current_page=page - 1)
            if plan.special:
                raise CommandError(f"'{plan.special}' only makes sense in an open editing session, not here")
            steps.append((plan.description, plan.op()))
        if recipe is not None:
            loaded = load_recipe(recipe.read_text(encoding="utf-8"))
            steps.extend((f"recipe {loaded.name}, step {i}", op) for i, op in enumerate(loaded.ops, start=1))
    except CommandError as exc:
        typer.echo(f"error: {exc}", err=True)
        for suggestion in exc.suggestions:
            typer.echo(f"  did you mean: {suggestion}", err=True)
        typer.echo(f"  {exc.hint}", err=True)
        raise typer.Exit(code=2) from exc
    except PdfWorkerzError as exc:
        raise _fail(exc) from exc
    _apply_steps(path, steps, dry_run=dry_run, out=out, overwrite=overwrite, password=password, save_recipe=save_recipe)


@app.command()
def run(
    recipe: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="Recipe file (YAML or JSON)")],
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PDF file to apply it to")],
    dry_run: Annotated[bool, typer.Option(help="Show what would change, without changing or saving")] = False,
    out: Annotated[Path | None, typer.Option(help="Output path; defaults to a new <name>.edited.pdf")] = None,
    overwrite: Annotated[bool, typer.Option(help="Write back to the original file instead")] = False,
    password: Annotated[str | None, typer.Option(help="User password, if the file is encrypted")] = None,
) -> None:
    """Replay a recipe (CMD-07/CMD-08). Output is byte-identical for the same recipe and
    input (unencrypted files; AES re-encrypts with fresh random IVs each save)."""
    try:
        loaded = load_recipe(recipe.read_text(encoding="utf-8"))
    except PdfWorkerzError as exc:
        raise _fail(exc) from exc
    steps = [(f"step {i}: {op['op']}", op) for i, op in enumerate(loaded.ops, start=1)]
    _apply_steps(path, steps, dry_run=dry_run, out=out, overwrite=overwrite, password=password)


@app.command()
def protect(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PDF file to protect")],
    user_password: Annotated[
        str | None, typer.Option("--user-password", help="Password required to open the file")
    ] = None,
    owner_password: Annotated[
        str | None, typer.Option("--owner-password", help="Password required to change permissions")
    ] = None,
    no_print: Annotated[bool, typer.Option("--no-print", help="Deny printing")] = False,
    no_copy: Annotated[bool, typer.Option("--no-copy", help="Deny copying/extracting text")] = False,
    no_modify: Annotated[bool, typer.Option("--no-modify", help="Deny editing the document")] = False,
    no_annotate: Annotated[bool, typer.Option("--no-annotate", help="Deny annotations and form filling")] = False,
    out: OutOption = None,
    overwrite: OverwriteOption = False,
    password: PasswordOption = None,
) -> None:
    """Add a password and encrypt with AES-256 (SEC-04). Give --user-password,
    --owner-password, or both; at least one is required."""
    op = SetPasswordOp(
        user_password=user_password,
        owner_password=owner_password,
        allow_print=not no_print,
        allow_copy=not no_copy,
        allow_modify=not no_modify,
        allow_annotate=not no_annotate,
        path=str(out) if out is not None else None,
        overwrite=overwrite,
    )
    try:
        with Document.open(path, password=password) as document:
            result = _run(op, document)
    except PdfWorkerzError as exc:
        raise _fail(exc) from exc
    typer.echo(f"saved -> {result.path}")


@app.command()
def unlock(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PDF file to unlock")],
    password: Annotated[
        str | None, typer.Option(help="The user or owner password that currently unlocks this file")
    ] = None,
    out: OutOption = None,
    overwrite: OverwriteOption = False,
) -> None:
    """Remove encryption, given the correct password (SEC-05). A wrong password is
    refused before anything is touched; the file is left exactly as it was."""
    op = RemovePasswordOp(path=str(out) if out is not None else None, overwrite=overwrite)
    try:
        with Document.open(path, password=password) as document:
            result = _run(op, document)
    except PdfWorkerzError as exc:
        raise _fail(exc) from exc
    typer.echo(f"saved -> {result.path}")


@app.command("set-permissions")
def set_permissions_command(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PDF file to restrict")],
    owner_password: Annotated[
        str, typer.Option("--owner-password", help="Required: the permissions can't be bypassed without this")
    ],
    user_password: Annotated[
        str | None, typer.Option("--user-password", help="Password required to open the file; default keeps it")
    ] = None,
    no_print: Annotated[bool, typer.Option("--no-print", help="Deny printing")] = False,
    no_copy: Annotated[bool, typer.Option("--no-copy", help="Deny copying/extracting text")] = False,
    no_modify: Annotated[bool, typer.Option("--no-modify", help="Deny editing the document")] = False,
    no_annotate: Annotated[bool, typer.Option("--no-annotate", help="Deny annotations and form filling")] = False,
    out: OutOption = None,
    overwrite: OverwriteOption = False,
    password: PasswordOption = None,
) -> None:
    """Set print/copy/modify/annotate permissions independently (SEC-06).
    Requires --owner-password, so the restriction can't be removed by anyone
    who simply opens the file with no password."""
    op = SetPermissionsOp(
        owner_password=owner_password,
        user_password=user_password,
        allow_print=not no_print,
        allow_copy=not no_copy,
        allow_modify=not no_modify,
        allow_annotate=not no_annotate,
        path=str(out) if out is not None else None,
        overwrite=overwrite,
    )
    try:
        with Document.open(path, password=password) as document:
            result = _run(op, document)
    except PdfWorkerzError as exc:
        raise _fail(exc) from exc
    typer.echo(f"saved -> {result.path}")


@app.command("sign-place")
def sign_place_command(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PDF file to edit")],
    rect: Annotated[str, typer.Option(help='Area to place it in, "x0,y0,x1,y1" in points')],
    kind: Annotated[Literal["drawn", "typed", "image"], typer.Option(help="drawn, typed, or image")],
    page: PageOption = 0,
    text: Annotated[str | None, typer.Option(help='Signature text, for --kind typed (e.g. "Jane Doe")')] = None,
    font: Annotated[
        str | None,
        typer.Option(help="Reserved for a future font choice; typed signatures use the bundled script font today"),
    ] = None,
    image: Annotated[
        Path | None, typer.Option(exists=True, dir_okay=False, help="PNG/JPEG (or other) image file, for --kind image")
    ] = None,
    strokes_json: Annotated[
        Path | None,
        typer.Option(
            "--strokes-json",
            exists=True,
            dir_okay=False,
            help="JSON file: a list of strokes, each a list of [x, y] points, for --kind drawn",
        ),
    ] = None,
    out: OutOption = None,
    overwrite: OverwriteOption = False,
    password: PasswordOption = None,
) -> None:
    """Place a drawn, typed or image signature as ordinary page content (SIG-01) --
    never an AcroForm field. Give exactly one of --strokes-json (drawn),
    --text (typed) or --image, matching --kind."""
    strokes = None
    if strokes_json is not None:
        try:
            strokes = json.loads(strokes_json.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise typer.BadParameter(f"{strokes_json} is not valid JSON: {exc}") from exc
    op = PlaceSignatureOp(
        page_index=page,
        rect=_parse_rect(rect),
        kind=kind,
        strokes=strokes,
        text=text,
        font=font,
        image_base64=_image_b64(image) if image is not None else None,
    )
    typer.echo(f"saved -> {_apply_and_save(path, op, out=out, overwrite=overwrite, password=password)}")


@app.command("cert-generate")
def cert_generate_command(
    common_name: Annotated[str, typer.Option("--common-name", help="The certificate's subject/issuer common name")],
    out_dir: Annotated[
        Path, typer.Option("--out-dir", file_okay=False, help="Directory to write cert.pem/key.pem/bundle.p12 into")
    ],
    key_size: Annotated[int, typer.Option(help="RSA key size in bits; 2048 or more")] = 2048,
    passphrase: Annotated[
        str | None,
        typer.Option(help="Encrypts the private key (both key.pem and bundle.p12); strongly recommended"),
    ] = None,
) -> None:
    """Generate a local self-signed certificate and private key (SIG-06), with
    no network call, and write cert.pem, key.pem and bundle.p12 into --out-dir.
    Writing the files -- and with what permissions -- is this command's own
    responsibility: engine.certs itself only returns bytes. On Windows, a
    POSIX file-mode bit is not a meaningful protection (there's no POSIX mode
    to restrict to begin with), so --passphrase is the protection that
    actually travels with the key; this command still asks the OS for owner-only
    access via os.open's mode argument, for whatever it's worth on this
    filesystem, but does not rely on it."""
    try:
        op = GenerateCertificateOp(common_name=common_name, key_size=key_size, passphrase=passphrase)
        result = op.apply(None)
    except PdfWorkerzError as exc:
        raise _fail(exc) from exc
    out_dir.mkdir(parents=True, exist_ok=True)
    cert_path, key_path, bundle_path = out_dir / "cert.pem", out_dir / "key.pem", out_dir / "bundle.p12"
    for file_path, data, mode in (
        (cert_path, result.cert_pem.encode("ascii"), 0o644),  # a certificate is public; the key and bundle are not
        (key_path, result.key_pem.encode("ascii"), 0o600),
        (bundle_path, base64.b64decode(result.pkcs12_base64), 0o600),
    ):
        fd = os.open(file_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
    typer.echo(f"saved -> {cert_path}, {key_path}, {bundle_path}")
    if passphrase is None:
        typer.echo(
            "warning: no --passphrase given; the private key is unencrypted on disk "
            "(file permissions are the only protection, and Windows doesn't honor POSIX mode bits)",
            err=True,
        )


@app.command("sign-document")
def sign_document_command(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PDF file to sign")],
    cert: Annotated[Path, typer.Option(exists=True, dir_okay=False, help="Signer certificate, PEM")],
    key: Annotated[Path, typer.Option(exists=True, dir_okay=False, help="Signer private key, PEM")],
    key_passphrase: Annotated[str | None, typer.Option(help="Passphrase for --key, if it's encrypted")] = None,
    field_name: Annotated[str | None, typer.Option("--field-name", help="Existing or new signature field name")] = None,
    reason: Annotated[str | None, typer.Option(help="Reason for signing, embedded in the signature")] = None,
    location: Annotated[str | None, typer.Option(help="Location, embedded in the signature")] = None,
    tsa_url: Annotated[str | None, typer.Option("--tsa-url", help="RFC 3161 timestamp authority URL (SIG-04)")] = None,
    out: Annotated[
        Path | None, typer.Option(help="Write the signed file here instead of signing --path in place")
    ] = None,
    overwrite: Annotated[
        bool, typer.Option(help="Sign the file in place (the default; rejected together with --out)")
    ] = False,
    password: PasswordOption = None,
) -> None:
    """Apply a real PAdES digital signature (SIG-02), optionally timestamped by
    an RFC 3161 TSA (SIG-04). Unlike the other edit commands, this signs the
    file *in place* by default (an incremental update naturally appends to
    the same file); pass --out to write the signed result elsewhere instead,
    leaving `path` untouched."""
    if out is not None and overwrite:
        raise typer.BadParameter("pass either --out or --overwrite, not both")
    try:
        with Document.open(path, password=password) as document:
            op = SignDocumentOp(
                cert_pem=cert.read_text(encoding="ascii"),
                key_pem=key.read_text(encoding="ascii"),
                key_passphrase=key_passphrase,
                field_name=field_name,
                reason=reason,
                location=location,
                tsa_url=tsa_url,
                path=str(out) if out is not None else None,
            )
            result = _run(op, document)
    except PdfWorkerzError as exc:
        raise _fail(exc) from exc
    typer.echo(f"signed -> {result.path} (field {result.field_name!r}, PAdES {result.pades_level})")


@app.command("validate-signatures")
def validate_signatures_command(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PDF file to check")],
    password: PasswordOption = None,
) -> None:
    """Print SIG-03's validation report -- one entry per embedded signature -- as JSON."""
    try:
        with Document.open(path, password=password) as document:
            reports = _run(ValidateSignaturesOp(), document)
    except PdfWorkerzError as exc:
        raise _fail(exc) from exc
    typer.echo(json.dumps([report.model_dump(mode="json") for report in reports], indent=2))


def _parse_field_value(raw: str) -> str | bool:
    if raw.lower() in ("true", "false"):
        return raw.lower() == "true"
    return raw


def _parse_field_set(value: str) -> tuple[str, str | bool]:
    if "=" not in value:
        raise typer.BadParameter('--set must be "name=value", e.g. --set agree=true')
    name, _, raw_value = value.partition("=")
    return name, _parse_field_value(raw_value)


@forms_app.command("list")
def forms_list(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PDF file to inspect")],
    page: PageOption = 0,
    password: PasswordOption = None,
) -> None:
    """FRM-01: list every AcroForm field on one page (a radio group counts as one field)."""
    try:
        with Document.open(path, password=password) as document:
            fields = _run(PageFieldsOp(page_index=page), document)
    except PdfWorkerzError as exc:
        raise _fail(exc) from exc
    if not fields:
        typer.echo("no fields on this page")
        return
    for field in fields:
        options = f" options={field.options}" if field.options else ""
        typer.echo(f"{field.name!r}: {field.field_type} value={field.value!r} rect={field.rect}{options}")


@forms_app.command("fill")
def forms_fill(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PDF file to fill")],
    page: PageOption = 0,
    set_: Annotated[
        list[str],
        typer.Option("--set", help='A field to set, "name=value" (repeatable); "true"/"false" for checkboxes'),
    ] = [],  # noqa: B006 -- typer reads this as the option's default, never mutated
    out: OutOption = None,
    overwrite: OverwriteOption = False,
    password: PasswordOption = None,
) -> None:
    """FRM-02: fill one or more fields on one page in a single undo step. An
    unknown field name, or a value that isn't one of a dropdown's/radio's
    options, refuses the whole call."""
    if not set_:
        raise typer.BadParameter("give at least one --set name=value")
    values = dict(_parse_field_set(item) for item in set_)
    try:
        with Document.open(path, password=password) as document:
            _run(FillFieldsOp(page_index=page, values=values), document)
            saved_to = _save(document, path, out, overwrite)
    except PdfWorkerzError as exc:
        raise _fail(exc) from exc
    typer.echo(f"filled {len(values)} field(s) on page {page}")
    typer.echo(f"saved -> {saved_to}")


@forms_app.command("tab-order")
def forms_tab_order(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PDF file to reorder")],
    page: PageOption = 0,
    order: Annotated[str, typer.Option(help="Comma-separated field names, in the desired tab order")] = "",
    out: OutOption = None,
    overwrite: OverwriteOption = False,
    password: PasswordOption = None,
) -> None:
    """FRM-05: set the tab order of one page's fields. A field left out of
    --order is placed after every named field, in its current relative order."""
    if not order:
        raise typer.BadParameter("give --order as a comma-separated list of field names")
    field_names = [name.strip() for name in order.split(",") if name.strip()]
    try:
        with Document.open(path, password=password) as document:
            _run(SetTabOrderOp(page_index=page, field_names=field_names), document)
            saved_to = _save(document, path, out, overwrite)
    except PdfWorkerzError as exc:
        raise _fail(exc) from exc
    typer.echo(f"tab order set on page {page}")
    typer.echo(f"saved -> {saved_to}")


@forms_app.command("flatten")
def forms_flatten(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PDF file to flatten")],
    page: Annotated[int | None, typer.Option(help="0-based page index; omit to flatten the whole document")] = None,
    out: OutOption = None,
    overwrite: OverwriteOption = False,
    password: PasswordOption = None,
) -> None:
    """FRM-06: draw every field's current appearance into static page content and
    drop the interactive AcroForm. A document with no AcroForm is left unchanged."""
    try:
        with Document.open(path, password=password) as document:
            result = _run(FlattenFormOp(page_index=page), document)
            saved_to = _save(document, path, out, overwrite)
    except PdfWorkerzError as exc:
        raise _fail(exc) from exc
    typer.echo(f"flattened {result.fields_flattened} field(s)")
    typer.echo(f"saved -> {saved_to}")


@forms_app.command("create")
def forms_create(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PDF file to add a field to")],
    field_type: Annotated[str, typer.Option(help="text|checkbox|radio|dropdown|listbox|signature")],
    name: Annotated[str, typer.Option(help="The new field's name (must be unique on this page)")],
    page: PageOption = 0,
    rect: Annotated[
        str | None, typer.Option(help='"x0,y0,x1,y1"; not used for radio (give --rect-for instead)')
    ] = None,
    option: Annotated[list[str], typer.Option("--option", help="A choice/radio option (repeatable, in order)")] = [],  # noqa: B006 -- typer reads this as the option's default, never mutated
    rect_for: Annotated[
        list[str],
        typer.Option("--rect-for", help='A radio option\'s own "x0,y0,x1,y1" (repeatable, same order as --option)'),
    ] = [],  # noqa: B006 -- typer reads this as the option's default, never mutated
    value: Annotated[str | None, typer.Option(help='Initial value; "true"/"false" for a checkbox')] = None,
    font: Annotated[str, typer.Option(help="Text field font: one of Cour/TiRo/Helv/ZaDb")] = "Helv",
    size: Annotated[float, typer.Option(help="Text field font size; 0 lets the viewer auto-size it")] = 0.0,
    multiline: Annotated[bool, typer.Option(help="Text field: allow multiple lines")] = False,
    out: OutOption = None,
    overwrite: OverwriteOption = False,
    password: PasswordOption = None,
) -> None:
    """FRM-03: add a new field to one page. A radio field is a GROUP: give --option and
    --rect-for (one each per button, same order); every other type's own position is
    --rect."""
    parsed_value: str | bool | None = _parse_field_value(value) if value is not None else None
    op = CreateFieldOp(
        page_index=page,
        field_type=field_type,  # type: ignore[arg-type]
        name=name,
        rect=_parse_rect(rect) if rect else None,
        options=list(option) or None,
        rects=[_parse_rect(r) for r in rect_for] or None,
        value=parsed_value,
        font=font,
        size=size,
        multiline=multiline,
    )
    try:
        with Document.open(path, password=password) as document:
            field = _run(op, document)
            saved_to = _save(document, path, out, overwrite)
    except PdfWorkerzError as exc:
        raise _fail(exc) from exc
    typer.echo(f"created {field.field_type} field {field.name!r} on page {page}")
    typer.echo(f"saved -> {saved_to}")


@forms_app.command("edit")
def forms_edit(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PDF file to edit a field in")],
    name: Annotated[str, typer.Option(help="The field to edit")],
    page: PageOption = 0,
    option: Annotated[
        list[str], typer.Option("--option", help="New choice/radio options (repeatable, replaces the old ones)")
    ] = [],  # noqa: B006 -- typer reads this as the option's default, never mutated
    rect_for: Annotated[
        list[str], typer.Option("--rect-for", help="New radio option rects (repeatable, with --option)")
    ] = [],  # noqa: B006 -- typer reads this as the option's default, never mutated
    rect: Annotated[str | None, typer.Option(help='New "x0,y0,x1,y1" for a non-radio field')] = None,
    font: Annotated[str | None, typer.Option(help="New text field font")] = None,
    size: Annotated[float | None, typer.Option(help="New text field font size")] = None,
    multiline: Annotated[bool | None, typer.Option(help="New text field multiline flag")] = None,
    out: OutOption = None,
    overwrite: OverwriteOption = False,
    password: PasswordOption = None,
) -> None:
    """FRM-03: change an existing field's type-specific properties (e.g. a dropdown's
    options, or a text field's font/size/multiline); only the options actually given
    are changed."""
    op = EditFieldOp(
        page_index=page,
        name=name,
        font=font,
        size=size,
        multiline=multiline,
        options=list(option) or None,
        rect=_parse_rect(rect) if rect else None,
        rects=[_parse_rect(r) for r in rect_for] or None,
    )
    try:
        with Document.open(path, password=password) as document:
            field = _run(op, document)
            saved_to = _save(document, path, out, overwrite)
    except PdfWorkerzError as exc:
        raise _fail(exc) from exc
    typer.echo(f"edited field {field.name!r} on page {page}")
    typer.echo(f"saved -> {saved_to}")


@forms_app.command("delete")
def forms_delete(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PDF file to delete a field from")],
    name: Annotated[str, typer.Option(help="The field to delete")],
    page: PageOption = 0,
    out: OutOption = None,
    overwrite: OverwriteOption = False,
    password: PasswordOption = None,
) -> None:
    """FRM-03: remove a field from both the page's annotations and the AcroForm's
    field tree (a radio group's every button together)."""
    try:
        with Document.open(path, password=password) as document:
            _run(DeleteFieldOp(page_index=page, name=name), document)
            saved_to = _save(document, path, out, overwrite)
    except PdfWorkerzError as exc:
        raise _fail(exc) from exc
    typer.echo(f"deleted field {name!r} from page {page}")
    typer.echo(f"saved -> {saved_to}")


@forms_app.command("export")
def forms_export(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PDF file to read field data from")],
    out: Annotated[Path, typer.Option(help="File to write the exported field data to")],
    format: Annotated[str, typer.Option(help="fdf|xfdf|json|csv")] = "json",
    page: PageOption = 0,
    password: PasswordOption = None,
) -> None:
    """FRM-07: every field on one page -- name and current value -- as FDF/XFDF/JSON/CSV."""
    try:
        with Document.open(path, password=password) as document:
            data = _run(ExportFormDataOp(page_index=page, format=format), document)  # type: ignore[arg-type]
    except PdfWorkerzError as exc:
        raise _fail(exc) from exc
    out.write_bytes(data)
    typer.echo(f"exported -> {out}")


@forms_app.command("import")
def forms_import(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PDF file to fill from field data")],
    in_: Annotated[Path, typer.Option("--in", exists=True, dir_okay=False, help="Field data file to read")],
    format: Annotated[str, typer.Option(help="fdf|xfdf|json|csv")] = "json",
    page: PageOption = 0,
    out: OutOption = None,
    overwrite: OverwriteOption = False,
    password: PasswordOption = None,
) -> None:
    """FRM-07: apply previously-exported (or hand-written) field data to one page, in
    one undo step. An unknown field name is reported, not a fatal error for the whole
    import."""
    data_base64 = base64.b64encode(in_.read_bytes()).decode("ascii")
    op = ImportFormDataOp(page_index=page, format=format, data_base64=data_base64)  # type: ignore[arg-type]
    try:
        with Document.open(path, password=password) as document:
            result = _run(op, document)
            saved_to = _save(document, path, out, overwrite)
    except PdfWorkerzError as exc:
        raise _fail(exc) from exc
    typer.echo(f"filled {len(result.filled)} field(s) on page {page}")
    if result.unknown:
        typer.echo(f"unknown field name(s), not applied: {', '.join(result.unknown)}", err=True)
    typer.echo(f"saved -> {saved_to}")


@forms_app.command("xfa-check")
def forms_xfa_check(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PDF file to check")],
    password: PasswordOption = None,
) -> None:
    """FRM-08: whether this document's AcroForm carries an /XFA entry (its layer isn't
    processed here -- only its ordinary static fields, if any, are read or filled)."""
    try:
        with Document.open(path, password=password) as document:
            report = _run(DetectXFAOp(), document)
    except PdfWorkerzError as exc:
        raise _fail(exc) from exc
    typer.echo(f"has_xfa={report.has_xfa} has_static_fields={report.has_static_fields}")
    if report.warning:
        typer.echo(report.warning, err=True)


@forms_app.command("detect")
def forms_detect(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PDF file to scan for flat-form cues")],
    page: PageOption = 0,
    dpi: Annotated[int, typer.Option(help="Render DPI used to detect visual cues")] = 150,
    apply_: Annotated[
        bool, typer.Option("--apply", help="Create a real field for every detected proposal, in one undo step")
    ] = False,
    out: OutOption = None,
    overwrite: OverwriteOption = False,
    password: PasswordOption = None,
) -> None:
    """FRM-04: detect likely field locations on a flat (non-interactive) page
    from visual cues alone (a line/underscore -> text field, a small box ->
    checkbox). Dry run by default -- prints each proposal for review; nothing
    is created unless --apply is given, which then creates every proposal as
    a real field in one undo step (see engine.form_detect for this
    heuristic's documented limitations)."""
    try:
        with Document.open(path, password=password) as document:
            proposals = _run(DetectFormFieldsOp(page_index=page, dpi=dpi), document)
            if not proposals:
                typer.echo("no form field cues detected on this page")
                return
            for proposal in proposals:
                label = f" label={proposal.label_text!r}" if proposal.label_text else ""
                typer.echo(
                    f"{proposal.suggested_name!r}: {proposal.field_type} rect={proposal.rect} "
                    f"confidence={proposal.confidence:.2f}{label}"
                )
            if apply_:
                _run(CreateDetectedFieldsOp(page_index=page, proposals=proposals), document)
                saved_to = _save(document, path, out, overwrite)
                typer.echo(f"created {len(proposals)} field(s) on page {page}")
                typer.echo(f"saved -> {saved_to}")
    except PdfWorkerzError as exc:
        raise _fail(exc) from exc


@app.command()
def redact(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PDF file to redact")],
    rect: Annotated[list[str], typer.Option("--rect", help='Area to redact, "x0,y0,x1,y1" (repeatable)')] = [],  # noqa: B006 -- typer reads this as the option's default, never mutated
    page: PageOption = 0,
    out: OutOption = None,
    overwrite: OverwriteOption = False,
    password: PasswordOption = None,
) -> None:
    """SEC-08: true redaction -- removes the glyphs, image pixels and vector paths
    under each --rect, never just a box drawn over them (see engine.redact). Runs
    SEC-11's verification automatically; fails loudly if anything is still recoverable."""
    if not rect:
        raise typer.BadParameter("give at least one --rect x0,y0,x1,y1")
    rects = [_parse_rect(value) for value in rect]
    try:
        with Document.open(path, password=password) as document:
            result = _run(RedactAreasOp(page_index=page, rects=rects), document)
            saved_to = _save(document, path, out, overwrite)
    except PdfWorkerzError as exc:
        raise _fail(exc) from exc
    for stats in result.stats:
        typer.echo(
            f"  {stats.rect}: {stats.glyphs_removed} glyph(s), "
            f"{stats.images_removed} image(s) removed, {stats.images_altered} image(s) altered, "
            f"{stats.shapes_removed} shape(s) removed, {stats.shapes_clipped} shape(s) clipped"
        )
    typer.echo("verification: passed" if result.verification.ok else "verification: FAILED")
    typer.echo(f"saved -> {saved_to}")


@app.command("redact-pattern")
def redact_pattern(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PDF file to scan")],
    pattern: Annotated[
        str, typer.Option(help='Built-in pattern ("email", "phone", "ssn", "credit_card") or a custom regex')
    ],
    page: PageOption = 0,
    apply_: Annotated[bool, typer.Option("--apply", help="Redact every match; default only lists them")] = False,
    out: OutOption = None,
    overwrite: OverwriteOption = False,
    password: PasswordOption = None,
) -> None:
    """SEC-09: find text matching a redaction pattern pack. Without --apply, this is a
    dry run: it only lists what it found. --apply redacts every match in one undo step
    (SEC-08's true redaction, SEC-11's verification included)."""
    try:
        with Document.open(path, password=password) as document:
            matches = _run(FindRedactionCandidatesOp(page_index=page, pattern=pattern), document)
            if not matches:
                typer.echo("no matches found")
                return
            for match in matches:
                typer.echo(f"  span {match.span_index} [{match.start}:{match.end}] {match.text!r} rect={match.rect}")
            if not apply_:
                typer.echo(f"{len(matches)} match(es) found; re-run with --apply to redact them")
                return
            result = _run(RedactAreasOp(page_index=page, rects=[m.rect for m in matches]), document)
            saved_to = _save(document, path, out, overwrite)
    except PdfWorkerzError as exc:
        raise _fail(exc) from exc
    typer.echo("verification: passed" if result.verification.ok else "verification: FAILED")
    typer.echo(f"redacted {len(matches)} match(es)")
    typer.echo(f"saved -> {saved_to}")


@app.command()
def sanitize(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PDF file to sanitize")],
    keep_metadata: Annotated[bool, typer.Option("--keep-metadata", help="Don't remove Info/XMP metadata")] = False,
    keep_javascript: Annotated[bool, typer.Option("--keep-javascript", help="Don't remove JavaScript")] = False,
    keep_embedded_files: Annotated[
        bool, typer.Option("--keep-embedded-files", help="Don't remove embedded files")
    ] = False,
    keep_hidden_text: Annotated[
        bool, typer.Option("--keep-hidden-text", help="Don't remove invisible (render mode 3) text")
    ] = False,
    out: OutOption = None,
    overwrite: OverwriteOption = False,
    password: PasswordOption = None,
) -> None:
    """SEC-10: remove metadata/XMP, JavaScript, embedded files and hidden text from the
    whole document; each category removed by default, keep one with --keep-*."""
    try:
        with Document.open(path, password=password) as document:
            report = _run(
                SanitizeOp(
                    remove_metadata=not keep_metadata,
                    remove_javascript=not keep_javascript,
                    remove_embedded_files=not keep_embedded_files,
                    remove_hidden_text=not keep_hidden_text,
                ),
                document,
            )
            saved_to = _save(document, path, out, overwrite)
    except PdfWorkerzError as exc:
        raise _fail(exc) from exc
    typer.echo(f"metadata removed: {report.metadata_removed}")
    typer.echo(f"javascript actions removed: {report.javascript_actions_removed}")
    typer.echo(f"embedded files removed: {report.embedded_files_removed}")
    typer.echo(f"hidden text characters removed: {report.hidden_text_chars_removed}")
    typer.echo(f"saved -> {saved_to}")
