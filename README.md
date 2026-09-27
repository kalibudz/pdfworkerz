# PDFWorkerz

A free, open-source, offline PDF editing worker for engineers. It edits, formats, organizes, converts and secures PDFs, matching the feature sets of iLovePDF and Nitro PDF Pro and adding more. It uses **zero AI tokens**: every operation is deterministic code.

Its flagship capability is **style-faithful text editing**. The worker identifies each span's font, size, color, spacing, baseline and render mode, so a changed word looks exactly like the original. Every edit is checked with a before/after pixel diff.

You tell it what to do in one of two ways:

- **Click-to-edit** directly on the page, or
- **Plain-English commands**, parsed by a fixed grammar with no AI:
  `replace "Rev C" with "Rev D" on all pages match style`

## Try it on a real PDF

Phases P0, P1 and P2 (the flagship font-identification and style-matched editing engine) are complete. That means real editing already works from the command line:

```bash
python -m venv .venv && .venv/Scripts/pip install -e .   # use .venv/bin/pip on macOS/Linux

pdfworkerz inspect mydoc.pdf                              # what's in it: fonts, encryption, forms, ...
pdfworkerz render mydoc.pdf 0 --out page0.png             # render a page
pdfworkerz repair damaged.pdf --out fixed.pdf             # fix a broken PDF

pdfworkerz replace mydoc.pdf "old text" "new text" --out edited.pdf
pdfworkerz delete mydoc.pdf "text to remove" --out edited.pdf
pdfworkerz restyle mydoc.pdf "text" --size 14 --color 1,0,0 --out edited.pdf
pdfworkerz insert mydoc.pdf "new text" --position 72,700 --reference "existing text" --out edited.pdf
```

Every edit command prints which font-match tier it used (exact / approximate / fallback) and whether the result needs a look — `--require-tier exact` refuses to proceed on anything weaker. `--out` is optional; without it, the edited copy goes to `<name>.edited.pdf` next to the original, which is never touched (`--overwrite` writes back to it explicitly, when that's what you want).

## Local API server (P3, in progress)

`pdfworkerz serve` starts a local-only HTTP server (COR-11) that the future web UI talks to — the same `Op` classes as the CLI, over JSON:

```bash
pdfworkerz serve --port 8000
```

It binds to `127.0.0.1` only and prints a random session token; every request must send it back in an `X-Session-Token` header (a request without it gets `401`). Roughly:

```bash
curl -s -X POST http://127.0.0.1:8000/documents \
  -H "X-Session-Token: <token>" -H "Content-Type: application/json" \
  -d '{"path": "mydoc.pdf"}'
# -> {"document_id": "...", "page_count": 3, "is_encrypted": false, "is_repaired": false}

curl -s -X POST http://127.0.0.1:8000/documents/<document_id>/ops \
  -H "X-Session-Token: <token>" -H "Content-Type: application/json" \
  -d '{"op": "replace_text", "match": "old text", "replacement": "new text"}'
```

`GET /documents/{id}/pages/{n}/render` returns a PNG; `/undo`, `/redo` and `GET /history` drive the same undo/redo journal COR-05 built. The web UI itself (pdf.js canvas, click-to-edit overlay, inspector, command bar) is the rest of P3 and hasn't been built yet — for now, testing beyond the CLI means this API directly, or scripting against `engine.document.Document` and `engine.ops.text`.

## Status

| Document | What it is |
|---|---|
| [SPEC.md](SPEC.md) | Full product and technical specification, with 161 catalogued features |
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

## Development checks (same as CI)

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"          # use .venv/bin/pip on macOS/Linux
pytest --feature-results --cov                  # tests + evidence for the tracker
python tools/update_tracker.py --write          # statuses follow test evidence
python tools/update_tracker.py --check          # fails on any unproven "done"
python tools/gen_spec_catalog.py --check        # SPEC.md in sync with features.json
python tools/session_budget.py --remaining 600000   # which task fits this session
ruff check . && mypy tools && bandit -q -r tools
```

A feature is marked **done** only when tests linked to it with `@pytest.mark.feature("ID")` pass. Nobody sets that status by hand.

## License

[AGPL-3.0-or-later](LICENSE). Free to use, study, modify and share.
