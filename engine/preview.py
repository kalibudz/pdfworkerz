"""CMD-05: what Ops would change, counted without changing anything.

Used by the command bar (preview before apply) and by recipes' dry runs. Text-finding Ops
report how many matches they would edit and on which pages; an insert reports whether its
reference text was found; any other Op is listed as "will be applied" with no count.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from engine.document import Document
from engine.fonts.style import dedupe_texttrace
from engine.ops.base import parse_op
from engine.ops.text import _compile_pattern


@dataclass
class OpPreview:
    op: str
    matches: int | None
    """How many matches it would edit; None when the Op doesn't search for text."""
    pages: list[int] = field(default_factory=list)
    """1-based pages with at least one match."""


@dataclass
class Preview:
    matches: int
    pages: list[int]
    ops: list[OpPreview]
    warnings: list[str]


def _span_texts(document: Document, page_index: int) -> list[str]:
    spans = dedupe_texttrace(document.raw[page_index].get_texttrace())
    return ["".join(chr(char[0]) for char in span["chars"]) for span in spans]


def _pages(document: Document, page_index: int | None) -> range:
    return range(document.page_count) if page_index is None else range(page_index, page_index + 1)


def preview_op(document: Document, data: dict[str, Any]) -> OpPreview:
    op = parse_op(data)
    name = data["op"]
    if name in ("replace_text", "delete_text", "restyle_text"):
        fields = op.model_dump()
        pattern = _compile_pattern(
            fields["match"], fields["mode"], fields["case_sensitive"], fields.get("whole_word", False)
        )
        total, pages = 0, []
        for page_index in _pages(document, fields["page_index"]):
            texts = _span_texts(document, page_index)
            if name == "restyle_text":
                found = sum(1 for text in texts if pattern.search(text))
            else:
                found = sum(len(pattern.findall(text)) for text in texts)
            if found:
                total += found
                pages.append(page_index + 1)
        return OpPreview(name, total, pages)
    if name == "insert_text":
        fields = op.model_dump()
        page = fields["page_index"]
        if fields["reference_match"] is None:
            return OpPreview(name, 1, [page + 1])
        pattern = _compile_pattern(fields["reference_match"], "literal", fields["reference_case_sensitive"])
        found = any(pattern.search(text) for text in _span_texts(document, page))
        return OpPreview(name, 1 if found else 0, [page + 1] if found else [])
    if name == "batch":
        parts = [preview_op(document, item) for item in data["ops"]]
        counted = [p for p in parts if p.matches is not None]
        batch_total = sum(p.matches or 0 for p in counted) if counted else None
        return OpPreview(name, batch_total, sorted({page for p in parts for page in p.pages}))
    return OpPreview(name, None)


def preview_ops(document: Document, ops: list[dict[str, Any]]) -> Preview:
    parts = [preview_op(document, data) for data in ops]
    counted = [p for p in parts if p.matches is not None]
    matches = sum(p.matches or 0 for p in counted)
    searching = [p for p in counted if p.op != "insert_text"]
    warnings = []
    if searching and sum(p.matches or 0 for p in searching) == 0:
        warnings.append("nothing matches, so applying this would change nothing")
    for part in parts:
        if part.op == "insert_text" and part.matches == 0:
            warnings.append("the text to place it by wasn't found on that page")
    return Preview(matches, sorted({page for p in parts for page in p.pages}), parts, warnings)
