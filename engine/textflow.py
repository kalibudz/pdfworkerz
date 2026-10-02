"""Reading a PDF's pages as *structured text* -- headings, paragraphs, lists, tables, pictures --
instead of glyphs at coordinates. This is what plain-text, Markdown and HTML export are built
on (CVF-05, CVF-06), so all three agree on what is a heading or a list.

Heuristics, stated so they can be checked:

* The **body size** is the most common font size, by characters. A block set wholly in a size
  at least 15% larger is a **heading**; the larger the size, the higher the level (up to 4).
* A line that starts with a bullet character or ``1.`` / ``1)`` starts a **list item**.
* Lines of a block are joined into one paragraph; a word broken by a hyphen at a line end is
  mended (``exam-`` / ``ple`` becomes ``example``) when the next line continues in lower case.
* A **table** is what PyMuPDF's table finder (engine.tables) detects; the text inside it is
  left out of the paragraphs so it appears once.
* A **running header or footer** (text in the top or bottom margin that repeats on other pages,
  or is a page number) can be left out.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import pymupdf

from engine.document import Document
from engine.tables import find_page_tables

_BOLD, _ITALIC, _MONO = 16, 2, 8  # PyMuPDF span flags
_HEADING_RATIO = 1.15
_MAX_HEADING_LEVEL = 4
_MARGIN_ZONE = 0.1
_BULLETS = "\u2022\u00b7\u25e6\u25aa\u25cf\u2023\u2043-\u2013*"
_LIST_MARK = re.compile(rf"^\s*(?:(?P<bullet>[{re.escape(_BULLETS)}])|(?P<number>\d{{1,3}}[.)]))\s+")
_PAGE_NUMBER = re.compile(r"^(?:page\s*)?#+(?:\s*(?:of|/)\s*#+)?$|^[-\u2013]\s*#+\s*[-\u2013]$")


@dataclass(frozen=True)
class Run:
    """A stretch of text in one style."""

    text: str
    bold: bool = False
    italic: bool = False
    mono: bool = False


@dataclass(frozen=True)
class Block:
    """One piece of the page's content, in reading order."""

    kind: Literal["heading", "paragraph", "list_item", "table", "image"]
    page_index: int
    runs: tuple[Run, ...] = ()
    level: int = 0
    """Heading level, 1 is the largest."""
    ordered: bool = False
    """For a list item: it was numbered, not bulleted."""
    marker: str = ""
    """For a list item: the number as written ("2."); empty for a bullet."""
    rows: tuple[tuple[str, ...], ...] = ()
    image: bytes = field(default=b"", repr=False)
    image_ext: str = ""

    @property
    def text(self) -> str:
        return "".join(run.text for run in self.runs)


_LIGATURES = str.maketrans({"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st"})


def _merged(runs: list[Run]) -> list[Run]:
    """Neighbouring runs of one style as a single run, so ``**bold** text`` isn't ``**bo****ld**``."""
    out: list[Run] = []
    for run in runs:
        if out and (out[-1].bold, out[-1].italic, out[-1].mono) == (run.bold, run.italic, run.mono):
            out[-1] = Run(out[-1].text + run.text, run.bold, run.italic, run.mono)
        else:
            out.append(run)
    return out


def _runs(spans: list[dict[str, Any]]) -> list[Run]:
    flags = [span["flags"] for span in spans]
    return _merged(
        [
            Run(s["text"].translate(_LIGATURES), bool(f & _BOLD), bool(f & _ITALIC), bool(f & _MONO))
            for s, f in zip(spans, flags, strict=True)
        ]
    )


def _body_size(pages: list[dict[str, Any]]) -> float:
    sizes: Counter[float] = Counter()
    for page in pages:
        for block in page["blocks"]:
            for line in block.get("lines", ()):
                for span in line["spans"]:
                    sizes[round(span["size"] * 2) / 2] += len(span["text"].strip())
    return sizes.most_common(1)[0][0] if sizes else 11.0


def _line_text(line: dict[str, Any]) -> str:
    return "".join(span["text"] for span in line["spans"])


def _normalise(text: str) -> str:
    return re.sub(r"\d+", "#", " ".join(text.lower().split()))


def _running_keys(pages: list[dict[str, Any]]) -> set[str]:
    """Texts in the top or bottom margin that appear on more than one page."""
    seen: Counter[str] = Counter()
    for page in pages:
        keys = {_normalise(_block_text(b)) for b in _margin_blocks(page)}
        seen.update(keys)
    return {key for key, count in seen.items() if count > 1}


def _margin_blocks(page: dict[str, Any]) -> list[dict[str, Any]]:
    height = page["height"]
    return [
        b
        for b in page["blocks"]
        if b["type"] == 0 and (b["bbox"][3] < height * _MARGIN_ZONE or b["bbox"][1] > height * (1 - _MARGIN_ZONE))
    ]


def _block_text(block: dict[str, Any]) -> str:
    return " ".join(_line_text(line).strip() for line in block["lines"])


def _join_lines(lines: list[list[Run]]) -> list[Run]:
    """Lines as one paragraph: a space between them, or a mended word where one broke at a hyphen."""
    joined: list[Run] = []
    for number, line in enumerate(lines):
        if not line:
            continue
        if joined and number:
            tail, head = joined[-1], line[0]
            if tail.text.endswith("-") and head.text[:1].islower() and len(tail.text) > 1 and tail.text[-2].isalpha():
                joined[-1] = Run(tail.text[:-1], tail.bold, tail.italic, tail.mono)
            elif not tail.text.endswith(" ") and not head.text.startswith(" "):
                joined[-1] = Run(tail.text + " ", tail.bold, tail.italic, tail.mono)
        joined.extend(line)
    return _merged(joined)


def _strip_marker(runs: list[Run], marker: re.Match[str]) -> list[Run]:
    text = runs[0].text
    rest = text[marker.end() :]
    return ([Run(rest, runs[0].bold, runs[0].italic, runs[0].mono)] if rest else []) + runs[1:]


def _text_blocks(page_index: int, block: dict[str, Any], heading_sizes: list[float]) -> list[Block]:
    """One text block of the page as headings, a paragraph or list items."""
    lines = [line for line in block["lines"] if _line_text(line).strip()]
    if not lines:
        return []
    sizes = {round(span["size"] * 2) / 2 for line in lines for span in line["spans"] if span["text"].strip()}
    if len(sizes) == 1 and next(iter(sizes)) in heading_sizes and len(lines) <= 3:
        level = heading_sizes.index(next(iter(sizes))) + 1
        runs = _join_lines([_runs(line["spans"]) for line in lines])
        return [Block("heading", page_index, tuple(runs), level=level)]
    items: list[tuple[re.Match[str] | None, list[list[Run]]]] = []
    for line in lines:
        runs = _runs(line["spans"])
        marker = _LIST_MARK.match(runs[0].text) if runs else None
        if marker:
            items.append((marker, [_strip_marker(runs, marker)]))
        elif items:
            items[-1][1].append(runs)  # a wrapped continuation of the item above
        else:
            items.append((None, [runs]))
    out: list[Block] = []
    for marker, item_lines in items:
        runs = _join_lines(item_lines)
        if marker is None:
            out.append(Block("paragraph", page_index, tuple(runs)))
        else:
            number = marker.group("number")
            out.append(Block("list_item", page_index, tuple(runs), ordered=bool(number), marker=number or ""))
    return _merge_paragraphs(out)


def _merge_paragraphs(blocks: list[Block]) -> list[Block]:
    """Adjacent plain paragraphs from one block are one paragraph (a block is already a paragraph)."""
    merged: list[Block] = []
    for block in blocks:
        if merged and block.kind == merged[-1].kind == "paragraph":
            joined = _join_lines([list(merged[-1].runs), list(block.runs)])
            merged[-1] = Block("paragraph", block.page_index, tuple(joined))
        else:
            merged.append(block)
    return merged


def extract_flow(
    document: Document,
    page_indices: Sequence[int] | None = None,
    *,
    skip_running: bool = False,
    images: bool = False,
    tables: bool = True,
) -> list[Block]:
    """The selected pages (all by default) as blocks in reading order."""
    indices = list(range(document.page_count)) if page_indices is None else list(page_indices)
    pages = [
        document.raw[i].get_text("dict", sort=True, flags=pymupdf.TEXTFLAGS_DICT | pymupdf.TEXT_PRESERVE_IMAGES)
        for i in indices
    ]
    body = _body_size(pages)
    sizes = sorted(
        {
            round(span["size"] * 2) / 2
            for page in pages
            for block in page["blocks"]
            for line in block.get("lines", ())
            for span in line["spans"]
            if span["text"].strip() and span["size"] >= body * _HEADING_RATIO
        },
        reverse=True,
    )
    heading_sizes = sizes[:_MAX_HEADING_LEVEL]
    running = _running_keys(pages) if skip_running and len(pages) > 1 else set()
    flow: list[Block] = []
    for index, page in zip(indices, pages, strict=True):
        found = find_page_tables(document.raw[index]) if tables else []
        positioned: list[tuple[float, Block]] = []
        margin = {id(b) for b in _margin_blocks(page)}
        for block in page["blocks"]:
            if block["type"] == 1:
                if images and block.get("image"):
                    positioned.append(
                        (block["bbox"][1], Block("image", index, image=block["image"], image_ext=block["ext"]))
                    )
                continue
            if any(_inside(block["bbox"], table.bbox) for table in found) or not block["lines"]:
                continue
            if skip_running and id(block) in margin:
                key = _normalise(_block_text(block))
                if key in running or _PAGE_NUMBER.match(key):
                    continue
            positioned.extend((block["bbox"][1], b) for b in _text_blocks(index, block, heading_sizes))
        positioned.extend((t.bbox[1], Block("table", index, rows=tuple(tuple(r) for r in t.rows))) for t in found)
        flow.extend(block for _, block in sorted(positioned, key=lambda pair: pair[0]))
    return flow


def _inside(inner: tuple[float, ...], outer: tuple[float, ...]) -> bool:
    cx, cy = (inner[0] + inner[2]) / 2, (inner[1] + inner[3]) / 2
    return outer[0] <= cx <= outer[2] and outer[1] <= cy <= outer[3]
