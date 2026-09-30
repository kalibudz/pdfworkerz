# PDFWorkerz web UI

The browser UI (SPEC.md section 8): a pdf.js canvas with a thumbnail rail
(UI-01), the password prompt for encrypted files (UI-06), a click-to-edit
overlay (UI-02), an inspector panel (UI-03), a history panel with
undo/redo (UI-04), a before/after split view (UI-05), keyboard shortcuts
(UI-08) and a light/dark theme toggle (UI-09) -- plus the P3 editing
tools built on them: the format painter (EDT-07, `inspector.ts`/`overlay.ts`),
paragraph move/resize handles (EDT-05, `blockdrag.ts`), links (EDT-10),
images (EDT-08, `images.ts`), vector shapes (EDT-09, `shapes.ts`, sharing
`drag.ts` with images) and spell-check (EDT-11, `spelling.ts`). It talks to
`server/app.py` over plain JSON HTTP; nothing here runs on the server, and
nothing in `server/` knows this directory exists.

The edit layer stacks, bottom to top: shape boxes, image boxes, text
boxes (one per block, line or word -- see "Selection modes" below),
spelling marks, then handles and menus -- so text over an
image or shape is always clickable for editing. Every box is placed with
`overlay.ts`'s `bboxToRect`, which converts MuPDF's y-down, crop-box-relative
coordinates through pdf.js's viewport (see its comment for the bug this
once had).

## Selection modes: Block, Line, Word (EDT-16..18)

The toolbar's **Block | Line | Word** control (keys `B`, `L`, `W`; a radio
group, so the arrow keys move within it) decides what one click on text
selects. The default is Line; the choice is kept in `localStorage` under
`pdfworkerz.selectMode`. Changing mode clears the selection and redraws the
page's text boxes.

- The server groups the text: `viewer.ts` fetches
  `GET .../pages/{p}/text_units?granularity=<mode>` on every render and hands
  the units to `overlay.ts` and `arrange.ts`. Nothing in this directory
  decides what a line or a word is. The spans are still fetched too, because
  they carry the styles the inspector shows.
- `overlay.ts` draws one box per unit. Every box keeps the class
  `.pw-span-box` (the one selector for "a text hit target") plus
  `pw-unit-block`, `pw-unit-line` or `pw-unit-word`, with `data-unit-index`
  (the unit's index) and `data-span-index` (its first span).
- The inspector (`showUnit`) shows which kind of unit is selected, its text,
  and the first span's style. A style field that differs across the unit's
  spans shows "(mixed)" and is only sent once the user changes it.
- Apply (or Enter) sends one `edit_text_unit` Op with `expect_text` and only
  the fields that changed. Afterwards the edited unit is found again by its
  unit kind, its origin (within 0.5pt) and its new text; failing that, by
  whichever unit overlaps its old box most.
- Every mode can drag, nudge, align, copy, paste, duplicate and delete. Text
  refs in `move_objects`, `duplicate_objects` and `delete_objects` carry
  `unit` and `expect_text`. A block keeps its move and resize handles
  (`move_text_block`); a line or word has a move handle only, which sends a
  single-item `move_objects`.
- The format painter copies one style run (a span), so it is off in Word
  mode. In Line and Block modes it copies from the selected unit's first
  span onto the run under the next click.

## Running it

1. Start the API: `pdfworkerz serve` (prints the port and a session token).
2. In this directory: `npm ci` once, then `npm run dev` for a live-reloading
   dev server, or `npm run build` + `npm run preview` for a production
   build.
3. Open the printed dev/preview URL with `?token=<token>&api=http://127.0.0.1:<port>`
   appended, e.g. `http://localhost:5173/?token=abc123&api=http://127.0.0.1:8000`.
   `src/config.ts` reads these once, remembers them in `sessionStorage` for
   the rest of the tab's session, and scrubs them from the address bar so
   the token doesn't linger in browser history. Without them, a "Connect"
   screen asks for the same two values by hand.

The API and the UI are always two different origins (different ports at
least), even when both run on `127.0.0.1` -- `server/app.py` allows this
via a CORS policy scoped to loopback origins only (never a wildcard), since
its `X-Session-Token` header isn't something a browser attaches
automatically the way it would a cookie.

## Testing

`tests/web/` (Python, at the repo root) drives a real build of this
directory with a real Chromium through Playwright -- not a DOM/unit test
double. Build first (`npm run build`), then from the repo root:
`pytest tests/web/`. See that directory's `conftest.py` for how the API
server, a static file server for `dist/`, and the browser are wired
together, and the repo root's environment notes for why the browser
executable path is resolved the way it is.

## UI-02's font approximation is deliberate, not exact

The click-to-edit overlay (`src/overlay.ts`) gives each text box the
first span's *exact* size, but only an *approximated*
font-family/weight/style guessed from the font's name (serif/sans/mono,
bold, italic). It does not load the document's actual embedded font as a
web font in the browser. That no longer shows: a box is only a hit target,
and its text is transparent (`style.css`), because the canvas underneath
already shows the real glyphs. The committed result goes through the same
font-resolution pipeline the CLI and server use (`engine.edit`), which
*is* exact.

Text is never edited on the page. Clicking a box selects it and puts the
cursor in the inspector's **Edit text** textarea (`inspector.ts`); Enter or
Apply commits, Esc or Revert restores the draft, and Esc in an unchanged
textarea hands the keyboard back to the page. The boxes are not
`contenteditable`, so there is no blur/cancel handling in `overlay.ts` to
be careful with: clicking elsewhere keeps the draft, and choosing other
text with an unapplied draft asks first.

## UI-04's history panel shows what was asked for, not how well it went

`src/history.ts` renders `GET .../history`'s `ops` list, which is
`journal.history` -- each applied `Op`'s own requested fields
(`model_dump()`), not the `EditResult` it produced. Tier, confidence and
verification are not part of that response, so a history entry describes
*what was asked for* (e.g. "Replace text with \"Hello, Editor.\""), not
*how well it went* (SPEC.md's own mockup shows a richer summary like "14
hits, Exact" that this doesn't attempt). Getting that would mean the
journal itself recording results per entry, not just inputs -- out of
this feature's tracked scope. `describeOp` switches on each Op's `op`
discriminator and falls back to the raw op name for one it doesn't
recognize, rather than guessing at fields that might not exist.

`viewer.ts`'s `reloadDocument` (generalized from UI-02's
`reloadAfterCommit`) is the one place that refreshes everything after any
document-changing action -- a commit, an undo, or a redo -- ending with
`history.refresh()`. Both the overlay's commit callback and the history
panel's own undo/redo callbacks call this single function rather than
each managing a partial refresh, which is also why the panel's undo/redo
button handlers don't call `refresh()` themselves on success: `reloadDocument`
already will. They still call `refresh()` on failure, to re-sync the
buttons' disabled state if the undo/redo request itself was rejected.

## UI-05's split view renders both sides on the server, deliberately not via pdf.js

`src/compare.ts` fetches two PNGs from the server -- `GET .../render`
(the live document) and `GET .../render/original` (the document exactly
as first opened, from a new `UndoRedoJournal.original_bytes` snapshot
captured once at open time, independent of the undo stack's history cap)
-- rather than loading a second `PdfDocument` and rendering the "before"
side with pdf.js like the main canvas does. The split view's whole point
is comparing the exact render the document would produce if saved right
now against the exact render it would have produced when opened, on both
sides; pdf.js's own rendering is a reasonable approximation for the live,
interactive canvas but not what a before/after comparison should be
built on.

The two panes scroll together (SPEC.md section 8.4's "synchronized
scrolling"): each pane's `scroll` listener copies its `scrollTop`/
`scrollLeft` onto the other, guarded by a `syncing` flag so the mirrored
scroll event doesn't bounce back and re-trigger the first listener.

The "diff overlay toggle" SPEC.md also asks for is computed entirely in
the browser -- both PNGs are drawn to offscreen canvases, compared pixel
by pixel with a small per-channel tolerance (PNG re-encoding and
anti-aliasing introduce a little noise even between two genuinely
identical renders), and painted as a translucent red overlay wherever
they differ. This does *not* reuse `engine/verify.py`'s numpy-based
pixel-diff harness -- that module is Python-only, built for FNT-12's
per-edit verification and the P1 regression suite, and isn't reachable
from the browser without a new endpoint; comparing two same-size PNGs is
simple enough to do directly in JS. A status line ("Pages are identical"
/ "Pages differ (X.X% of pixels changed)") is always shown, independent
of whether the overlay itself is toggled visible.

Comparing and editing are mutually exclusive in `viewer.ts`: the
"Compare" toolbar toggle swaps `pageArea`'s content between the normal
click-to-edit canvas and the compare panel rather than layering them, so
`overlay.ts` and `compare.ts` never need to coordinate state neither
otherwise needs to know about.

## UI-08's keyboard shortcuts, and a real bug found while adding them

Beyond the page-navigation shortcuts UI-01 already had (arrow keys,
Page Up/Down), `viewer.ts`'s `keyHandler` now also handles: `Home`/`End`
(first/last page), bare `+`/`-` (zoom -- deliberately not a `Ctrl`
combination, since `Ctrl +`/`Ctrl -` are the browser's own page-zoom
shortcuts, and hijacking those would be a worse trade than the feature),
`Ctrl`/`Cmd+Z` and `+Shift+Z` (undo/redo, via two new `HistoryHandle`
methods -- `triggerUndo`/`triggerRedo` -- that are exactly
`button.click()`, so a shortcut can never act past what the button's own
`disabled` state already allows), and `c` (toggle the UI-05 Compare
view). Every toolbar/history button also gained a `title` naming its
shortcut, plus `aria-label` on the two symbol-only zoom buttons.

`isTypingTarget` is the guard that keeps these shortcuts from firing while
someone is typing. Text is typed in the inspector's textarea, which its
`INPUT`/`TEXTAREA` tag check covers. It also checks
`target.isContentEditable`: the text boxes used to be `contenteditable`
`<div>`s, and without that check the arrow keys turned pages while moving
the caret. No box is contenteditable today; the check stays for any
editable element added later, and a regression test locks the behaviour in.

The single-letter shortcuts are `C` (compare) and `B` / `L` / `W`
(selection mode, EDT-16). They only fire with no modifier held and outside
a typing target, so typing those letters in the inspector or the command
bar is unaffected.

## UI-09's theme toggle, and a fixed-position layout bug it exposed

`src/theme.ts` layers an explicit light/dark toggle on top of
`style.css`'s existing *passive* `prefers-color-scheme` support rather
than replacing it: "Auto" (the default, and previously the only choice)
still follows the OS; "Light" and "Dark" are explicit overrides that win
regardless of what the OS says, via a `data-theme` attribute on `<html>`
persisted to `localStorage`. It's mounted from `main.ts` into a
`#pw-theme-toggle` element that's a *sibling* of `#app` in `index.html`,
not something inside it -- `app.ts`'s `mount()` replaces `#app`'s entire
content wholesale on every connect -> open -> viewer transition, so
anything placed inside `#app` would be wiped by the next screen. Living
outside it is what lets the toggle (and the theme choice) survive every
screen, not just the viewer.

The first version of this toggle used `position: fixed` to float it in a
screen corner -- and broke four *already-passing* UI-05 tests the moment
it was added, each failing with Playwright's own error naming the exact
element blocking the click: the viewer toolbar's own button row reached
that same corner at the test browser's window width, and the
fixed-position toggle sat on top of it in z-order, silently eating clicks
meant for "Compare". Fixed by making `body` a flex column with
`#pw-theme-toggle` as a real, in-flow reserved strip (`flex: 0 0 auto`)
above `#app` (`flex: 1 1 auto; min-height: 0`) instead of a floating
overlay on top of it. In-flow layout can't collide with anything below
it at any window width; a fixed-position element never has that
guarantee. Worth remembering before adding any other persistent UI chrome
to this app.

## The pdfjs-dist version pin and the `getOrInsertComputed` polyfill

`pdfjs-dist` is pinned to a specific patch version deliberately, not left
open-ended: versions `>=5.6.83 <6.2.108` carry a high-severity, publicly
disclosed vulnerability (arbitrary JavaScript execution on opening a
malicious PDF -- exactly the input this project's whole job is to open).
`npm audit` gates this in CI (the `security` job); re-run it after ever
changing this pin.

Both the main thread and pdf.js's own worker script use
`Map`/`WeakMap.prototype.getOrInsertComputed` (a TC39 proposal), which is
missing on some real installed Chromium builds. `src/polyfills.ts` fills
the gap only when the native method isn't present, so it's a no-op on any
browser that already has it -- confirmed necessary by hitting the exact
`getOrInsertComputed is not a function` failure against this sandbox's
pinned test browser before adding it, in both the main-thread bundle and
the worker (`src/pdf.worker.ts` wraps pdfjs-dist's own worker script
specifically so the polyfill also installs in that separate realm, which
`src/main.ts`'s own import into the main thread can't reach).
