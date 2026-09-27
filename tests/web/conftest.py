"""Fixtures for the browser-driven UI-01/UI-06 evidence tests.

These are real end-to-end tests: a real `server/app.py` instance (via
uvicorn, in a background thread -- an in-process ``TestClient`` can't be
navigated to by a browser) and a real static file server for `web/dist`
(the production build; `vite dev`'s own server is not under test here),
driven by a real Chromium through `pytest-playwright`. `web/dist` must
already be built (`npm ci && npm run build` in `web/`) -- this module
skips entirely rather than building it itself, the same separation of
concerns `tracker/`'s own build has from the Python test suite.
"""

from __future__ import annotations

import functools
import http.server
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn

from server.app import create_app

DIST_DIR = Path(__file__).resolve().parent.parent.parent / "web" / "dist"
TOKEN = "playwright-test-token"  # a fixed test-only value, not a real secret

# This sandbox's pre-installed Chromium lives at a fixed path outside
# Playwright's own version-keyed cache (see the repo's environment notes);
# CI and a normal dev machine instead get a real browser via
# `playwright install chromium` at Playwright's default cache location, so
# this override only applies when that fixed path actually exists.
_SANDBOX_CHROMIUM = Path("/opt/pw-browsers/chromium")

pytestmark = pytest.mark.skipif(
    not DIST_DIR.exists(),
    reason="web/dist is not built -- run `npm ci && npm run build` in web/ first",
)


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args: dict[str, object]) -> dict[str, object]:
    if _SANDBOX_CHROMIUM.exists():
        return {**browser_type_launch_args, "executable_path": str(_SANDBOX_CHROMIUM)}
    return browser_type_launch_args


@pytest.fixture(scope="module")
def api_server() -> Iterator[str]:
    """A real, running server/app.py instance with a known token."""
    app = create_app(token=TOKEN)
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True


@pytest.fixture(scope="module")
def static_server() -> Iterator[str]:
    """web/dist, served exactly as a real deployment would (no dev-server proxy)."""
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(DIST_DIR))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()


@pytest.fixture
def app_url(static_server: str, api_server: str) -> str:
    """The URL that hands the frontend its session config (config.ts)."""
    return f"{static_server}/?token={TOKEN}&api={api_server}"


@pytest.fixture
def connect_only_url(static_server: str) -> str:
    """No `?token=`/`?api=` -- exercises the connect-screen fallback."""
    return f"{static_server}/"
