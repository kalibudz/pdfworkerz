/**
 * EDT-11: spell-check on the current page. While enabled (the toolbar's
 * "Spelling" toggle), every misspelled word gets an underline mark in the
 * edit layer; clicking one opens a small menu of suggestions. Choosing one
 * sends correct_word (engine.ops.spellcheck), which re-checks that the word
 * is still where it was before replacing it in its span's own style.
 * "Ignore" hides that word for the rest of this browser session.
 */

import type * as pdfjsLib from "pdfjs-dist";

import type { Api, Misspelling } from "./api";
import { bboxToRect } from "./overlay";

export interface SpellToolOptions {
  api: Api;
  documentId: string;
  onCommitted: () => void;
  /** How many misspellings the current page has (null while disabled). */
  onCount: (count: number | null) => void;
}

export interface SpellToolHandle {
  /** Re-check the page just rendered (a no-op while disabled). */
  update(pageIndex: number, viewport: pdfjsLib.PageViewport): Promise<void>;
  setEnabled(enabled: boolean): Promise<void>;
}

export function createSpellTool(layer: HTMLElement, options: SpellToolOptions): SpellToolHandle {
  let enabled = false;
  let pageIndex = 0;
  let viewport: pdfjsLib.PageViewport | null = null;
  let requestId = 0;
  const ignored = new Set<string>();

  function clear(): void {
    for (const el of layer.querySelectorAll(".pw-misspelling, .pw-spell-menu")) {
      el.remove();
    }
  }

  function closeMenu(): void {
    layer.querySelector(".pw-spell-menu")?.remove();
  }

  async function correct(miss: Misspelling, replacement: string): Promise<void> {
    closeMenu();
    try {
      await options.api.applyOp(options.documentId, {
        op: "correct_word",
        page_index: pageIndex,
        span_index: miss.span_index,
        start: miss.start,
        end: miss.end,
        word: miss.word,
        replacement,
        require_tier: "fallback",
      });
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "The correction could not be saved.");
      return;
    }
    options.onCommitted();
  }

  function openMenu(miss: Misspelling, mark: HTMLElement): void {
    closeMenu();
    const menu = document.createElement("div");
    menu.className = "pw-spell-menu";
    menu.setAttribute("role", "menu");
    menu.style.left = mark.style.left;
    menu.style.top = `${parseFloat(mark.style.top) + parseFloat(mark.style.height) + 2}px`;
    const add = (label: string, className: string, action: () => void): void => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = className;
      button.setAttribute("role", "menuitem");
      button.textContent = label;
      button.addEventListener("mousedown", (event) => event.stopPropagation());
      button.addEventListener("click", action);
      menu.appendChild(button);
    };
    if (miss.suggestions.length === 0) {
      const none = document.createElement("span");
      none.className = "pw-hint";
      none.textContent = "No suggestions";
      menu.appendChild(none);
    }
    for (const suggestion of miss.suggestions) {
      add(suggestion, "pw-spell-suggestion", () => void correct(miss, suggestion));
    }
    add(`Ignore “${miss.word}”`, "pw-spell-ignore", () => {
      ignored.add(miss.word.toLowerCase());
      void refresh();
    });
    layer.appendChild(menu);
    menu.querySelector("button")?.focus();
  }

  async function refresh(): Promise<void> {
    const view = viewport;
    const id = ++requestId;
    clear();
    if (!enabled || !view) {
      options.onCount(null);
      return;
    }
    const found = await options.api.pageSpelling(options.documentId, pageIndex, [...ignored]);
    if (id !== requestId || !enabled) {
      return; // a newer page or toggle superseded this request
    }
    for (const miss of found) {
      const rect = bboxToRect(view, miss.bbox);
      const mark = document.createElement("div");
      mark.className = "pw-misspelling";
      mark.title = miss.suggestions.length ? `Did you mean: ${miss.suggestions.join(", ")}?` : "Not in the dictionary";
      mark.dataset.word = miss.word;
      mark.style.left = `${rect.left}px`;
      mark.style.top = `${rect.top}px`;
      mark.style.width = `${rect.width}px`;
      mark.style.height = `${rect.height}px`;
      mark.addEventListener("mousedown", (event) => {
        event.preventDefault();
        event.stopPropagation();
        openMenu(miss, mark);
      });
      layer.appendChild(mark);
    }
    options.onCount(found.length);
  }

  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeMenu();
    }
  });
  layer.addEventListener("mousedown", (event) => {
    if (!(event.target as HTMLElement).closest(".pw-spell-menu")) {
      closeMenu();
    }
  });

  return {
    async update(nextPageIndex, nextViewport) {
      pageIndex = nextPageIndex;
      viewport = nextViewport;
      await refresh();
    },
    async setEnabled(next) {
      enabled = next;
      await refresh();
    },
  };
}
