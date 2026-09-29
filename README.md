# PDFWorkerz

A free, open-source, offline PDF editing worker for engineers. It edits, formats, organizes, converts and secures PDFs, matching the feature sets of iLovePDF and Nitro PDF Pro and adding more. It uses **zero AI tokens**: every operation is deterministic code.

Its flagship capability is **style-faithful text editing**. The worker identifies each span's font, size, color, spacing, baseline and render mode, so a changed word looks exactly like the original. Every edit is checked with a before/after pixel diff.

You tell it what to do in one of two ways:

- **Click-to-edit** directly on the page, or
- **Plain-English commands**, parsed by a fixed grammar with no AI:
  `replace "Rev C" with "Rev D" on all pages match style`

## Try it on a real PDF

Phases P0 through P3 are complete: the flagship font-identification and style-matched editing engine (P2), plus the browser UI and the rest of the page-editing tools (P3). Real editing works from the command line and in the browser.

```bash
python -m venv .venv && .venv/Scripts/pip install -e .   # use .venv/bin/pip on macOS/Linux

pdfworkerz inspect mydoc.pdf                              # what's in it: fonts, encryption, forms, ...
pdfworkerz render mydoc.pdf 0 --out page0.png             # render a page
pdfworkerz repair damaged.pdf --out fixed.pdf             # fix a broken PDF

# Text (style-matched)
pdfworkerz replace mydoc.pdf "old text" "new text" --out edited.pdf
pdfworkerz replace mydoc.pdf --match "- End -" "Fin"      # --match for text starting with "-"
pdfworkerz delete mydoc.pdf "text to remove" --out edited.pdf
pdfworkerz restyle mydoc.pdf "text" --size 14 --color 1,0,0 --bold --font "Times" --out edited.pdf
pdfworkerz insert mydoc.pdf "new text" --position 72,700 --reference "existing text" --out edited.pdf
pdfworkerz insert mydoc.pdf "APPROVED" --position 72,700 --font "Courier" --size 20 --bold   # explicit style
pdfworkerz fonts                                            # font families you can choose
pdfworkerz fonts --research                                 # fonts edits could only approximate
pdfworkerz copy-style mydoc.pdf --source "Heading" --target "plain text"      # format painter
pdfworkerz move-block mydoc.pdf --match "a line of the paragraph" --dy 40 --width 300
pdfworkerz spellcheck mydoc.pdf                            # offline, Hunspell en_US

# Links, images, shapes
pdfworkerz add-link mydoc.pdf --over "our website" --uri https://example.com
pdfworkerz links mydoc.pdf 0 / remove-link mydoc.pdf 0
pdfworkerz insert-image mydoc.pdf logo.png --rect 72,72,172,122
pdfworkerz images mydoc.pdf 0 / move-image / crop-image / replace-image / delete-image
pdfworkerz draw-shape mydoc.pdf rect --points "72,300 272,400" --fill 1,1,0
pdfworkerz shapes mydoc.pdf 0 / edit-shape / delete-shape

# Plain-English commands and recipes (P4)
pdfworkerz edit mydoc.pdf --do 'replace "2024" with "2025" on all pages' --do 'set bold for "Total"'
pdfworkerz edit mydoc.pdf --do 'delete "DRAFT" on odd pages' --dry-run      # show what would change
pdfworkerz edit mydoc.pdf --do '...' --save-recipe fixes.yaml               # keep the steps
pdfworkerz run fixes.yaml other.pdf --dry-run                               # replay on another file
```

Commands never guess: one that doesn't parse is refused with the closest valid forms ("did you mean …") and a syntax hint, and nothing changes. The grammar covers `replace`, `delete`, `insert … below/above/after/before "…"` or `at x, y`, `set <style> for …`, pages as `on page 3 | on pages 1-3,5 | on odd pages | on all pages`, targets as `"text"`, `/regex/` or `text`, and style words `bold`, `italic`, `size 11`, `color #cc0000`, `font "Times"`, `match style`. A recipe is a YAML/JSON list of the same Ops as `docs/ops.schema.json`; replaying one on the same input gives byte-identical output (for unencrypted files: AES uses fresh random IVs on every save).

Every text edit command prints which font-match tier it used (exact / approximate / fallback) and whether the result needs a look — `--require-tier exact` refuses to proceed on anything weaker. A replacement keeps a right-aligned or centered line's edge. `--out` is optional; without it, the edited copy goes to `<name>.edited.pdf` next to the original, which is never touched (`--overwrite` writes back to it explicitly, when that's what you want).

## Browser UI

`pdfworkerz serve` starts a local-only HTTP server (COR-11); the web UI in [`web/`](web/README.md) talks to it:

```bash
pdfworkerz serve --port 8000                 # prints a session token
cd web && npm ci && npm run build && npm run preview
# open the printed URL with ?token=<token>&api=http://127.0.0.1:8000
```

Type commands in the **command bar** under the toolbar (press `/`): suggestions show what can come next, Enter previews how many matches would change, and Enter again (or **Apply**) does it; the bar also exports the session as a recipe and runs a recipe after a dry run. Click any text to edit it in place, in its detected style. The inspector shows the font, size, color and match confidence, and offers **Change style…** (font, bold, italic, size, color), **Copy style** (format painter) and the text's **links**. Drag the handles beside selected text to move or re-wrap its paragraph. Images and shapes can be selected, dragged, resized, restyled, cropped (images) and deleted. The toolbar adds text (**Text…**, then click where it goes), inserts images, draws lines/rectangles/ellipses, toggles **Spelling** (underlines, with suggestions), and opens a before/after **Compare** view. Every change is undoable from the history strip (Ctrl+Z / Ctrl+Shift+Z).

The server binds to `127.0.0.1` only, and every request must carry the session token in an `X-Session-Token` header (`401` otherwise). The API is the same `Op` classes as the CLI, over JSON: `POST /documents` opens a file, `POST /documents/{id}/ops` applies any Op (journaled, undoable), and read-only `GET .../pages/{n}/spans|links|images|shapes|spelling|render` routes describe a page. `docs/ops.schema.json` lists every Op.

## Status

| Document | What it is |
|---|---|
| [SPEC.md](SPEC.md) | Full product and technical specification, with 163 catalogued features |
| [tracker/features.json](tracker/features.json) | Single source of truth for feature status |
| [tracker/src/FeatureTracker.jsx](tracker/src/FeatureTracker.jsx) | Live tracker UI |
| [docs/SESSION_PROTOCOL.md](docs/SESSION_PROTOCOL.md) | Token look-ahead and resume procedure for build sessions |
| [state/checkpoint.json](state/checkpoint.json) | Where the build currently stands |

## Live tracker

```bash
cd tracker
npm install
npm run dev
```

The dev server hot-reloads whenever `features.json` or `state/checkpoint.json` changes. `npm run artifact` bundles a single self-contained HTML page. A published snapshot is at https://claude.ai/artifact/GSgFTQKmziP9ZzyyFiEG6m (private until shared); it is republished as statuses change.

## Development checks (the local gate)

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"          # use .venv/bin/pip on macOS/Linux
.venv/Scripts/python -m playwright install chromium
.venv/Scripts/python tools/gate.py              # every review worker: lint, types, spec sync, tests, security, tracker build
.venv/Scripts/python tools/gate.py --job lint   # just one job (repeatable); --list shows every step
.venv/Scripts/python tools/update_tracker.py --write     # statuses follow the evidence the gate's test run produced
.venv/Scripts/python tools/session_budget.py --remaining 600000   # which task fits this session
```

The gate must pass before every commit. `.github/workflows/ci.yml` runs the same jobs across Linux, Windows and macOS, but only when triggered by hand from the Actions tab: Actions minutes are billed on this private repository.

A feature is marked **done** only when tests linked to it with `@pytest.mark.feature("ID")` pass. Nobody sets that status by hand.

## License

[AGPL-3.0-or-later](LICENSE). Free to use, study, modify and share.
