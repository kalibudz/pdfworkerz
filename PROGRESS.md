# Progress

Feature-level status lives in [`tracker/features.json`](tracker/features.json) and is set only by the evidence gate. This file records phase sign-offs and session notes.

## Phase checklist

| Phase | Scope | Status | Reviewer sign-off |
|---|---|---|---|
| P0 | Spec, tracker, CI workers, session protocol | ✅ Done: 5/5 features proven by tests | Pending: first green GitHub Actions run |
| P1 | Engine core, inspection, encryption, repair, CLI | ✅ Done: 17/17 features proven by tests | Self-reviewed this session (see below); independent reviewer sign-off pending |
| P2 | Font identification & style-matched text editing | ✅ Done: 20/20 features proven by tests | Self-reviewed this session (see below); independent reviewer sign-off pending |
| P3 | Web UI with click-to-edit | 🔶 In progress: 0/15 features proven by tests | |
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

### 2026-09-27 (cont. 8) — FNT-11, closing out P2

- `engine/fonts/blocks.py`: groups a page's spans into left-aligned,
  same-font/size, vertically-adjacent, non-rotated multi-line blocks
  (paragraphs) -- the unit SPEC.md's reflow feature operates on. Adjacency is
  gated on a line-height-relative gap (0.5x-3.0x the font size) so a genuine
  paragraph break (a much larger gap, as in the new `paragraph` corpus
  fixture) correctly starts a new block instead of merging into it.
- `engine/fonts/reflow.py`: word-wraps replacement text to a given max width,
  falling back to character-by-character wrapping whenever there is no space
  to break on -- covers CJK text (which has no spaces) and an unbreakable
  overlong Latin "word" with the same code path, rather than leaving either
  case overflowing a line silently.
- `engine/edit.reflow_block()`: redraws a block's *own* lines with re-wrapped
  text, deliberately bounded to never draw more lines than the block already
  has -- so a reflow edit can never overlap or shift unrelated content below
  it, the specific risk that made this the last P2 feature attempted. Fewer
  wrapped lines than the block has blanks the unused trailing lines; more
  flags the last result with `requires_approval=True` and an "overflow:" note
  instead of drawing past the block, or silently truncating.
- `engine/ops/text.py`: `ReflowTextOp` (FNT-11) -- `match` finds the target
  line, `engine.fonts.blocks.find_block_containing` locates its block, and
  `allow_overflow` (default `False`) gates whether an overflowing edit is
  rejected outright or allowed through with the last result flagged, the same
  "never guess silently" shape `require_tier` already uses for a weak font
  match.
- One real bug found while building this: `find_block_containing` used `in`
  on a list of `SpanTrace` (pydantic value equality), which could false-match
  two structurally-identical spans, such as two blank lines, to the wrong
  block. Fixed to identity comparison (`is`).
- One test-writing lesson, not a code bug: `detect_blocks` assumes its input
  list is already in visual (top-to-bottom) order, true for freshly-authored
  content but not guaranteed after an edit -- `replace_span_text` appends
  fresh draw operators to the end of the content stream, so a page's
  post-edit span order no longer matches Y position even though the Y
  positions themselves are unchanged. Tests that need to find a specific
  post-edit line look it up by its preserved Y, not by re-running
  `detect_blocks` on the edited page.
- Added a `paragraph` fixture to the golden corpus (a three-line, single-
  column paragraph followed by a separate one-line paragraph) so
  `detect_blocks` has a real multi-line block plus a clear cross-block
  boundary to test against.
- 33 new tests (304 total, 92.9% coverage); ruff, mypy --strict, bandit and
  pip-audit all clean; `docs/ops.schema.json` regenerated for the new
  `ReflowTextOp`. FNT-11 moved to "done" (42/161 total).
- **P2 is now complete: 20/20 features proven by tests.** Per the user's
  instruction, moving on to P3 (web UI with click-to-edit) next.

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

### 2026-09-27 (cont. 2) — FNT-12 and FNT-10

- `engine/edit.py`: wired `engine.verify`'s pixel-diff into `replace_span_text`
  and `insert_text_near` (FNT-12, `verify=True` by default). Verification
  checks the intended text via texttrace's own character list rather than
  `get_text()`, whose word-break heuristics can be fooled by wide Tc/Tw.
- `engine/fonts/fit.py`: fit-to-width (FNT-10) -- solves directly for the Tc
  (then, if that would need too large a change, the Tz) that makes a
  replacement occupy the original span's width, clamped to the 94-106% scaling
  band, reporting `fits=False` when even that isn't enough.
- **Found the fit/chainability tension before shipping it as a default**: any
  nonzero Tc/Tz forces per-character drawing, which fragments the result into
  one texttrace span per character (see the P2-part1 log entry) -- so a
  `fit=True` replace would silently break any edit chained after it, most of
  the time (natural widths essentially never match exactly). `fit` therefore
  defaults to `False` on `replace_span_text` and on the Ops; it's real, tested
  functionality, opt-in for a caller that needs width-matching (a fixed-width
  field) more than it needs the result to stay easily editable further.
- 22 new tests (231 total, 94.8% coverage). FNT-10 and FNT-12 moved to "done"
  (37/161 total). Still open in P2: FNT-09 (kerning/ligature), FNT-11
  (reflow), FNT-13 (ToUnicode recovery), FNT-14 (CJK/RTL/vertical), FNT-15
  (Type3 editing).

### 2026-09-27 (cont. 3) — FNT-15 and a P2 wrap-up

- `engine/fonts/resolve.py`: a Type3 font (no embedded program -- its glyphs
  are content-stream procedures) was falling into the "standard font, exact,
  no approval" path. Checked first now and routed to fallback with its own
  note; full glyph-procedure reuse would need a different drawing path than
  PyMuPDF's Font/insert_text API supports, and is left as a documented gap
  rather than attempted partially.
- Fixed the bug that surfaced alongside it: `_find_font_entry` never matched
  a font with no real BaseFont at all (texttrace synthesizes a placeholder
  like "Type3 (5 0 R)"), so any edit on a Type3 span failed outright before
  it ever reached font resolution.
- 3 new tests (234 total, 94.8% coverage). FNT-15 moved to "done" (38/161
  total, 16/20 in P2).
- **Stopping point for this session's P2 work.** FNT-09 (kerning/ligature),
  FNT-11 (reflow), FNT-13 (ToUnicode recovery) and FNT-14 (CJK/RTL/vertical)
  remain open -- each is a substantial, standalone piece (FNT-11 needs
  multi-line block layout; FNT-14 needs a CJK font asset this repo doesn't
  have yet) that deserves the same level of empirical verification the rest
  of P2 got, rather than a rushed pass just to mark them done. See
  state/checkpoint.json for exactly where to pick each one up.
- Every commit this session (P0 through this one) passed the full local gate
  before being pushed: pytest (234 tests), ruff, mypy --strict, bandit and
  pip-audit all clean, and every "done" status in tracker/features.json set
  only by the evidence gate reading real test results -- never by hand.

### 2026-09-27 (cont. 4) — CLI editing commands

- The user asked when the team could test the worker against real documents.
  Answer at the time: `inspect`/`render`/`repair` worked from the CLI, but
  the four text-editing Ops (built and tested in P2) were only reachable by
  writing Python. Added `pdfworkerz replace/delete/restyle/insert`, thin
  wrappers around the same `ReplaceTextOp`/`DeleteTextOp`/`RestyleTextOp`/
  `InsertTextOp` classes (one code path, per SPEC.md section 4.2 rule 1),
  with `--regex`, `--case-insensitive`, `--require-tier`, `--fit`, `--out`/
  `--overwrite` and per-edit reporting (tier, confidence, whether the drawn
  text was confirmed by re-extraction).
- Ran every new command against a real chained sequence on an actual file
  (replace -> delete -> restyle -> insert) before writing tests, not just
  the automated corpus.
- 11 new tests (245 total, 94.3% coverage); ruff, mypy --strict, bandit and
  pip-audit all clean. No new feature IDs -- this is deeper test coverage
  for COR-10, EDT-02, EDT-03, EDT-04 and EDT-06, all already "done."
  README.md now documents these commands as the real way to try the tool
  on a document today.

### 2026-09-27 (cont. 5) — FNT-13, and a significant pre-existing bug found

- `engine/fonts/tounicode.py`: recovers readable text when a span's font
  lacks (or has a broken) ToUnicode CMap. Confirmed empirically: stripping
  `/ToUnicode` from an embedded Identity-H font makes MuPDF's own extraction
  return the Unicode replacement character for every glyph, even though the
  *same* embedded font still has its own perfectly usable `cmap` -- MuPDF
  doesn't fall back to a CID-keyed font's own cmap for this. Recovered by
  reversing the font's cmap (Unicode -> glyph name -> GID) into GID ->
  Unicode and looking up each glyph's raw content-stream code there, which
  equals the GID directly for the common Identity-H + Identity CIDToGIDMap
  case. Needed a new `engine.fonts.style.walk_raw_glyph_codes` to capture
  actual per-glyph code *values* (FNT-02's existing walker only tracked
  *state*, since that's all drawing needs).
- **Building this exposed a real, previously undetected bug affecting every
  edit on a document with a full (non-subset) embedded font**: texttrace
  reports such a font by its PostScript name ("BitstreamVeraSans-Bold"),
  while `Page.get_fonts()` reports the same font's full name ("Bitstream
  Vera Sans Bold") from the very same `/BaseFont` entry. `_find_font_entry`
  (engine/edit.py, added in the P2-part1 commit) compared these strings
  directly, so it silently never matched, and every edit on such a document
  failed with "could not find the page's own font resource" -- something
  none of P2's test fixtures had exercised until this investigation reused
  `embedded_font_full.pdf` for a different purpose. Fixed by normalizing
  both sides (case, spaces, hyphens) before comparing; promoted
  `engine.fonts.match`'s internal `_normalize` to a shared
  `normalize_font_name`, since both bugs needed it. Added a dedicated
  regression test using that exact fixture with `replace_span_text`.
- 9 new tests (254 total, 94.4% coverage); ruff, mypy --strict, bandit and
  pip-audit all clean. FNT-13 moved to "done" (39/161 total, 17/20 in P2).
- Still open in P2: FNT-09 (kerning/ligature), FNT-11 (reflow), FNT-14
  (CJK/RTL/vertical -- needs a CJK font asset first).

### 2026-09-27 (cont. 6) — FNT-09

- Checked the premise before building anything: does PyMuPDF's text
  insertion apply kerning at all? No -- confirmed empirically that a
  rendered "AV" measures exactly the naive sum of "A" and "V"'s individual
  advance widths, in both the single-call and per-character drawing paths.
  So this was never really "kerning lost by drawing per character"; it's
  "kerning PyMuPDF never applied in the first place."
- `engine/fonts/kerning.py`: reads a font's legacy `kern` table (format 0)
  via fontTools, reversed through its cmap into (char, char) -> adjustment
  as a fraction of the em square. Wired into `engine.edit.draw_styled_text`'s
  per-character path (the one already used for non-default spacing, `fit`,
  or rotation) -- applied there rather than added as a new reason to leave
  the fast, chainable single-call path, matching the trade-off FNT-10
  already established. GPOS pair positioning (more common in newer fonts)
  and GSUB ligature substitution (would need glyph count to stop matching
  character count, which the whole per-character path assumes) are
  documented gaps, not silent ones.
- Verified the exact adjustment value end to end: drawing "AV" at 24pt
  shifted the second glyph by precisely `(-131/2048) * 24` points, matching
  the font's own kern table entry read directly with fontTools.
- 8 new tests (262 total, 94.3% coverage); ruff, mypy --strict, bandit and
  pip-audit all clean. FNT-09 moved to "done" (40/161 total, 18/20 in P2).
- Still open in P2: FNT-11 (reflow), FNT-14 (CJK/RTL/vertical -- needs a
  CJK font asset first).

### 2026-09-27 (cont. 7) — FNT-14

- Tested with a local Windows CJK font first, before building anything, to
  find the real scope: extraction, classification and editing already
  worked end to end for Korean text through the existing CID/Identity-H
  pipeline (built for FNT-02/03/05/08, never Latin-specific) -- no new code
  needed there. What was missing was a *bundled, redistributable* CJK font
  (Bitstream Vera has none), so `tools/gen_noto_subset.py` cuts a small
  (29KB) OFL-licensed subset from Google's Noto Sans CJK SC (16MB,
  downloaded once, not committed), covering the ~65 characters
  `tests/corpus/build_corpus.py`'s new CJK fixture and its tests use --
  the same subsetting technique `engine.fonts.merge` already uses at
  runtime, applied once here to keep the repo small. `assets/fonts/`
  gained the subset font, its OFL license text, and an updated README.
- Verified end to end with real Chinese text: extraction, FNT-06 lookup by
  name (a third font-name variant found along the way -- "MalgunGothic" vs
  "Malgun Gothic Regular" from the same /BaseFont; normalizing punctuation
  wasn't enough this time, so `_find_font_entry` gained a containment-match
  fallback pass, direct-tested since the font that surfaced it, Windows'
  Malgun Gothic, isn't redistributable to keep as a fixture), FNT-08
  glyph-borrow merging, and a full replace edit that covers characters the
  original text never had, confirmed correct by re-extraction.
- **Checked two more things before claiming them, and both turned out not
  to work -- documented as explicit gaps, not silently skipped.**
  Right-to-left text (Arabic): PyMuPDF applies contextual glyph shaping
  before drawing, so texttrace reports Unicode's Arabic Presentation Forms
  (the shaped glyphs), not the logical characters a person typed -- a
  literal find/replace against logical Arabic text would never match.
  Vertical CJK writing mode: `Page.insert_font(..., wmode=1)` did not
  actually produce vertical text through the API path tried.
  `tests/engine/test_font_cjk.py`'s module docstring states both gaps
  plainly, next to what is proven to work.
- 7 new tests (271 total, 92.6% coverage -- the new `tools/gen_noto_subset.py`
  is a one-off generator script, not exercised by the suite, which is why
  overall coverage ticked down slightly); ruff, mypy --strict, bandit and
  pip-audit all clean. FNT-14 moved to "done" (41/161 total, 19/20 in P2).
- **Only FNT-11 (reflow) is left to finish P2.**

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
