"""SEC-10: remove metadata, XMP, JavaScript, embedded files and hidden text.

Each of the four categories is independent and opts out on its own
(``sanitize_document``'s keyword arguments, all default True); the result reports what
was actually found and removed in each, rather than a bare "done".

Metadata and XMP are cleared through PyMuPDF's own document-level calls
(``Document.set_metadata({})``, ``Document.del_xml_metadata()``) -- verified at runtime
(pymupdf 1.28.2): ``set_metadata({})`` blanks every Info field, and ``del_xml_metadata()``
drops the ``/Metadata`` stream and its xref entirely (``xref_xml_metadata()`` reads 0
afterwards), not just its visible text.

JavaScript has no PyMuPDF-level removal call, so it is cleared directly on the live
document's own xref objects (``Document.xref_set_key``, which pymupdf 1.28.2 accepts
slash-separated nested paths for, e.g. ``"Names/JavaScript"``) rather than by round-
tripping through a separate pikepdf copy: a document-level ``/OpenAction`` or ``/AA``,
the ``/Root/Names/JavaScript`` name tree, and every page's own ``/AA`` plus every form
field (widget)'s ``/AA`` and ``/A``. Setting a key to the literal ``null`` severs the
reference; the now-unreachable action dictionary itself is dropped on the next full
(non-incremental) save, which is already pymupdf's own garbage-collecting default
(``Document.save``'s ``garbage=4``) for an unsigned document -- so "no /JS or
/JavaScript actions remain" holds once the sanitized document is saved, which is the
same point at which SEC-08's own redaction is verified against.

Simplification: a field or page's ``/AA`` dictionary can hold several named triggers
(``/K``, ``/F``, ``/WC``, ...) in one dict; if *any* of them is a JavaScript action, the
*whole* ``/AA`` dict is cleared rather than picking out just the JS entries, so a
non-JS action sharing the same dict is also removed. This keeps the removal simple and
unambiguous rather than risking a JS action surviving tucked under a key this module
didn't think to check.

Embedded files reuse engine.structure's existing attachment machinery
(``list_attachments``/``remove_attachment``) rather than duplicating it.

Hidden text (render mode 3, invisible) reuses engine.edit's existing per-glyph redaction
primitive (``_redact_spans``) -- the same machinery SEC-08's true redaction and every
text edit in this codebase removes glyphs with -- rather than a second implementation of
"delete these glyphs".
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.document import Document
from engine.edit import _redact_spans
from engine.fonts.style import extract_page_spans
from engine.structure import list_attachments, remove_attachment

_JS_MARKER = "JavaScript"


@dataclass(frozen=True)
class SanitizeReport:
    """What one ``sanitize_document`` call actually found and removed, per category."""

    metadata_removed: bool
    """Info dictionary fields and XMP metadata were cleared."""
    javascript_actions_removed: int
    """How many separate action locations (open action, document /AA, a page's /AA, a
    field's /AA or /A, the Names/JavaScript tree) carried JavaScript and were cleared."""
    embedded_files_removed: int
    hidden_text_chars_removed: int
    """How many invisible (render mode 3) glyphs were removed."""


def _clear_if_javascript(document: Document, xref: int, key: str) -> bool:
    kind, value = document.raw.xref_get_key(xref, key)
    if kind in ("null", "") or not value or _JS_MARKER not in value:
        return False
    document.raw.xref_set_key(xref, key, "null")
    return True


def _remove_javascript(document: Document) -> int:
    doc = document.raw
    removed = 0
    catalog = doc.pdf_catalog()

    if _clear_if_javascript(document, catalog, "OpenAction"):
        removed += 1
    if _clear_if_javascript(document, catalog, "AA"):
        removed += 1

    names_kind, names_value = doc.xref_get_key(catalog, "Names")
    if names_kind not in ("null", "") and names_value and _JS_MARKER in names_value:
        doc.xref_set_key(catalog, "Names/JavaScript", "null")
        removed += 1

    for page_index in range(document.page_count):
        page = doc[page_index]
        if _clear_if_javascript(document, page.xref, "AA"):
            removed += 1
        for widget in page.widgets():
            if _clear_if_javascript(document, widget.xref, "AA"):
                removed += 1
            if _clear_if_javascript(document, widget.xref, "A"):
                removed += 1
    return removed


def _remove_hidden_text(document: Document) -> int:
    removed = 0
    for page_index in range(document.page_count):
        spans = extract_page_spans(document.raw, page_index)
        hidden = [
            span
            for span in spans
            if span.text_state is not None and span.text_state.render_mode == 3 and span.style.text.strip()
        ]
        if not hidden:
            continue
        _redact_spans(document.raw[page_index], hidden)
        removed += sum(len(span.style.chars) for span in hidden)
    return removed


def sanitize_document(
    document: Document,
    *,
    remove_metadata: bool = True,
    remove_javascript: bool = True,
    remove_embedded_files: bool = True,
    remove_hidden_text: bool = True,
) -> SanitizeReport:
    """SEC-10: remove the requested categories from `document` (mutated in place) and
    report what was found and removed in each. Every category defaults on; pass any of
    them False to keep it (the CLI's ``--keep-*`` flags, the Op's matching booleans)."""
    # Order matters: confirmed at runtime (pymupdf 1.28.2) that del_xml_metadata()'s
    # removal of the /Metadata stream can be silently undone by a later content-stream
    # redaction (apply_redactions, which _remove_hidden_text below triggers) -- the
    # /Metadata reference reappears after it. Metadata/XMP removal therefore runs
    # *last*, after every other category that might touch a page's content stream.
    javascript_removed = _remove_javascript(document) if remove_javascript else 0

    files_removed = 0
    if remove_embedded_files:
        for attachment in list_attachments(document):
            remove_attachment(document, attachment.name)
            files_removed += 1

    hidden_removed = _remove_hidden_text(document) if remove_hidden_text else 0

    metadata_removed = False
    if remove_metadata:
        document.raw.set_metadata({})
        document.raw.del_xml_metadata()
        metadata_removed = True

    return SanitizeReport(
        metadata_removed=metadata_removed,
        javascript_actions_removed=javascript_removed,
        embedded_files_removed=files_removed,
        hidden_text_chars_removed=hidden_removed,
    )
