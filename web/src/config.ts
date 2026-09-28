/**
 * Session config: where the API is and the token that authenticates every
 * request but /health (server/app.py's verify_token). `pdfworkerz serve`
 * prints both; state/checkpoint.json's own suggested design (rather than a
 * dev-server proxy, which would only work for `vite dev` and not for the
 * built `dist/` or for Playwright driving a static build in tests/web/) is
 * to pass them once via the URL and remember them for the rest of the tab's
 * session -- so this resolves identically under `vite dev`, a static build,
 * and a test server.
 */

const TOKEN_KEY = "pdfworkerz.token";
const API_BASE_KEY = "pdfworkerz.apiBase";
const DEFAULT_API_BASE = "http://127.0.0.1:8000";

export interface SessionConfig {
  apiBase: string;
  token: string;
}

function stripConfigParamsFromUrl(): void {
  const url = new URL(window.location.href);
  if (!url.searchParams.has("token") && !url.searchParams.has("api")) {
    return;
  }
  url.searchParams.delete("token");
  url.searchParams.delete("api");
  window.history.replaceState({}, "", url.pathname + url.search + url.hash);
}

/** Read `?token=&api=` once, persist to sessionStorage, then scrub the URL bar
 * so the token doesn't linger in browser history or get shared accidentally. */
function consumeUrlParams(): void {
  const params = new URLSearchParams(window.location.search);
  const token = params.get("token");
  const apiBase = params.get("api");
  if (token) {
    sessionStorage.setItem(TOKEN_KEY, token);
  }
  if (apiBase) {
    sessionStorage.setItem(API_BASE_KEY, apiBase);
  }
  stripConfigParamsFromUrl();
}

export function loadConfig(): SessionConfig | null {
  consumeUrlParams();
  const token = sessionStorage.getItem(TOKEN_KEY);
  const apiBase = sessionStorage.getItem(API_BASE_KEY) ?? DEFAULT_API_BASE;
  if (!token) {
    return null;
  }
  return { apiBase, token };
}

export function saveConfig(config: SessionConfig): void {
  sessionStorage.setItem(TOKEN_KEY, config.token);
  sessionStorage.setItem(API_BASE_KEY, config.apiBase);
}

export function clearConfig(): void {
  sessionStorage.removeItem(TOKEN_KEY);
  sessionStorage.removeItem(API_BASE_KEY);
}

export function defaultApiBase(): string {
  return DEFAULT_API_BASE;
}
