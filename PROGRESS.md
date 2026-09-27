# Progress

Feature-level status lives in [`tracker/features.json`](tracker/features.json) and is set only by the evidence gate. This file records phase sign-offs and session notes.

## Phase checklist

| Phase | Scope | Status | Reviewer sign-off |
|---|---|---|---|
| P0 | Spec, tracker, CI workers, session protocol | ✅ Done: 5/5 features proven by tests | Pending: first green GitHub Actions run |
| P1 | Engine core, inspection, encryption, repair, CLI | ✅ Done: 17/17 features proven by tests | Self-reviewed this session (see below); independent reviewer sign-off pending |
| P2 | Font identification & style-matched text editing | 🔶 In progress: 13/20 features proven by tests (FNT-01..08, EDT-01/02/03/04/06) | |
| P3 | Web UI with click-to-edit | Planned | |
| P4 | Command bar & recipes | Planned | |
| P5 | Organize, page design, annotate, document structure | Planned | |
| P6 | Forms, signatures, security, redaction | Planned | |
| P7 | OCR, scans, conversions | Planned | |
| P8 | Optimize, compare, accessibility, batch, extras | Planned | |
| P9 | Packaging & documentation | Planned | |

A phase is complete when all of its features are **done** through the evidence gate, CI is green on `main`, and the reviewer agent has signed off here.

## Standing review items

- [ ] Re-verify the iLovePDF and Nitro PDF Pro parity tags against the vendors' current feature pages before the first release.
- [ ] Pin engine dependency versions in `pyproject.toml` when P1 starts, and add API-contract tests for every library call.

## Session log

### 2026-09-27 (cont.) — P2 style-matched text editing

- `engine/fonts/style.py`, `classify.py`, `coverage.py`, `match.py`, `merge.py`,
  `resolve.py`: the full font-intelligence pipeline (FNT-01..08) -- per-span style
  and per-glyph text-state extraction, font classification and name-based style
  fingerprinting, glyph-coverage checking, cross-platform font lookup with a
  bundled fallback family, metric-similarity ranking with a confidence score, and
  glyph-borrow merging into a fresh subset -- feeding a single `resolve_font()`
  decision (exact / approximate / fallback) used by every edit.
- `engine/edit.py` + `engine/ops/text.py`: `ReplaceTextOp` (EDT-02), `DeleteTextOp`
  (EDT-04), `RestyleTextOp` (EDT-06), `InsertTextOp` (EDT-03), and the shared
  `replace_span_text`/`insert_text_near` primitives underneath (EDT-01). Redacts
  the target region, then draws the replacement through the resolved font at
  positions computed from Tc/Tw/Tz/Ts/Tr, with rotation support. `require_tier`
  lets an Op refuse a weak font match outright, standing in for the UI approval
  step SPEC.md describes until one exists.
- **A deliberate, documented departure from SPEC.md's literal Tier 1** ("rewrite
  the Tj/TJ operands in place"): PyMuPDF cannot draw through a font program with
  no `cmap` table, which is exactly what its own subsetting produces (confirmed:
  inserting through raw extracted subset bytes drew glyphs that could not be
  read back as the text they were meant to be). So "exact" match always goes
  through a freshly merged subset cut from the *same*, full font instead --
  identical typeface, different embedding mechanism.
- Five more real bugs found by testing against actual PyMuPDF/pikepdf/fontTools
  output rather than assumption, each fixed before being marked done:
  1. `extract_metrics` required a font's `post` table unconditionally; PyMuPDF's
     subsetting strips it (like `cmap`), so Tier 3 could never actually fire.
  2. `resolve_font`'s Tier 3 branch measured metrics from a variable that was
     always None by the time that line ran (the other branch had already
     returned) -- Tier 3 was unreachable code.
  3. `check_coverage` misclassified a zero-contour glyph (space, by design) as
     "stripped by subsetting."
  4. `_cmap_coverage` crashed outright on a TrueType Collection's raw bytes
     (`TTLibFileIsCollectionError`), reached whenever a `.ttc` system font was a
     ranking candidate.
  5. Per-character `insert_text` calls fragmented texttrace's spans into one
     span per character, silently breaking any edit chained after another (a
     second replace, or an insert referencing already-edited text, could no
     longer find its target). Fixed by drawing default-spacing, unrotated text
     in a single call, falling back to per-character drawing only when Tc/Tw/Tz
     or rotation actually require it.
- 216 tests total (94.6% coverage); ruff, mypy --strict and bandit clean;
  pip-audit clean.
- FNT-01..FNT-08 and EDT-01/02/03/04/06 moved to "done" (35/161 total). Still
  open in P2: FNT-09 (kerning/ligature), FNT-10 (fit-to-width for a
  length-mismatched replacement), FNT-11 (reflow), FNT-12 (wiring the existing
  pixel-diff harness into the edit pipeline itself), FNT-13 (missing-ToUnicode
  recovery), FNT-14 (CJK/RTL/vertical), FNT-15 (Type3 editing).

### 2026-09-27 — P1 engine core

- Pinned runtime deps (pymupdf 1.28.2, pikepdf 10.14.0, fonttools 4.66.0, pydantic 2.13.5,
  typer 0.27.2, numpy 2.5.3) in `pyproject.toml`; every library call used below was verified
  against the installed version with a short REPL check before being relied on in code.
- `engine/document.py`: `Document.open/from_bytes/save/render_page/iter_pages/inspect`
  (COR-01, COR-02, COR-06, COR-07, COR-08, COR-09). Atomic same-path overwrite (temp file +
  `Path.replace`), auto incremental-vs-full save mode (signed documents get incremental),
  `filetype="pdf"` forced so PyMuPDF never silently opens a non-PDF via extension sniffing,
  and a `RepairFailedError`/`NotAPdfError` split so "too damaged to repair" is distinguished
  from "never a PDF" (OPT-06).
- `engine/security.py` + `engine/inspect.py`: encryption/permission reporting for every
  revision (RC4-40/128, AES-128, AES-256) and owner-only-restricted files (SEC-01, SEC-02,
  SEC-03), certificate-encryption detection via pikepdf's `PdfError` message on an
  unsupported filter (SEC-07), and the full COR-03 inspection report (fonts, images, forms,
  signatures, layers, bookmarks).
- `engine/ops/base.py` + `engine/ops/journal.py`: the typed, registered Op model with a
  JSON-schema export (`docs/ops.schema.json`, kept in sync by `tools/gen_ops_schema.py`) and
  a snapshot-based undo/redo journal (COR-04, COR-05).
- `engine/verify.py`: the pixel-diff harness (INF-07), reused later by FNT-12.
- `cli/main.py`: `pdfworkerz inspect/render/repair/version`, built on the same Op classes the
  future UI and command bar will use (COR-10).
- `tests/corpus/build_corpus.py`: the golden PDF corpus (INF-06) — plain, multi-page and
  1,000-page docs; all four encryption revisions; an owner-only-restricted file; a
  repairable (tail-truncated) and an unrepairable (header-only) broken file; a hand-built
  incremental-update PDF announcing `Adobe.PubSec` for certificate-encryption detection; a
  form+signature-field file; and an optional-content-layer file.
- 130 tests, all passing; 93% coverage of `tools/engine/cli`; ruff, mypy --strict and bandit
  all clean; pip-audit found no known vulnerabilities in the pinned dependencies.
- All 17 P1 features moved to `done` by `tools/update_tracker.py --write` (evidence gate),
  bringing the total to 22/161.
- Two things learned the hard way, worth remembering: (1) `Document.save()` needs
  `encryption=PDF_ENCRYPT_KEEP` or PyMuPDF silently strips encryption on save; (2) PyMuPDF
  refuses a full (non-incremental) save back to the path it opened from, so an in-place
  overwrite has to go through a temp file and `Path.replace`.
- Next: P2 (font identification & style-matched text editing) — the flagship feature set.

### 2026-09-26 — P0 bootstrap

- Wrote SPEC.md (16 sections, 161 features across 21 categories, parity matrix, roadmap).
- Built the evidence gate (`tools/update_tracker.py`), the spec generator (`tools/gen_spec_catalog.py`) and the token look-ahead tool (`tools/session_budget.py`).
- Added the pytest evidence plugin (`tests/conftest.py`): 34 tests pass, with 93% coverage of `tools/`.
- Built the live tracker (`tracker/`) in React + Vite, plus a single-file artifact build.
- Added the CI workflow: lint, types, 3-OS × 2-Python test matrix, security, spec sync, tracker build.
- Next: P1, starting with INF-06 (golden corpus generator) and COR-01 (open/parse).
