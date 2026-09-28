# Progress

Feature-level status lives in [`tracker/features.json`](tracker/features.json) and is set only by the evidence gate. This file records phase sign-offs and session notes.

## Phase checklist

| Phase | Scope | Status | Reviewer sign-off |
|---|---|---|---|
| P0 | Spec, tracker, CI workers, session protocol | ✅ Done: 5/5 features proven by tests | First green GitHub Actions run: [run #17](https://github.com/kalibudz/pdfworkerz/actions/runs/36356246278), all 11 jobs, 2026-09-27 |
| P1 | Engine core, inspection, encryption, repair, CLI | ✅ Done: 17/17 features proven by tests | Self-reviewed this session (see below); independent reviewer sign-off pending |
| P2 | Font identification & style-matched text editing | ✅ Done: 20/20 features proven by tests | Self-reviewed this session (see below); independent reviewer sign-off pending |
| P3 | Web UI with click-to-edit | ✅ Done: 15/15 features proven by tests | Self-reviewed (see the 2026-09-28 P3 completion entry); independent reviewer sign-off pending |
| P4 | Command bar & recipes | Planned | |
| P5 | Organize, page design, annotate, document structure | Planned | |
| P6 | Forms, signatures, security, redaction | Planned | |
| P7 | OCR, scans, conversions | Planned | |
| P8 | Optimize, compare, accessibility, batch, extras | Planned | |
| P9 | Packaging & documentation | Planned | |

A phase is complete when all of its features are **done** through the evidence gate, the local gate (`python tools/gate.py`) is green on `main`, and the reviewer agent has signed off here.

## Standing review items

- [ ] Re-verify the iLovePDF and Nitro PDF Pro parity tags against the vendors' current feature pages before the first release.
- [ ] Pin engine dependency versions in `pyproject.toml` when P1 starts, and add API-contract tests for every library call.

## Session log

### 2026-09-28 (cont. 2) — verification moves from GitHub Actions to a local gate; PR #1 merged locally

- **Independent re-verification** of the P3 branch on a second machine
  (Windows 11, Python 3.14.3, Node 24), from a fresh dependency install:
  546 tests passed, including the Playwright suite against a fresh
  `web/dist`, at 94.21% coverage. ruff, ruff format, mypy --strict,
  bandit, pip-audit, npm audit, the SPEC catalog and ops-schema checks
  were all clean. `update_tracker.py --write` found no status changes:
  57/161 features, 0 unsupported claims. The previous entry's claims
  reproduce.
- **All branches consolidated into `main`.** `claude/pdf-workerz-editing-c7fz6n`
  already contained every commit from `claude/epic-davinci-vu56x5` and
  `main`, so `main` was fast-forwarded to it (this merges PR #1).
- **GitHub Actions is now manual-only.** Its minutes are billed on this
  private repo, which is what stopped every job after the P3 merge.
  `ci.yml` triggers only on `workflow_dispatch` and still mirrors all six
  jobs across three OSes for an occasional on-demand run.
- **`tools/gate.py` is the everyday gate.** It runs the same checks as
  `ci.yml` locally (lint, types, spec sync, tests with the evidence gate,
  security, tracker build), keeps going past a failure, and prints a
  summary. Python tools run as `sys.executable -m <tool>`, never from PATH.
- **INF-03's evidence moved to the gate.** Its old test required CI to run
  on push and pull request; the new tests prove the gate defines every job,
  enforces the evidence and spec checks, reports failures without stopping,
  uses no AI services, and that `ci.yml` cannot run without a manual
  trigger. SPEC.md, SESSION_PROTOCOL.md and README.md now describe the gate.
- **Coverage lost:** only Windows and Python 3.14 are exercised routinely
  now, although `requires-python` is `>=3.11`. Recorded in the checkpoint's
  open questions.

### 2026-09-28 (cont.) — P3 complete: branches consolidated, EDT-05/07/08/09/10/11, real-document fixes

- **Consolidation.** The UI work (UI-01..UI-09) lived on
  `claude/epic-davinci-vu56x5`, which had never been merged, and `main` had
  four newer commits the UI branch lacked (Form XObject editing, bundled
  Roboto, font `meta` table). Merged both on `claude/pdf-workerz-editing-c7fz6n`
  (PR #1). Only bookkeeping files conflicted. The full gate, including the
  Playwright suite, passed on the merged tree.
- **EDT-07 format painter** (`CopyStyleOp`, `copy-style`, inspector "Copy style").
- **EDT-10 hyperlinks** (URI and go-to-page; `javascript:`/`file:`/`data:`
  refused). PyMuPDF caches a page's link list until `reload_page`.
- **EDT-05 move/resize paragraphs** (`MoveTextBlockOp`, drag handles). All
  old lines are redacted before any are drawn. Block lookup tries both
  stream order and visual order, which answers the open question about
  `detect_blocks`' input order after an edit.
- **EDT-08 images.** Per-placement edits via a pinpoint image-only
  redaction (never `delete_image(xref)`, which blanks every use).
  `insert_image(keep_proportion=True)` stretched in 1.28.2, so fitting is
  computed here. Crop really cuts the pixels. Pillow is now a dependency.
- **EDT-09 vector shapes.** Line-art redaction removes everything inside
  the area, so collateral paths are diffed and redrawn. The margin must be
  ~10x the stroke width (miter allowance), measured.
- **EDT-11 spell-check** (spylls + bundled SCOWL en_US). spylls 0.1.7's
  wheel installs a stray top-level `tests` package, which shadowed ours;
  `tests/` is now a regular package.
- **Real bugs found along the way:**
  - The click-to-edit overlay was vertically mirrored: MuPDF's y-down
    coordinates were fed to pdf.js's y-up conversion. The tests never
    clicked at the text itself, so they missed it.
  - The journal didn't roll back an Op that raised partway.
  - `pyproject.toml` omitted `engine.fonts` from its packages.
- **Real-document follow-ups closed:**
  - `--match`/`--replacement` for text starting with "-".
  - Right-aligned and centered lines keep their edge or center when
    replaced.
  - Cross-family metric confidence is scaled by 0.75 (the Delta-Book →
    Maiandra 0.98 case).
- 546 tests, 94.3% coverage; ruff, ruff format, mypy --strict, bandit,
  pip-audit and npm audit are clean locally.
- **CI could not confirm any of this.** From the merge commit on, every
  GitHub Actions job on this private repo failed within seconds, with no
  logs and no steps run. Run #31 on the UI branch had passed hours earlier.
  That pattern matches exhausted Actions minutes or a spending limit;
  macOS runners bill at 10x and Windows at 2x, and this matrix runs both
  on every push and PR. It needs the account owner, not a code change.

### 2026-09-28 — UI-09: light/dark theme toggle with persistence

- `web/src/theme.ts`: an explicit light/dark toggle layered on top of
  `style.css`'s existing *passive* `prefers-color-scheme` support, rather
  than replacing it -- "system" (the default, and previously the only
  choice) still follows the OS; "light" and "dark" are explicit overrides
  that win regardless of what the OS says. A `data-theme` attribute on
  `<html>` drives new `:root[data-theme="light"]`/`[data-theme="dark"]`
  CSS blocks; the existing `@media (prefers-color-scheme: dark)` block is
  now guarded by `:root:not([data-theme="light"])` so an explicit light
  choice can still override a dark OS. Persisted to `localStorage`
  (wrapped in try/catch -- private browsing or a locked-down browser just
  means the choice won't survive a reload, never a crash), and applied as
  early as possible in `main.ts` (before `mount()`) so a returning
  visitor's choice takes effect without a flash of the wrong theme.
- The toggle itself lives *outside* `app.ts`'s screen-swapping: `mount()`
  replaces `#app`'s entire content wholesale on every connect -> open ->
  viewer transition, so anything placed inside `#app` would be wiped by
  the next screen. `index.html` gained a `#pw-theme-toggle` sibling to
  `#app`; `main.ts` mounts the toggle there once, so it (and the theme
  choice) persist across every screen rather than just the viewer.
- **A real bug, caught by the Playwright suite catching a real click
  failure, not by inspection**: the toggle's first design used
  `position: fixed` to float it in a screen corner. On the viewer screen,
  the toolbar's own button row happened to reach that exact corner at the
  test browser's window width, and the fixed-position toggle sat on top
  of it in z-order -- silently eating clicks meant for the "Compare"
  button (four previously-passing UI-05 tests started failing the moment
  this was added, each timing out on `page.click(".pw-toolbar
  button:has-text('Compare')")` with Playwright's own error naming the
  exact intercepting element). Fixed by making `body` a flex column with
  `#pw-theme-toggle` as a real, in-flow reserved strip above `#app`
  (`flex: 0 0 auto`) rather than a floating overlay on top of it --
  `#app` takes the remaining height (`flex: 1 1 auto; min-height: 0`).
  In-flow layout can't collide with anything below it at any window
  width, which a fixed-position overlay can't guarantee.
- 4 new Playwright tests (386 total, 93.3% coverage): the toggle is
  visible before any document is open (proving it survives screen
  swaps), cycling through Auto -> Light -> Dark -> Auto, an explicit dark
  choice actually changing a computed CSS variable (not just the
  attribute), and the choice surviving a reload. ruff (check and format),
  mypy (scoped to `engine cli tools server`), bandit,
  `ops.schema.json`/SPEC catalog sync (unchanged -- no backend touched at
  all this session) and `npm audit` all clean.
- UI-09 moved to `done` (51/161 total, 9/15 in P3 -- **8/8 of P3's UI-0x
  features**, every one tracked for this phase, is now built and
  proven). Only EDT-05/07/08/09/10/11 remain open in P3, independent of
  the UI scaffolding this and the prior several sessions built.

### 2026-09-28 — UI-08: keyboard shortcuts and accessible UI

- Extended `viewer.ts`'s existing keyboard handler (which already had
  ArrowLeft/Right and PageUp/Down for page navigation) with: Home/End
  (first/last page), `+`/`-` (zoom in/out, deliberately bare keys rather
  than a `Ctrl`+ combination, since `Ctrl +`/`Ctrl -` are the browser's own
  page-zoom shortcuts and intercepting those would be a worse UX than the
  feature it adds), Ctrl/Cmd+Z and Ctrl/Cmd+Shift+Z (undo/redo, mirroring
  `history.ts`'s buttons via two new `triggerUndo`/`triggerRedo` methods
  that are exactly `button.click()` -- a no-op on a disabled button, so a
  shortcut can never act past what the button itself currently allows),
  and `c` (toggle the UI-05 Compare view). Every new toolbar/history
  button also got a `title` naming its shortcut, plus `aria-label` on the
  two symbol-only zoom buttons, for the "accessible UI" half of this
  feature's name.
- **A real, pre-existing bug found while scoping this, not a new one
  introduced by it**: `isTypingTarget` (the guard that keeps global
  shortcuts from firing while someone is typing) checked only `INPUT`/
  `TEXTAREA` tag names -- missing that UI-02's click-to-edit boxes
  (`overlay.ts`) are `contenteditable` `<div>`s, not `INPUT`s. That gap
  meant pressing ArrowLeft/ArrowRight to move the caret while actively
  editing a span's text *also* navigated pages, and (via the handler's own
  `preventDefault()`) silently broke caret movement inside the edit box
  entirely. Adding Home/End/Ctrl+Z next to the existing arrow keys would
  only have made this worse (imagine pressing Home while editing to jump
  to the start of a line, and getting bounced to page 1 instead), so this
  had to be fixed as part of the same change, not after: `isTypingTarget`
  now also checks `target.isContentEditable`, which is true for a
  contenteditable element and (unlike a tag-name check) for any of its
  descendants too. A regression test locks this in
  (`test_arrow_keys_move_the_caret_instead_of_navigating_pages_while_editing`).
- **A second real bug, this time in the test suite rather than the app**,
  found while writing this feature's own Ctrl+Z test and hunting down a
  CI failure it exposed in an *unrelated*, already-pushed UI-05 test on
  Windows only: `_commit_edit` (the UI-04 session's test helper) only
  waited for editing to visibly end, which happens synchronously the
  instant `commitEdit()` starts -- *before* the network request behind it,
  let alone `viewer.ts`'s post-commit `reloadDocument()`, has finished.
  `page.click()` (used for the Undo/Redo buttons directly in the existing
  UI-04 tests) has a built-in actionability wait that happens to absorb
  this race, which is why it went unnoticed there; a raw
  `page.keyboard.press()` (this feature's own shortcut tests) has no such
  wait, and neither did a UI-05 test that toggled Compare and then
  `check()`/`uncheck()`ed its diff checkbox -- a *second*, delayed
  `compare.show()` call (from the edit's own reload, only completing
  *after* the test had already moved on to interacting with Compare, and
  finding `comparing` already `true`) reset that checkbox out from under
  the test mid-sequence, failing only on Windows CI (slower, so the race
  window was wide enough to actually lose). Fixed once, at the source,
  rather than patched at each call site: `_commit_edit` now also waits for
  a `.pw-history-entry` to appear -- the actual last effect of
  `reloadDocument()` completing -- before returning.
- 5 new tests (382 total, 93.3% coverage): a regression test for the
  arrow-key/caret bug, Home/End page jumps, `+`/`-` zoom, Ctrl+Z undo and
  Ctrl+Shift+Z redo, and `c` toggling Compare. Also caught, this session,
  by actually running the local gate's `ruff format --check` (not just
  `ruff check`, which this session had been running alone since UI-04 --
  a real gap in the routine, not a new problem this feature introduced):
  two files from the UI-05 push were unformatted and had slipped through
  to a red `lint` job on CI. Reformatted and confirmed clean; `ruff format
  --check` is now part of every gate run from here on. mypy (scoped to
  `engine cli tools server`), bandit, `ops.schema.json`/SPEC catalog sync
  (unchanged) and `npm audit` all clean.
- One more thing observed, not fixed because it didn't reproduce: a single
  full-suite run (all 382 tests together) saw
  `test_ctrl_z_undoes_and_ctrl_shift_z_redoes` fail on a `wait_for_function`
  timeout; the same test passed in isolation, three repeats of the whole
  `test_ui.py` module, and an immediate full-suite re-run. Most likely
  this sandbox's resource contention under the full 382-test run (a real,
  documented environment characteristic, not this feature's doing) rather
  than a logic bug -- noted here rather than silently ignored, in case it
  recurs and turns out to be something real.
- UI-08 moved to `done` (50/161 total, 8/15 in P3). Remaining in P3: UI-09
  (light/dark toggle + persistence) and EDT-05/07/08/09/10/11.

### 2026-09-28 — UI-05: before/after split view

- **Backend, one small addition**: `UndoRedoJournal` now captures
  `original_bytes` once, at construction -- the document exactly as first
  opened, independent of the undo stack (which caps at `max_history` and
  drops its oldest entries, so `_undo_stack[0].before` stops being the true
  original after enough edits in a long session). A new route,
  `GET .../pages/{n}/render/original`, renders from a throwaway
  `Document.from_bytes(journal.original_bytes)` using the *same*
  `RenderPageOp` the existing `GET .../render` route already uses on the
  live document -- no new Op type, just a second place to apply the one
  that already exists. Never touches the journal's undo/redo state.
- **Deliberately reuses the server's authoritative PNG render for both
  sides, not a second pdf.js instance** -- unlike the main canvas (which
  is pdf.js, client-side, for interactivity), a before/after comparison
  should show the exact render the document would produce if saved right
  now, on both sides, not the browser's own approximation of one of them.
- **Frontend**: `src/compare.ts` builds two side-by-side scrollable panes
  (before/after), fetches both PNGs via the new API method
  (`Api.renderPage(id, page, {original})`, returning a `Blob` turned into
  an object URL), and mirrors scroll position between the two panes
  (SPEC.md 8.4's "synchronized scrolling") with a guard flag so the
  mirrored scroll event doesn't bounce straight back. The "diff overlay
  toggle" SPEC.md also asks for is computed client-side -- draw both PNGs
  to canvases, compare pixels with a small per-channel tolerance (PNG
  re-encoding and anti-aliasing introduce noise even between genuinely
  identical renders), and paint a translucent red overlay only where they
  differ -- rather than reusing `engine/verify.py`'s numpy-based pixel-diff
  harness, which is Python-only (built for FNT-12's per-edit verification
  and the P1 regression suite) and not reachable from the browser without
  a new endpoint; a same-size image comparison is simple enough to do
  directly in JS. A status line ("Pages are identical" / "Pages differ
  (X.X% of pixels changed)") is always shown, independent of whether the
  overlay itself is toggled on -- both a genuinely useful signal on its own
  and what let the Playwright tests assert something concrete without
  needing to inspect canvas pixel data through the DOM.
- `viewer.ts` gained a "Compare" toolbar toggle. Comparing and editing are
  mutually exclusive -- toggling swaps `pageArea`'s content between the
  normal click-to-edit canvas and the compare panel, rather than layering
  them, since SPEC.md's mockup doesn't ask for editing *while* comparing
  and keeping them exclusive means `overlay.ts` and `compare.ts` never
  need to coordinate shared state neither otherwise needs to know about.
  `goToPage` and `reloadDocument` both refresh the compare view too, if
  it's currently showing, so paging through the document or making
  another edit while comparing doesn't leave it stale.
- 8 new tests (377 total, 93.3% coverage): 2 engine (`original_bytes`'s
  stability, including across history capping), 2 server (the new route
  matches the live one before any edit, stays unchanged across an edit and
  an undo), 4 Playwright (toggle shows both renders and reports identical
  pre-edit; reports "differ" post-edit; the diff checkbox shows/hides the
  overlay canvas; toggling off returns to the editable canvas). One test
  needed a real fix, not a design change: comparing `to_bytes()` output
  byte-for-byte for the *engine* test failed, because PyMuPDF regenerates
  a random component of the PDF's `/ID` on every `tobytes()` call even
  with nothing else changed (confirmed directly -- two back-to-back calls
  on the same untouched document differ at one byte offset) -- fixed by
  comparing actual text content instead, the same fix pattern already
  used for a similar false assumption in the UI-02/UI-03 session's preview
  test. ruff, `mypy --strict` (scoped to `engine cli tools server`,
  matching CI), bandit, `ops.schema.json`/SPEC catalog sync (unchanged --
  no new Op type) and `npm audit` all clean.
- UI-05 moved to `done` (49/161 total, 7/15 in P3). Remaining in P3:
  UI-08 (keyboard shortcuts beyond page nav), UI-09 (light/dark toggle +
  persistence), and EDT-05/07/08/09/10/11.

### 2026-09-28 — UI-04: history panel with undo/redo

- The smallest remaining UI item, and the least novel: `GET .../history`
  and `POST .../undo`/`.../redo` already existed and were already fully
  tested from the COR-11 session, so this was almost entirely frontend
  wiring, not new backend design.
- `web/src/history.ts`: a footer strip (SPEC.md section 8.1's mockup) --
  a compact, horizontally-scrollable list of applied ops plus Undo/Redo
  buttons. `describeOp` renders a human-readable line per Op type by
  switching on its own discriminator field, matching `engine/ops/text.py`'s
  registered Op shapes field-for-field; an Op it doesn't recognize falls
  back to the raw op name rather than guessing.
- **One real limitation, documented rather than papered over**:
  `journal.history` (and so the API's `HistoryResponse`) is a list of the
  Ops as they were *requested* (an Op's own fields via `model_dump()`),
  not the `EditResult` each one produced -- tier, confidence and
  verification aren't part of it. Entries describe what was asked for, not
  how well it went; the richer per-edit summary SPEC.md's own mockup shows
  ("14 hits, Exact") would need the history endpoint to start recording
  results too, out of this feature's tracked scope (`tracker/features.json`'s
  UI-04 is just "History panel with undo/redo").
- `viewer.ts`'s post-commit reload (previously `reloadAfterCommit`,
  written for UI-02) generalized into `reloadDocument`, now the one place
  that owns "something about the document changed": re-fetch bytes,
  reload pdf.js, rebuild the thumbnail rail, re-render the current page,
  and refresh the history panel. Both the overlay's commit callback and
  the history panel's own undo/redo callback call this same function now,
  rather than each managing a partial refresh -- avoided a first-draft
  redundancy where the panel's undo/redo handlers would have refreshed
  history themselves *and* through this shared reload, double-fetching on
  every click.
- One new backend test (`test_replace_span_text_op_round_trips_through_history_and_undo`,
  tagged UI-04 since it's proving *this* feature's assumption, not COR-11's
  general undo/redo plumbing already covered): confirms `replace_span_text`'s
  fields -- what `describeOp` actually reads -- really are present in the
  history response, and that undo/redo work for this specific Op, not just
  the generically-tested `replace_text`.
- 5 new tests (369 total, 93.2% coverage); ruff, `mypy --strict` (scoped to
  `engine cli tools server`, matching CI -- running it over the whole tree
  including `tests/` isn't the actual gate and produces hundreds of
  unrelated pre-existing errors in test files that were never meant to be
  strictly typed), bandit, `ops.schema.json`/SPEC catalog sync all clean.
  `npm audit` on `web/` still finds 0 vulnerabilities. `pip-audit` flags
  several CVEs in `cryptography`/`httplib2`/`pip`/`pyjwt`/`setuptools`/
  `urllib3`/`wheel` -- none of them are pdfworkerz dependencies (grepped
  `pyproject.toml` to confirm; `setuptools` appears only as a
  `build-system` version floor, not a runtime dep), so this is sandbox
  environment drift in the advisory database since the last session, not
  anything this change introduced or can fix from here.
- UI-04 moved to `done` (48/161 total, 6/15 in P3). Remaining in P3:
  UI-05 (before/after split view), UI-08 (keyboard shortcuts beyond page
  nav), UI-09 (light/dark toggle + persistence), and EDT-05/07/08/09/10/11.

### 2026-09-28 — UI-02 and UI-03: click-to-edit and the inspector panel

- Continuing straight from the checkpoint's own plan: UI-03 (inspector
  panel) and UI-02 (click-to-edit overlay) together, since they share the
  same per-span style data that nothing server-side exposed yet.
- **Backend, in its own commit before any frontend work** (three new
  pieces, all thin wrappers over existing engine internals -- nothing here
  duplicates logic that already existed):
  - `PageSpansOp` (`engine/ops/base.py`): every span's style and text
    state on a page, read-only, exposed at `GET .../pages/{n}/spans`.
  - `PreviewTextOp` (`engine/ops/text.py`): what committing a text edit
    *would* do -- the exact font-resolution decision
    `engine.edit.resolve_font_for_span` already makes, already
    side-effect-free -- without drawing, redacting or touching the
    document at all. `GET .../pages/{n}/preview`. This is what lets the
    overlay show a live "Match" tier as the user types, before they've
    committed to anything, and is also where tracker/features.json's
    "confidence" field for UI-03 actually comes from (not buildable from
    `PageSpansOp`'s static data alone).
  - `ReplaceSpanTextOp` (`engine/ops/text.py`): a real bug headed off
    before it shipped. `ReplaceTextOp` finds its target by *searching*
    page text, so if the overlay's commit step had reused it, clicking one
    specific span and committing would have silently edited *every* span
    with the same text on that page instead -- confirmed with a
    two-identical-spans test fixture. This Op targets a span by its
    position in a fresh extraction instead (shared `_span_at` helper with
    `PreviewTextOp`, raising a clear `OpValidationError` rather than a raw
    `IndexError` on a bad index) -- exactly one span changes, guaranteed.
    Journaled normally, unlike its two read-only siblings above.
  - Also added `pdfworkerz spans` (mirrors `inspect`/`render`) and 15 new
    engine/server/CLI tests for all three.
- **Frontend**: `src/inspector.ts` (UI-03) is a small, focused side panel
  -- font (subset tag parsed out for readability), size, color (swatch +
  hex), Tc/Tz spacing, rotation, and a live "Match" row (a colored dot --
  green/amber/red for exact/approximate/fallback -- plus the confidence
  percentage and the resolver's own note as a tooltip). `src/overlay.ts`
  (UI-02) draws one absolutely-positioned box per span over the canvas
  (positioned via pdf.js's own `convertToViewportPoint` on the span's
  bbox corners -- confirmed against the installed pdfjs-dist's own `.d.ts`
  that no `convertToViewportRectangle` exists on this version's
  `PageViewport`, so both corners are converted and normalized by hand
  instead of assuming a method that isn't there). Clicking turns a box
  `contenteditable`; typing debounces into `PreviewTextOp` calls that
  drive the inspector's Match row live; Enter commits through
  `ReplaceSpanTextOp` (asking for confirmation first via `window.confirm`
  when the preview's own `requires_approval` says so -- SPEC.md section
  5.3's tiers 3/4, not a tier-name string comparison reinvented in TS);
  Escape discards. `viewer.ts` now owns fetching this page's spans
  alongside every render and reloading everything (bytes, spans, this
  page's render, the *whole* thumbnail rail -- simplest correct choice
  over tracking one stale thumbnail) after a commit, since span indices
  aren't assumed stable across one.
- **The font shown while editing is a deliberate approximation, not the
  document's real embedded typeface**: `overlay.ts` styles each box with
  the span's exact size and color, but only a serif/sans/mono +
  bold/italic guess from the font's *name* for family/weight/style --
  loading the actual embedded font as a browser `@font-face` is a
  documented gap (web/README.md), not a silent one. The size, color and
  (once committed) the real drawn result all still go through the exact
  same `engine.edit` pipeline the CLI and server already use.
- **Two more real bugs found by actually driving this in a browser** (on
  top of a caught-before-shipping design gap, the `ReplaceTextOp`-would
  edit-every-occurrence issue above):
  1. A UI-03 layout bug: `.pw-inspector-value`'s color swatch and match
     dot were `<span>`s I `prepend()`-ed into the value cell, then set
     text via `.lastChild.textContent` -- but after prepending, the swatch
     *is* `lastChild`, so that line was setting text *inside* the little
     colored box, not next to it. Fixed by giving each row its own
     dedicated text node up front, never reusing the icon element as a
     text target.
  2. A real reentrancy bug in `overlay.ts`, caught by an automated
     click-away test, not by inspection: setting `contentEditable = false`
     on a focused element can itself fire a synchronous `blur`, which
     re-enters `cancelEdit()` through `box.onblur` *before* the outer call
     finishes -- the reentrant call nulls out the shared "active box"
     reference first, so the outer call's next line crashed setting
     `.textContent` on what was now `null`. Root-caused by rebuilding the
     failure against `vite dev`'s unminified source to get a real stack
     trace, then fixed by having `cancelEdit()` take a local copy of the
     box and clear the shared reference *before* touching it at all, so a
     reentrant call becomes a harmless no-op.
- `tests/web/test_ui.py` gained 9 real, end-to-end Playwright tests (click
  opens an editable box; the inspector shows exact known values for a
  fixture with a known font/size/color; the Match dot populates as soon as
  a span is selected, before any typing; typing keeps it exact for
  ordinary text; Escape and clicking away both discard without saving --
  the second one is exactly what caught bug 2 above; Enter commits an
  exact match with no dialog; a Type3 fixture's fallback match asks for
  confirmation first; declining that confirmation leaves the edit
  uncommitted). 20/20 pass, stable across repeated runs.
- 364 tests total (93.2% coverage); ruff, mypy --strict, bandit, pip-audit
  and `npm audit` (0 vulnerabilities) all clean. UI-02 and UI-03 moved to
  "done" by the evidence gate (47/161 total, 5/15 in P3).
- **A third real bug, caught by CI itself rather than locally**: pushed
  this and found the macOS shards (only macOS, both Python versions) failed
  two of the new tests -- typed text was inserted at the start of the
  original text instead of replacing it. Root cause: `Control+A` is the
  Cocoa/Emacs "move to start of line" binding on macOS, not select-all
  (`Cmd+A` is); six of the new tests pressed it before typing, on the
  mistaken assumption it was needed. It never was -- `overlay.ts`'s
  `startEdit()` already calls `selectAllContents()` the instant a box
  becomes editable, so every platform already has the text selected by
  the time a test types into it. Deleted the keypress rather than
  branching per platform; Linux and Windows had been passing only because
  both treat Ctrl+A as select-all too, silently masking that it did
  nothing. Confirmed green on all three OSes afterward
  ([run #25](https://github.com/kalibudz/pdfworkerz/actions/runs/36365316425)).
- Still open in P3: UI-04/05/08/09 (history panel, before/after split
  view, keyboard shortcuts, themes) and EDT-05/07/08/09/10/11 (block
  move/resize, format painter, images, shapes, hyperlinks, spell-check).
  UI-04 (history panel) is a natural next step -- `GET .../history` and
  `POST .../undo` / `.../redo` already exist and are tested (COR-11); it's
  mostly frontend wiring onto what's already there, unlike UI-02 was.

### 2026-09-27 (cont. 11) — UI-01 and UI-06: the first real browser UI

- `web/`: a new TypeScript + Vite + pdf.js frontend (SPEC.md section 4.1's
  chosen stack), talking to `server/app.py` over plain JSON HTTP with no
  build-time coupling between the two. `src/config.ts` reads
  `?token=&api=` from the URL once (state/checkpoint.json's own suggested
  design from the end of the COR-11 session), remembers them in
  `sessionStorage`, and scrubs them from the address bar; `src/connect.ts`
  is the manual fallback when neither is known. `src/open.ts` (UI-06) and
  `src/viewer.ts` + `src/pdf.ts` (UI-01) are the two features themselves;
  `src/api.ts` is the typed client and `src/app.ts` wires the three
  screens together.
- **Four real bugs found and fixed before this could be called done, each
  through the same discipline the rest of this project already uses --
  build it, then actually drive it with a real browser and see what
  breaks, rather than trusting the code by inspection:**
  1. **CORS.** The UI and the API are always two different origins
     (different ports), even on one machine -- confirmed by hitting an
     actual browser-blocked `fetch` (no `Access-Control-Allow-Origin`)
     the first time the built UI tried to open a document from a static
     file server on a different port than `pdfworkerz serve`.
     `server/app.py` now adds `CORSMiddleware` scoped to loopback origins
     only (`127.0.0.1`/`localhost`/`::1`, never a wildcard) -- safe to
     scope this loosely (any port) because authentication here is the
     `X-Session-Token` *header*, which unlike a cookie a browser never
     attaches automatically, so a page on some other origin still can't
     act as this user without already knowing the random token. Two new
     direct tests confirm the header is actually granted for a loopback
     `Origin` and actually absent for a non-loopback one, not just that
     the regex looks right.
  2. **A genuinely incompatible pdf.js release, caught by version, not by
     guessing.** `pdfjs-dist` versions `>=5.6.83 <6.2.108` carry a public,
     high-severity CVE (arbitrary JS execution opening a malicious PDF --
     exactly this tool's own threat model); `npm audit` (now gated in the
     `security` CI job too) refused every version in that range. But the
     current, patched release (and, it turned out, everything back to at
     least 5.5.207) throws `getOrInsertComputed is not a function` on
     this sandbox's pinned test browser -- a `Map`/`WeakMap` method
     (TC39's "Upsert" proposal) pdf.js relies on that a real, current
     browser has natively by now but an older engine doesn't.
     `src/polyfills.ts` fills the gap only when the native method is
     missing (a no-op on any browser that already has it), and
     `src/pdf.worker.ts` wraps pdfjs-dist's own worker script so the same
     polyfill also installs in *that* separate realm -- confirmed by
     testing that the error came from both the main-thread bundle and the
     worker bundle before fixing only one and calling it done.
  3. **A real race condition in `viewer.ts`.** The toolbar's prev/next/zoom
     buttons and the keyboard-navigation handler were wired up *after* the
     `await`s that fetch and parse the document, but `.pw-viewer` (and
     those buttons) were already in the DOM before that -- so an
     interaction fast enough to land in that window was simply lost, no
     error, because no listener existed yet. Caught by the automated
     Playwright tests failing intermittently, not by manual testing (whose
     own incidental delays had been masking it). Fixed by attaching every
     listener immediately and disabling the affected controls until the
     document has actually loaded, rather than trying to guess a safe
     delay.
  4. **A real logic bug in the password-retry message**, caught the same
     way: distinguishing "needs a password" from "wrong password" only
     needs to look at whether *this* request sent one, not any memory of
     earlier attempts -- the earlier, more complicated version compared
     the current attempt against the *previous* attempt's state and picked
     the wrong branch on the very first wrong-password retry.
- `tests/web/`: real end-to-end evidence, not a DOM/unit test double --
  a real `server/app.py` instance (uvicorn, background thread; an
  in-process `TestClient` can't be navigated to by a browser), a real
  static file server for `web/dist`, and `pytest-playwright` driving a
  real Chromium. On this sandbox specifically, that browser is the
  pre-installed one at a fixed path outside Playwright's own version-keyed
  cache (see the repo's environment notes) -- `conftest.py`'s
  `browser_type_launch_args` override only takes effect when that path
  exists, so CI (which runs a real `playwright install --with-deps
  chromium` step instead) and a normal dev machine both get Playwright's
  own default resolution. 11 tests cover both features: the connect
  screen, URL-config consumption, the missing-file error, all three
  password-prompt states (needs one / wrong / correct), page count and
  thumbnail count, next/prev, arrow keys, thumbnail-click navigation, and
  zoom.
- CI (`tests` job): added Node + `npm ci && npm run build` (in `web/`) and
  `playwright install --with-deps chromium` ahead of the existing
  `pytest --feature-results` step, so this evidence is produced in CI --
  the same single pytest invocation the evidence gate already reads --
  not only locally. Cost accepted deliberately: this runs once per OS ×
  Python-version shard (6 times total) rather than once, since splitting
  it into a separate job would put UI-01/UI-06's evidence in a
  `feature_results.json` the `tests` job's own evidence-gate check never
  sees.
- **Also fixed, while verifying end to end**: this sandbox's bare `pip`
  installs into a site-packages that this sandbox's bare `mypy`/`ruff`
  binaries on `PATH` don't share (see the previous session entry) --
  same root cause, newly relevant here because `pip install ".[dev]"` now
  also has to make `playwright`/`pytest-playwright` visible to whichever
  `pytest` actually runs; using `python3 -m <tool>` throughout stayed the
  reliable fix.
- 340 tests total (93.1% coverage); ruff, `python3 -m mypy` --strict,
  bandit, pip-audit and `npm audit` (0 vulnerabilities on `web/`'s pinned
  deps) all clean. UI-01 and UI-06 moved to "done" by the evidence gate
  (45/161 total, 3/15 in P3).
- Still open in P3: UI-02/03/04/05/08/09 (click-to-edit overlay,
  inspector, history panel, before/after split view, keyboard shortcuts,
  themes) and EDT-05/07/08/09/10/11 (block move/resize, format painter,
  images, shapes, hyperlinks, spell-check). Per the checkpoint's own
  ordering: UI-03 (inspector) and UI-02 (click-to-edit) next, since they
  share the same per-span style data and nothing else in P3 needs new
  frontend scaffolding the way those two still do.

### 2026-09-27 (cont. 11) — first real-document test: Form XObject fix, bundled Roboto

- The user asked to test reading and editing on a real document: a 4-page,
  templated account statement (not committed anywhere; all regression tests
  use generated fixtures). Reading worked. Editing exposed two real,
  related gaps, both fixed:
  1. **Text inside a Form XObject couldn't be edited.** The statement draws
     its whole header, address block, dates and page numbers inside a
     per-page form. `classify_font` / `_bytes_per_glyph` only searched the
     page's own `/Resources/Font`, so every such edit crashed with a raw
     `KeyError`. When a form reuses a page-level name (`/F1` in both), a
     name lookup would silently pick the wrong font instead. Fonts are now
     classified by xref (`classify_font_xref`).
  2. **No span on any page had a text state** (0/288). The content-stream
     walkers never followed `Do`, so their glyph count never matched
     texttrace, which does include form glyphs inline (verified). The new
     `_walk_content` follows forms under the spec's implicit q/Q, with the
     form's resources in scope and a cycle guard. Coverage went to 288/288,
     recovering real Tc/Tw values and a render-mode-2 faux bold.
  - Unlocatable fonts now raise `FontResourceNotFoundError` (422 over the
    API) instead of a bare `ValueError`.
  - Checked, not assumed: MuPDF's redaction already copies a shared form on
    write, so editing it on one page leaves the other page intact. A
    regression test locks that in; no refusal guard was needed.
- **Bundled Roboto Light/Regular** (OFL-1.1, the official v3.016 `web/static`
  builds, ~157KB each, covering Latin-1/Ext-A, Cyrillic and most Greek).
  The statement's Roboto text went from approximate (Trebuchet, needs
  approval) to exact, confidence 1.0. A test proves the bundled font is
  the cause: exact with it in the index, approximate without.
- Ran the user's full 10-step test on a scratch copy before handing it
  over: inspect, render, spans, replace (page text and XObject text),
  tier gate, delete, restyle, insert, chaining, and the whole API path
  (open/op/render/history/undo/redo/save). All passed. The original's
  SHA-256 was unchanged, and no files were written next to it.
- Observed, not fixed (follow-ups):
  - A replacement keeps the original's *left* edge, so shorter text in a
    right-aligned header no longer ends at the right margin. Single-line
    alignment isn't detected yet.
  - A match text beginning with `-` needs `--` before the positional
    arguments on the CLI (standard Click parsing).
  - Delta-Book → Maiandra scores confidence 0.98 despite being a different
    family. Metric confidence looks overcalibrated for cross-family matches.
- 17 new tests (344 total, 93.2% coverage); ruff, mypy --strict, bandit and
  pip-audit clean. No feature status changes (bug fix plus a font asset).

### 2026-09-27 (cont. 10) — three real, pre-existing CI failures fixed

- Every commit from P0 through COR-11 had claimed a clean local gate, but
  **no GitHub Actions run on this repository had ever actually gone
  green** -- all 16 runs on `main` show `conclusion: failure` (checked via
  the GitHub API, per the session protocol's step 1.3, at the start of
  this session). Local runs never caught this because this sandbox's bare
  `mypy`/`pip` on `PATH` resolve to tool installs unrelated to the
  project's own dependency closure (a `uv tool install`-managed `mypy` in
  one case) -- worth remembering: `python3 -m mypy`/`python3 -m ruff`/
  `python3 -m pip_audit`, not the bare commands, are what actually check
  this project's own code against its own installed dependencies here.
  Three distinct, real bugs were behind the failures, found from the
  actual CI job logs (`actions_list`/`get_job_logs`), not guessed at:
  1. **`numpy==2.5.3` (pinned in P1) requires Python >=3.12 and has no
     3.11 wheel at all** -- confirmed against PyPI's own metadata -- so
     `pip install -e ".[dev]"` failed outright on every Python-3.11 CI
     shard (this project declares `requires-python = ">=3.11"` and tests
     3.11 in the matrix). Repinned to `numpy==2.4.6`, confirmed via the
     wheel's own METADATA to declare `Requires-Python: >=3.11` and to
     have working wheels for both 3.11 and 3.13.
  2. **`engine/fonts/match.py`'s `extract_metrics` crashed outright** with
     `fontTools.ttLib.TTLibError: Font contains no outlines` on
     `ubuntu-latest` specifically, breaking `rank_by_metrics` (FNT-07,
     Tier 3) for any document needing it and taking
     `test_replace_text_op_require_tier_rejects_a_weak_match` and
     `test_reflow_text_op_require_tier_rejects_a_weak_match` down with
     it. Root cause: `tt.getGlyphSet()` was called unguarded, and at
     least one font installed on that runner's image has neither a
     `glyf` nor a `CFF `/`CFF2` table (a bitmap- or color-bitmap-only
     font -- the same general class of "quirky installed font" that
     P2-part6 already had to special-case for `.ttc` files, just a
     different failure mode). Fixed the same way the surrounding code
     already handles a broken/unreadable font: caught and treated as "not
     a usable Tier 3 candidate," skipped rather than aborting the whole
     ranking pass.
  3. **`test_serve_command_is_registered` asserted `"--port" in
     result.output`** against Typer/Click's Rich-rendered `--help` text.
     Rich wraps that text to the detected terminal width, which is
     narrower and inconsistent across CI runners/OSes than this sandbox's
     shell, and was splitting the literal substring `--port` across a
     line break -- reproduced exactly from the CI logs (ubuntu-3.13,
     macos-3.13 and windows-3.13 all failed here, on this string, once
     the numpy fix let them reach it). Rewrote the test to check the
     actual Click command's own registered parameters
     (`typer.main.get_command(app).commands["serve"].params`) instead of
     parsing rendered text -- deterministic regardless of terminal width,
     Rich version or OS.
  4. Added two more `server/app.py` tests while re-verifying COR-11's
     evidence end to end (`GET .../file`, added below): confirms it
     returns the current in-memory document as real PDF bytes, and that
     it reflects an edit already applied, not just the on-disk original.
  5. `server/app.py` gained one small, undramatic route needed for the
     P3 UI work that follows this fix: `GET /documents/{id}/file`,
     returning `journal.document.to_bytes()` as `application/pdf` --
     the same direct-Document-method-call pattern `render`/`inspect`/
     `save` already use (not everything server-side needs to be an Op;
     only actions the undo/redo journal must track do). This is what lets
     the browser's pdf.js (SPEC.md section 4.1's chosen viewer library)
     render the real document client-side instead of only ever seeing
     page images.
- 329 tests total (2 net new); ruff, `python3 -m mypy` --strict, bandit
  and pip-audit all still clean. No feature changed status -- COR-11 was
  already "done" and stays done; this makes its evidence (and every other
  phase's) actually trustworthy in CI, which it demonstrably was not
  before. Pushing this and confirming the next Actions run is green is
  this session's immediate next step, before any further feature work,
  per the session protocol's own rule ("if CI is red, fixing it becomes
  the next task").

### 2026-09-27 (cont. 9) — COR-11, starting P3

- `server/app.py`: a FastAPI server exposing the same `Op` layer the CLI
  uses (SPEC.md section 4.2 rule 1), the foundation everything else in P3
  builds on. Every route but `/health` requires an `X-Session-Token` header
  matching a random token generated per server instance (SPEC.md section
  4.2 rule 5, "local only"); `server.run_server` binds uvicorn to
  `127.0.0.1` only, never configurable wider. Endpoints: open/inspect/close
  a document, apply any registered Op (`POST .../ops`, generic -- not just
  text edits), render a page to PNG, and undo/redo/history wired straight
  onto the existing `engine.ops.journal.UndoRedoJournal` (COR-05), which
  turned out to need no changes at all to serve UI-04 later. `pdfworkerz
  serve` (cli/main.py) starts it and prints the token.
- Added `fastapi==0.141.1` and `uvicorn==0.54.0` as runtime dependencies,
  and `httpx2==2.13.1` (not `httpx`, which this FastAPI/Starlette version's
  own TestClient deprecates in favor of it -- confirmed by installing both,
  seeing the deprecation warning, then uninstalling `httpx` and confirming
  `TestClient` still works with only `httpx2` present) as a dev dependency
  for `fastapi.testclient.TestClient`.
- **One real bug found and fixed, worth remembering for any future FastAPI
  route in this codebase**: this project's modules all start with `from
  __future__ import annotations` (postponed evaluation), and FastAPI
  resolves an `Annotated[X, Depends(f)]` parameter by `eval`-ing the
  stringified annotation against the route function's `__globals__`. A
  dependency function defined as a *closure inside* an app-building
  function (`f` only existing as a local variable, not a module global)
  isn't resolvable that way -- it fails silently and FastAPI falls back to
  treating the parameter as a plain (missing) query parameter instead of a
  dependency, breaking every route that used it, with no error at
  startup. Reproduced in isolation before writing the real fix: route
  handlers and their dependencies (`verify_token`, `_get_journal`) now live
  as module-level functions on a shared `APIRouter`, reading
  `request.app.state` instead of closing over a particular `app` instance,
  which `create_app()` just includes -- also a cleaner design, not only a
  workaround.
- Verified end to end twice: once in-process via `TestClient` (open a
  document, run a real `replace_text` Op through the API and see its
  result, render a page and check the PNG magic bytes, undo/redo through
  the API, save with and without `overwrite`), and once as a real spawned
  `pdfworkerz serve` process hit with `curl` over an actual socket on
  `127.0.0.1`, confirmed working, then stopped.
- 22 new tests (325 total, 93.1% coverage: 20 for `server/app.py` at 97%
  itself, 1 for the `pdfworkerz serve` CLI command, 1 confirming
  `create_app()` generates a different random token each time); ruff, mypy
  --strict, bandit and pip-audit all clean on `server/` alongside
  everything else. COR-11 moved to "done" (43/161 total, 1/15 in P3).
- Still open in P3: UI-01..06/08/09 (the actual pdf.js + TypeScript
  frontend -- a genuine tech-stack shift, nothing in `web/` exists yet) and
  EDT-05/07/08/09/10/11 (block move/resize, format painter, images,
  shapes, hyperlinks, spell-check), independent of the UI scaffolding.

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
