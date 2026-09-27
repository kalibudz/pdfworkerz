"""The pdfworkerz CLI (COR-10).

Every command here is a thin wrapper around the same engine calls the
future server and UI will use (SPEC.md section 4.2 rule 1: one code path
per capability) -- ``inspect`` and ``render`` go through the registered
:class:`~engine.ops.base.Op` classes so the CLI, recipes and command bar
stay on the exact same execution path once those exist.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from engine import __version__
from engine.document import Document
from engine.errors import PdfWorkerzError
from engine.ops.base import InspectOp, RenderPageOp

app = typer.Typer(add_completion=False, no_args_is_help=True, help="PDFWorkerz: free, offline, token-free PDF editing.")


def _fail(exc: PdfWorkerzError) -> typer.Exit:
    typer.echo(f"error: {exc}", err=True)
    return typer.Exit(code=1)


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
