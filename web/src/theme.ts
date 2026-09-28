/**
 * UI-09: an explicit light/dark theme toggle with persistence, layered on
 * top of style.css's existing passive `prefers-color-scheme` support
 * rather than replacing it -- "system" (the default, and the only choice
 * this app had before this feature) still follows the OS; "light" and
 * "dark" are explicit overrides that win regardless of what the OS says.
 *
 * Lives entirely outside app.ts's screen-swapping (`mount()`'s connect ->
 * open -> viewer transitions each replace `#app`'s content wholesale via
 * `innerHTML = ""`) -- main.ts mounts this into a separate root sibling
 * to `#app`, so the toggle -- and the chosen theme itself, which is a
 * `data-theme` attribute on `<html>`, not anything inside `#app` -- both
 * survive every screen transition rather than being wiped by one.
 */

export type Theme = "system" | "light" | "dark";

const STORAGE_KEY = "pw-theme";
const ORDER: readonly Theme[] = ["system", "light", "dark"];
const LABELS: Record<Theme, string> = {
  system: "\u{1F5A5}️ Auto",
  light: "☀️ Light",
  dark: "\u{1F319} Dark",
};

function isTheme(value: string | null): value is Theme {
  return value === "system" || value === "light" || value === "dark";
}

/** Defaults to "system" if no choice was ever made, or if storage isn't
 * available at all (private browsing, a locked-down test browser) --
 * never fatal, just falls back to following the OS. */
function loadTheme(): Theme {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    return isTheme(stored) ? stored : "system";
  } catch {
    return "system";
  }
}

function storeTheme(theme: Theme): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // Storage unavailable -- the choice won't survive a reload, but the
    // toggle still works for the rest of this session.
  }
}

function applyTheme(theme: Theme): void {
  if (theme === "system") {
    delete document.documentElement.dataset.theme;
  } else {
    document.documentElement.dataset.theme = theme;
  }
}

/** Call once, as early as possible (before anything paints) -- applying a
 * previously chosen theme immediately avoids a flash of the wrong one. */
export function applyStoredTheme(): void {
  applyTheme(loadTheme());
}

/** Builds a self-contained toggle button: cycles Auto -> Light -> Dark ->
 * Auto, applying and persisting each choice and keeping its own label
 * current. Reads the already-applied stored theme as its starting point
 * rather than assuming "system", so it stays in sync with
 * applyStoredTheme()'s earlier call. */
export function createThemeToggle(): HTMLButtonElement {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "pw-theme-toggle";

  function render(theme: Theme): void {
    button.textContent = LABELS[theme];
    button.title = `Theme: ${theme} (click to change)`;
  }

  let current = loadTheme();
  render(current);

  button.addEventListener("click", () => {
    current = ORDER[(ORDER.indexOf(current) + 1) % ORDER.length];
    applyTheme(current);
    storeTheme(current);
    render(current);
  });

  return button;
}
