# PDFWorkerz web UI

The browser UI (SPEC.md section 8): a pdf.js canvas with a thumbnail rail
(UI-01), the password prompt for encrypted files (UI-06), and -- as later
features land -- the click-to-edit overlay, inspector, command bar and
history panel. It talks to `server/app.py` over plain JSON HTTP; nothing
here runs on the server, and nothing in `server/` knows this directory
exists.

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
