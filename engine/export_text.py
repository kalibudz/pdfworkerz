"""CVF-05 / CVF-06: a PDF as plain text, Markdown or HTML.

All three render the same blocks (engine.textflow), so a heading is a heading in each.
"""

from __future__ import annotations

import base64
import html
import re
from collections.abc import Sequence
from dataclasses import replace

from engine.document import Document
from engine.textflow import Block, Run, extract_flow

_MARKDOWN_SPECIAL = re.compile(r"([\\`*_\[\]<>])")
_LEADING_BLOCK_MARK = re.compile(r"^(\s*)([#>+-]|\d+[.)])(?=\s)")
_HTML_STYLE = """
body { font-family: system-ui, sans-serif; line-height: 1.5; max-width: 48rem; margin: 2rem auto; padding: 0 1rem; }
table { border-collapse: collapse; margin: 1rem 0; }
td, th { border: 1px solid #999; padding: 0.25rem 0.6rem; vertical-align: top; }
section + section { border-top: 1px solid #ccc; margin-top: 2rem; padding-top: 1rem; }
img { max-width: 100%; height: auto; }
""".strip()


def _pages_of(flow: Sequence[Block]) -> list[tuple[int, list[Block]]]:
    pages: dict[int, list[Block]] = {}
    for block in flow:
        pages.setdefault(block.page_index, []).append(block)
    return sorted(pages.items())


# ---------- plain text ----------


def to_text(document: Document, page_indices: Sequence[int] | None = None, *, skip_running: bool = False) -> str:
    """The text in reading order. Each page starts with a ``--- Page N ---`` line; paragraphs
    are separated by a blank line; a table's cells are separated by tabs."""
    parts = []
    for index, blocks in _pages_of(extract_flow(document, page_indices, skip_running=skip_running)):
        lines = [f"--- Page {index + 1} ---"]
        for block in blocks:
            if block.kind == "table":
                lines.append("\n".join("\t".join(row) for row in block.rows))
            elif block.kind == "list_item":
                lines.append(f"{block.marker or chr(0x2022)} {block.text}")
            else:
                lines.append(block.text)
        parts.append("\n\n".join(lines))
    return "\n\n".join(parts) + "\n" if parts else ""


# ---------- Markdown ----------


def _markdown_text(text: str) -> str:
    return _MARKDOWN_SPECIAL.sub(r"\\\1", text)


def _markdown_runs(runs: Sequence[Run], *, plain_weight: bool = False) -> str:
    out = []
    for run in runs:
        run = replace(run, bold=False) if plain_weight else run
        text = run.text.strip("\n")
        if not text.strip():
            out.append(text)
            continue
        lead, core, tail = text[: len(text) - len(text.lstrip())], text.strip(), text[len(text.rstrip()) :]
        if run.mono:
            core = f"`{core}`"
        else:
            core = _markdown_text(core)
            if run.bold and run.italic:
                core = f"***{core}***"
            elif run.bold:
                core = f"**{core}**"
            elif run.italic:
                core = f"*{core}*"
        out.append(lead + core + tail)
    return "".join(out).strip()


def _markdown_table(rows: Sequence[Sequence[str]]) -> str:
    width = max(len(row) for row in rows)

    def line(cells: Sequence[str]) -> str:
        padded = [_markdown_text(cell).replace("|", "\\|") for cell in cells] + [""] * (width - len(cells))
        return "| " + " | ".join(padded) + " |"

    return "\n".join([line(rows[0]), "| " + " | ".join(["---"] * width) + " |", *(line(row) for row in rows[1:])])


def to_markdown(document: Document, page_indices: Sequence[int] | None = None, *, skip_running: bool = False) -> str:
    """Markdown: ``#`` headings, ``**bold**`` and ``*italic*``, ``-`` and ``1.`` lists and pipe
    tables, with a rule between pages."""
    pages = []
    for _, blocks in _pages_of(extract_flow(document, page_indices, skip_running=skip_running)):
        parts: list[str] = []
        previous_item = False
        for block in blocks:
            if block.kind == "heading":
                parts.append(f"{'#' * block.level} {_markdown_runs(block.runs, plain_weight=True)}")
            elif block.kind == "list_item":
                bullet = block.marker if block.ordered else "-"
                item = f"{bullet} {_markdown_runs(block.runs)}"
                if previous_item:
                    parts[-1] += "\n" + item
                else:
                    parts.append(item)
            elif block.kind == "table":
                parts.append(_markdown_table(block.rows))
            elif block.kind == "paragraph":
                text = _markdown_runs(block.runs)
                parts.append(_LEADING_BLOCK_MARK.sub(r"\1\\\2", text))  # a paragraph that happens to begin like a list
            previous_item = block.kind == "list_item"
        pages.append("\n\n".join(parts))
    return "\n\n---\n\n".join(pages) + "\n" if pages else ""


# ---------- HTML ----------


def _html_runs(runs: Sequence[Run], *, plain_weight: bool = False) -> str:
    out = []
    for run in runs:
        run = replace(run, bold=False) if plain_weight else run
        text = html.escape(run.text, quote=False)
        if run.mono:
            text = f"<code>{text}</code>"
        if run.bold:
            text = f"<strong>{text}</strong>"
        if run.italic:
            text = f"<em>{text}</em>"
        out.append(text)
    return "".join(out).strip()


def _html_table(rows: Sequence[Sequence[str]]) -> str:
    head, *body = rows
    cells = lambda row, tag: "".join(f"<{tag}>{html.escape(c, quote=False)}</{tag}>" for c in row)  # noqa: E731
    return (
        "<table><thead><tr>"
        + cells(head, "th")
        + "</tr></thead><tbody>"
        + "".join("<tr>" + cells(row, "td") + "</tr>" for row in body)
        + "</tbody></table>"
    )


def to_html(
    document: Document, page_indices: Sequence[int] | None = None, *, skip_running: bool = False, title: str = ""
) -> str:
    """One self-contained HTML file: headings, paragraphs, lists and tables as real elements, each
    page a ``<section>``, pictures embedded as data. Every piece of the PDF's text is escaped."""
    sections = []
    for index, blocks in _pages_of(extract_flow(document, page_indices, skip_running=skip_running, images=True)):
        parts: list[str] = []
        open_list = ""
        for block in blocks:
            wanted = ("ol" if block.ordered else "ul") if block.kind == "list_item" else ""
            if open_list and wanted != open_list:
                parts.append(f"</{open_list}>")
                open_list = ""
            if wanted and not open_list:
                parts.append(f"<{wanted}>")
                open_list = wanted
            if block.kind == "heading":
                parts.append(f"<h{block.level}>{_html_runs(block.runs, plain_weight=True)}</h{block.level}>")
            elif block.kind == "paragraph":
                parts.append(f"<p>{_html_runs(block.runs)}</p>")
            elif block.kind == "list_item":
                parts.append(f"<li>{_html_runs(block.runs)}</li>")
            elif block.kind == "table":
                parts.append(_html_table(block.rows))
            elif block.kind == "image":
                data = base64.b64encode(block.image).decode("ascii")
                parts.append(f'<img alt="" src="data:image/{block.image_ext};base64,{data}">')
        if open_list:
            parts.append(f"</{open_list}>")
        sections.append(f'<section data-page="{index + 1}">\n' + "\n".join(parts) + "\n</section>")
    name = html.escape(title or (document.source_path.stem if document.source_path else "Document"))
    return (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        f"<title>{name}</title>\n<style>\n{_HTML_STYLE}\n</style>\n</head>\n<body>\n"
        + "\n".join(sections)
        + "\n</body>\n</html>\n"
    )
