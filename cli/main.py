"""The pdfworkerz CLI (COR-10).

Every command here is a thin wrapper around the same engine calls the
future server and UI will use (SPEC.md section 4.2 rule 1: one code path
per capability) -- ``inspect`` and ``render`` go through the registered
:class:`~engine.ops.base.Op` classes so the CLI, recipes and command bar
stay on the exact same execution path once those exist.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

import typer

from engine import __version__
from engine.document import Document
from engine.edit import EditResult
from engine.errors import PdfWorkerzError
from engine.ops.base import InspectOp, RenderPageOp
from engine.ops.text import DeleteTextOp, InsertTextOp, ReplaceTextOp, RestyleTextOp

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


@app.command()
def inspect(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PDF file to inspect")],
    password: Annotated[str | None, typer.Option(help="User password, if the file is encrypted")] = None,
) -> None:
    """Print the document inspection report (COR-03) as JSON."""
    try:
        with Document.open(path, password=password) as document:
            report = InspectOp(password=password or "").apply(document)
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
            png_bytes = RenderPageOp(page_index=page, dpi=dpi).apply(document)
    except PdfWorkerzError as exc:
        raise _fail(exc) from exc
    output = out or path.with_name(f"{path.stem}.p{page}.png")
    output.write_bytes(png_bytes)
    typer.echo(str(output))


@app.command()
def replace(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PDF file to edit")],
    match: Annotated[str, typer.Argument(help="Text to find (literal, unless --regex)")],
    replacement: Annotated[str, typer.Argument(help="Text to put in its place")],
    regex: Annotated[bool, typer.Option(help="Treat match as a regular expression")] = False,
    case_insensitive: Annotated[bool, typer.Option(help="Match regardless of case")] = False,
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
    try:
        with Document.open(path, password=password) as document:
            op = ReplaceTextOp(
                match=match,
                replacement=replacement,
                mode="regex" if regex else "literal",
                case_sensitive=not case_insensitive,
                page_index=page,
                require_tier=require_tier,
                fit=fit,
            )
            results = op.apply(document)
            saved_to = _save(document, path, out, overwrite)
    except PdfWorkerzError as exc:
        raise _fail(exc) from exc
    _report(results)
    typer.echo(f"saved -> {saved_to}")


@app.command()
def delete(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PDF file to edit")],
    match: Annotated[str, typer.Argument(help="Text to find and remove (literal, unless --regex)")],
    regex: Annotated[bool, typer.Option(help="Treat match as a regular expression")] = False,
    case_insensitive: Annotated[bool, typer.Option(help="Match regardless of case")] = False,
    page: Annotated[int | None, typer.Option(help="Only this 0-based page; default is every page")] = None,
    require_tier: TierOption = "approximate",
    out: Annotated[Path | None, typer.Option(help="Output path; defaults to a new <name>.edited.pdf")] = None,
    overwrite: Annotated[bool, typer.Option(help="Write back to the original file instead")] = False,
    password: Annotated[str | None, typer.Option(help="User password, if the file is encrypted")] = None,
) -> None:
    """Find and remove text (EDT-04), closing the gap in its style-matched span."""
    try:
        with Document.open(path, password=password) as document:
            op = DeleteTextOp(
                match=match,
                mode="regex" if regex else "literal",
                case_sensitive=not case_insensitive,
                page_index=page,
                require_tier=require_tier,
            )
            results = op.apply(document)
            saved_to = _save(document, path, out, overwrite)
    except PdfWorkerzError as exc:
        raise _fail(exc) from exc
    _report(results)
    typer.echo(f"saved -> {saved_to}")


@app.command()
def restyle(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="PDF file to edit")],
    match: Annotated[str, typer.Argument(help="Text to find (literal, unless --regex)")],
    size: Annotated[float | None, typer.Option(help="New font size in points")] = None,
    color: Annotated[str | None, typer.Option(help='New color as "r,g,b", each 0-1, e.g. "1,0,0" for red')] = None,
    regex: Annotated[bool, typer.Option(help="Treat match as a regular expression")] = False,
    case_insensitive: Annotated[bool, typer.Option(help="Match regardless of case")] = False,
    page: Annotated[int | None, typer.Option(help="Only this 0-based page; default is every page")] = None,
    require_tier: TierOption = "approximate",
    out: Annotated[Path | None, typer.Option(help="Output path; defaults to a new <name>.edited.pdf")] = None,
    overwrite: Annotated[bool, typer.Option(help="Write back to the original file instead")] = False,
    password: Annotated[str | None, typer.Option(help="User password, if the file is encrypted")] = None,
) -> None:
    """Change the size and/or color of matching text (EDT-06), leaving its wording unchanged."""
    parsed_color = _parse_color(color) if color is not None else None
    try:
        with Document.open(path, password=password) as document:
            op = RestyleTextOp(
                match=match,
                size=size,
                color=parsed_color,
                mode="regex" if regex else "literal",
                case_sensitive=not case_insensitive,
                page_index=page,
                require_tier=require_tier,
            )
            results = op.apply(document)
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
    reference: Annotated[str, typer.Option(help="Literal text of the span whose style to copy")],
    page: Annotated[int, typer.Option(help="0-based page index")] = 0,
    require_tier: TierOption = "approximate",
    out: Annotated[Path | None, typer.Option(help="Output path; defaults to a new <name>.edited.pdf")] = None,
    overwrite: Annotated[bool, typer.Option(help="Write back to the original file instead")] = False,
    password: Annotated[str | None, typer.Option(help="User password, if the file is encrypted")] = None,
) -> None:
    """Add new text near an existing span (EDT-03), matching its style. Nothing existing is touched."""
    parsed_position = _parse_point(position)
    try:
        with Document.open(path, password=password) as document:
            op = InsertTextOp(
                page_index=page,
                text=text,
                position=parsed_position,
                reference_match=reference,
                require_tier=require_tier,
            )
            result = op.apply(document)
            saved_to = _save(document, path, out, overwrite)
    except PdfWorkerzError as exc:
        raise _fail(exc) from exc
    _report([result])
    typer.echo(f"saved -> {saved_to}")


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
def version() -> None:
    """Print the installed PDFWorkerz engine version."""
    typer.echo(__version__)


if __name__ == "__main__":
    app()
