/**
 * P4: the command bar (SPEC.md section 8.3). Type a command, press Enter to preview what it
 * would change (CMD-05), Enter again (or Apply) to do it. As you type, the words that can
 * come next are offered (CMD-04). A command that doesn't parse is never run as a guess: the
 * bar shows why, the closest valid commands to click (CMD-06), and a syntax hint.
 * Recipes (CMD-07): export this session's edits, or run a recipe file after a dry run.
 */

import { ApiError, type Api } from "./api";

export interface CommandBarOptions {
  api: Api;
  documentId: string;
  /** 0-based page shown in the viewer: where "insert" goes when a command names no page. */
  currentPage: () => number;
  /** A command or recipe changed the document. */
  onApplied: () => Promise<void>;
  /** Suggested download name for an exported recipe. */
  title: string;
}

export interface CommandBarHandle {
  element: HTMLElement;
  focus(): void;
}

const COMPLETE_DEBOUNCE_MS = 150;

function button(label: string, className: string): HTMLButtonElement {
  const el = document.createElement("button");
  el.type = "button";
  el.className = className;
  el.textContent = label;
  return el;
}

export function createCommandBar(options: CommandBarOptions): CommandBarHandle {
  const bar = document.createElement("div");
  bar.className = "pw-command-bar";

  const row = document.createElement("div");
  row.className = "pw-command-row";
  const input = document.createElement("input");
  input.type = "text";
  input.id = "pw-command";
  input.placeholder = 'Type a command, e.g. replace "2024" with "2025" on all pages   (press / to focus)';
  input.setAttribute("aria-label", "Command");
  input.autocomplete = "off";
  input.spellcheck = false;
  const previewButton = button("Preview", "pw-command-preview");
  const exportButton = button("Export recipe", "pw-recipe-export");
  exportButton.title = "Download this session's edits as a replayable recipe (YAML)";
  const runButton = button("Run recipe…", "pw-recipe-run");
  runButton.title = "Apply a recipe file to this document, after showing what it would change";
  const recipeFile = document.createElement("input");
  recipeFile.type = "file";
  recipeFile.accept = ".yaml,.yml,.json";
  recipeFile.hidden = true;
  recipeFile.id = "pw-recipe-file";
  row.append(input, previewButton, exportButton, runButton, recipeFile);

  const suggestions = document.createElement("div");
  suggestions.className = "pw-command-suggestions";
  suggestions.setAttribute("aria-label", "Suggestions");

  const result = document.createElement("div");
  result.className = "pw-command-result";
  result.setAttribute("role", "status");
  result.hidden = true;

  bar.append(row, suggestions, result);

  let previewedText: string | null = null;
  let previewedPage = 0;
  let pendingRecipe: string | null = null;
  let debounce: ReturnType<typeof setTimeout> | null = null;
  let completionId = 0;

  function clearResult(): void {
    previewedText = null;
    pendingRecipe = null;
    result.hidden = true;
    result.replaceChildren();
    result.className = "pw-command-result";
  }

  function showMessage(kind: "ok" | "error" | "preview", lines: (string | HTMLElement)[]): void {
    result.hidden = false;
    result.className = `pw-command-result pw-command-${kind}`;
    result.replaceChildren(...lines.map((line) => (typeof line === "string" ? textLine(line) : line)));
  }

  function textLine(text: string, className = ""): HTMLElement {
    const p = document.createElement("p");
    p.textContent = text;
    if (className) p.className = className;
    return p;
  }

  function useSuggestion(text: string): void {
    input.value = text;
    input.focus();
    clearResult();
    void refreshCompletions();
  }

  async function refreshCompletions(): Promise<void> {
    const id = ++completionId;
    const text = input.value;
    let words: string[] = [];
    try {
      words = await options.api.completeCommand(text);
    } catch {
      words = [];
    }
    if (id !== completionId) return;
    suggestions.replaceChildren(
      ...words.slice(0, 12).map((word) => {
        const chip = button(word, "pw-command-chip");
        chip.addEventListener("mousedown", (event) => event.preventDefault());
        chip.addEventListener("click", () => {
          const partial = /(\S+)$/.exec(text);
          const endsWithSpace = /[\s,-]$/.test(text) || text === "";
          const base = endsWithSpace || !partial ? text : text.slice(0, text.length - partial[1].length);
          input.value = `${base}${word} `;
          input.focus();
          clearResult();
          void refreshCompletions();
        });
        return chip;
      }),
    );
  }

  function showError(error: unknown): void {
    if (!(error instanceof ApiError)) {
      showMessage("error", [error instanceof Error ? error.message : "Something went wrong."]);
      return;
    }
    const lines: (string | HTMLElement)[] = [textLine(error.detail, "pw-command-message")];
    if (error.suggestions.length) {
      const list = document.createElement("div");
      list.className = "pw-command-didyoumean";
      list.append(textLine("Did you mean:"));
      for (const suggestion of error.suggestions) {
        const choice = button(suggestion, "pw-command-suggestion");
        choice.addEventListener("click", () => useSuggestion(suggestion));
        list.append(choice);
      }
      lines.push(list);
    }
    if (error.hint) lines.push(textLine(error.hint, "pw-hint"));
    showMessage("error", lines);
  }

  async function preview(): Promise<void> {
    const text = input.value.trim();
    if (!text) return;
    try {
      const page = options.currentPage();
      const plan = await options.api.previewCommand(options.documentId, text, page);
      previewedText = text;
      previewedPage = page;
      const apply = button(plan.special ? plan.description : "Apply", "pw-command-apply");
      apply.addEventListener("click", () => void applyPreviewed());
      const cancel = button("Cancel", "pw-command-cancel");
      cancel.addEventListener("click", clearResult);
      const summary =
        plan.matches === null
          ? plan.description
          : `${plan.description}: ${plan.matches} match${plan.matches === 1 ? "" : "es"}` +
            (plan.pages.length ? ` on page${plan.pages.length === 1 ? "" : "s"} ${plan.pages.join(", ")}` : "");
      const actions = document.createElement("div");
      actions.className = "pw-command-actions";
      actions.append(apply, cancel);
      showMessage("preview", [
        textLine(summary, "pw-command-summary"),
        ...plan.warnings.map((warning) => textLine(`Warning: ${warning}`, "pw-command-warning")),
        actions,
      ]);
      apply.focus();
    } catch (error) {
      showError(error);
    }
  }

  async function applyPreviewed(): Promise<void> {
    if (pendingRecipe !== null) {
      const recipe = pendingRecipe;
      clearResult();
      try {
        const done = await options.api.runRecipe(options.documentId, recipe, false);
        await options.onApplied();
        showMessage("ok", [`Applied recipe “${done.recipe}” (${done.steps} step${done.steps === 1 ? "" : "s"}).`]);
      } catch (error) {
        showError(error);
      }
      return;
    }
    const text = previewedText;
    if (text === null) return;
    clearResult();
    try {
      // The page that was previewed, even if the viewer has moved since.
      await options.api.applyCommand(options.documentId, text, previewedPage);
      input.value = "";
      await options.onApplied();
      showMessage("ok", [`Done: ${text}`]);
      void refreshCompletions();
    } catch (error) {
      showError(error);
    }
  }

  async function exportRecipe(): Promise<void> {
    try {
      const text = await options.api.exportRecipe(options.documentId);
      const url = URL.createObjectURL(new Blob([text], { type: "application/yaml" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = `${options.title.replace(/\.pdf$/i, "")}.recipe.yaml`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      showError(error);
    }
  }

  async function dryRunRecipe(file: File): Promise<void> {
    const text = await file.text();
    try {
      const run = await options.api.runRecipe(options.documentId, text, true);
      pendingRecipe = text;
      previewedText = null;
      const apply = button("Apply recipe", "pw-command-apply");
      apply.addEventListener("click", () => void applyPreviewed());
      const cancel = button("Cancel", "pw-command-cancel");
      cancel.addEventListener("click", clearResult);
      const actions = document.createElement("div");
      actions.className = "pw-command-actions";
      actions.append(apply, cancel);
      showMessage("preview", [
        textLine(
          `Recipe “${run.recipe}”: ${run.steps} step${run.steps === 1 ? "" : "s"}, ${run.matches ?? 0} match(es)` +
            (run.pages?.length ? ` on pages ${run.pages.join(", ")}` : ""),
          "pw-command-summary",
        ),
        ...(run.warnings ?? []).map((warning) => textLine(`Warning: ${warning}`, "pw-command-warning")),
        actions,
      ]);
    } catch (error) {
      showError(error);
    }
  }

  input.addEventListener("input", () => {
    if (previewedText !== null && input.value.trim() !== previewedText) clearResult();
    if (debounce !== null) clearTimeout(debounce);
    debounce = setTimeout(() => void refreshCompletions(), COMPLETE_DEBOUNCE_MS);
  });
  input.addEventListener("focus", () => void refreshCompletions());
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      if (previewedText !== null && input.value.trim() === previewedText) {
        void applyPreviewed();
      } else {
        void preview();
      }
    } else if (event.key === "Escape") {
      event.preventDefault();
      if (!result.hidden) {
        clearResult();
      } else {
        input.blur();
      }
    }
  });
  previewButton.addEventListener("click", () => void preview());
  exportButton.addEventListener("click", () => void exportRecipe());
  runButton.addEventListener("click", () => recipeFile.click());
  recipeFile.addEventListener("change", () => {
    const file = recipeFile.files?.[0];
    recipeFile.value = "";
    if (file) void dryRunRecipe(file);
  });

  return { element: bar, focus: () => input.focus() };
}
