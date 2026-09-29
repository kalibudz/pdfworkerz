# Developer notes

Rules and design decisions that came out of real bugs or deliberate trade-offs. They used to be kept as "open questions" in `state/checkpoint.json`. They aren't open, though: each one is settled, and breaking it has caused a real failure before. Read the section for the area you're about to change.

Known limitations and genuinely open work stay in the checkpoint's `openQuestions` and on the build board, grouped by phase.

## Engine (Python)

- **Never call `tobytes()` or `save()` on a live document without `encryption=PDF_ENCRYPT_KEEP`.** Measured on pymupdf 1.28.2, a plain `tobytes()` permanently drops an encrypted document's encryption state: every later save, even with KEEP, writes the file decrypted. Use `engine.pdfbytes.plain_bytes(doc)` to get decrypted bytes for parsing (it decrypts a throwaway copy), and `Document.snapshot()` for the journal.
- **Read `needs_pass` only before `authenticate()`.** Measured on pymupdf 1.28.2: reading it after a successful login breaks decryption, so every page reads blank. Other properties (`is_encrypted`, `metadata`, `permissions`) are safe.
- **Rotated text goes through `insert_text(morph=(point, Matrix(-rotation_degrees)))`.** That is the matrix whose output texttrace reports at the original `rotation_degrees`; without it, glyphs come back upright.
- **Redact per glyph, never by span bbox.** A span's bbox runs from ascender to descender, and a text redaction removes every glyph its rectangle touches, so a bbox redaction deleted the lines above and below at ordinary leading. `_redact_spans` in `engine/edit.py` puts a tiny square at each glyph's centre, then checks the glyph count and refuses the edit if anything else vanished.
- **Draw at the effective size, not the `Tf` operand.** `Tf 1` under a `12 0 0 12` text matrix renders at 12pt. `_drawing_metrics` derives the scale from texttrace's size (which is `Tf` times the matrix's horizontal scale, including `Tz`) and scales `Tc`, `Tw`, rise and leading with it.
- **One font resource name per font program.** `page.insert_font` reuses whatever is already registered under a name, so the edit font's name includes a hash of its bytes.
- **Bounds checks use `engine.geometry.page_bounds(page)`,** not `page.rect`. Edit coordinates are unrotated, and `page.rect` is the rotated view.
- **Test encrypted behaviour on files with real content.** The corpus's encrypted files once had blank pages, so no test could see content being lost. MuPDF also can't read back an empty AES-encrypted stream.
- **Match fonts by normalized name, never by raw string.** Font names from texttrace, `Page.get_fonts()` and pikepdf go through `engine.fonts.match.normalize_font_name`, with a containment-match fallback. See `_find_font_entry` in `engine/edit.py` and `recover_broken_spans` in `engine/fonts/tounicode.py`. Comparing raw names caused three separate bugs in P2.
- **Target one span with `ReplaceSpanTextOp`.** `ReplaceTextOp` searches for its text and edits every match on the page. A UI action on the span the user clicked must use the index-based `ReplaceSpanTextOp`.
- **Read-only Ops are never journaled.** `PreviewTextOp`, `PageSpansOp` and `render_original_page` are applied directly in `server/app.py`. New read-only Ops and routes should work the same way.

## Web UI (`web/src`)

- **Don't use `position: fixed` for persistent UI.** It twice put an element over the toolbar, where it silently took clicks meant for the button underneath (the theme toggle over Compare). Use in-flow layout (flexbox or grid with reserved space), or a native `<dialog>`, which opens in the top layer on purpose.
- **Check `isContentEditable` for typing.** `isTypingTarget` in `viewer.ts` tests `isContentEditable`, which also covers descendants, as well as INPUT and TEXTAREA. Build any new editable surface on one of those and it inherits the keyboard-shortcut guard.
- **Keep `cancelEdit()`'s ordering.** In `overlay.ts`, setting `contentEditable = false` can fire `blur` synchronously, which re-enters `cancelEdit`. Clear shared state before touching the box.
- **The theme toggle (UI-09) has three states: Auto, Light and Dark.** "Auto" (follow the OS) must stay a real, reachable state, not just the implicit default.
- **Compare (UI-05)** always shows the document as first opened (`UndoRedoJournal.original_bytes`) as "before", not the state before the last edit. Per-edit diffs are FNT-12. The diff overlay's per-channel tolerance of 24 (`CHANNEL_TOLERANCE` in `compare.ts`) is empirical and unrelated to `engine/verify.py`'s `DEFAULT_TOLERANCE`.
- **The history panel (UI-04)** shows what each Op requested, not the `EditResult` it produced. Tier, confidence and verification aren't in `GET .../history`.
- **Polyfills.** `polyfills.ts` and `pdf.worker.ts` add `Map`/`WeakMap.prototype.getOrInsertComputed` for browsers that lack it. The Playwright Chromium in one build sandbox did.

## Tests

- **Wait for the last effect, not the first.** After a document-changing action, `reloadDocument()` re-renders the page before it refreshes the history panel. A test that then presses a key has to wait for the state that key depends on, such as the Redo button being enabled. `page.click()`'s actionability wait hides this race for clicks; `page.keyboard.press()` never does.
- **Never compare raw PDF bytes.** PyMuPDF's `Document.tobytes()` puts a new random `/ID` in the output on every call, even with no changes. Compare parsed content instead: `get_text()`, the page count, or rendered PNGs.
- **The UI tests need a built `web/dist`.** Without one they skip. Under `tools/gate.py` (`PDFWORKERZ_REQUIRE_WEB=1`) they fail instead. The skip used to be a `pytestmark` in `tests/web/conftest.py`, which pytest ignores, so it never applied.

## Tooling and dependencies

- **Run checks through `tools/gate.py`.** It runs ruff check and ruff format (two separate checks), and mypy scoped to `engine cli tools server`. The test files aren't strictly typed, so mypy over the whole tree shows about 240 errors. Python tools run as `sys.executable -m <tool>`, never from PATH.
- **Check the oldest supported Python.** `python tools/gate.py --python .venv311/Scripts/python.exe --job types --job tests` runs mypy and the suite under Python 3.11. `.venv311` is created with `uv` (see README).
- **pdfjs-dist is pinned to 6.3.289** (`web/package.json`). Versions from 5.6.83 up to, but not including, 6.2.108 have a public high-severity CVE. The gate's `npm audit` step re-checks the pin on every run.

## Security

- **CORS allows loopback origins only**, with any port (a regex in `server/app.py`). This is safe only because auth is a header a browser never attaches on its own. Revisit it before adding any cookie-based or credentialed auth.
