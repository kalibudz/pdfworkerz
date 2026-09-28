# PDFWorkerz web UI

The browser UI (SPEC.md section 8): a pdf.js canvas with a thumbnail rail
(UI-01), the password prompt for encrypted files (UI-06), a click-to-edit
overlay (UI-02), an inspector panel (UI-03), a history panel with
undo/redo (UI-04), and a before/after split view (UI-05) -- and, as later
features land, the command bar. It talks to `server/app.py` over plain
JSON HTTP; nothing here runs on the server, and nothing in `server/`
knows this directory exists.

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

The click-to-edit overlay (`src/overlay.ts`) styles each editable box with
the span's *exact* size and color, but only an *approximated*
font-family/weight/style guessed from the font's name (serif/sans/mono,
bold, italic). It does not load the document's actual embedded font as a
web font in the browser -- SPEC.md's "drawn in the detected font" is an
ideal this gets close to, not a claim that the glyphs on screen while
editing are pixel-identical to the PDF's own typeface. The size, color,
and (once committed) the actual drawn result all go through the same
font-resolution pipeline the CLI and server already use (`engine.edit`),
which *is* exact -- only the live, in-browser preview while typing is an
approximation.

`overlay.ts` also has a documented, confirmed-the-hard-way reentrancy
fix worth reading before touching its cancel/commit logic: setting
`contentEditable = false` on a focused element can itself fire a
synchronous `blur`, which re-enters the cancel handler through
`box.onblur` *before* the outer call has finished. `cancelEdit()` takes a
local copy of the shared "currently editing" reference and clears the
shared one immediately, before touching the box at all, specifically so
that reentrant call becomes a harmless no-op instead of operating on a
box the outer call has already moved past.

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
