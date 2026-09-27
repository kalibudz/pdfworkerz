# PDFWorkerz

A free, open-source, offline PDF editing worker for engineers. It edits, formats, organizes, converts and secures PDFs, matching the feature sets of iLovePDF and Nitro PDF Pro and adding more. It uses **zero AI tokens**: every operation is deterministic code.

Its flagship capability is **style-faithful text editing**. The worker identifies each span's font, size, color, spacing, baseline and render mode, so a changed word looks exactly like the original. Every edit is checked with a before/after pixel diff.

You tell it what to do in one of two ways:

- **Click-to-edit** directly on the page, or
- **Plain-English commands**, parsed by a fixed grammar with no AI:
  `replace "Rev C" with "Rev D" on all pages match style`

## Status

**Phase P0 is complete:** the specification, live tracker, CI workers and session protocol. The engine is built phase by phase (P1–P9); see [PROGRESS.md](PROGRESS.md).

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
