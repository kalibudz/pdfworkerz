/**
 * The Fonts dialog: the fonts-to-research list (every font an edit could only
 * approximate, engine.fonts.research) and the user's own font library
 * (engine.fonts.library), with the two ways to add to it -- take the complete
 * font this document embeds, or pick a font file on this machine. A native
 * <dialog>, like styledialog.ts.
 */

import type { Api, FontToResearch, LibraryFont } from "./api";

export interface FontsDialogOptions {
  api: Api;
  documentId: string;
  /** A font was added: the font lists elsewhere must be reloaded. */
  onLibraryChanged: () => void;
}

const TIER_WORDS: Record<string, string> = { approximate: "approximated", fallback: "fell back" };

function cell(text: string, className = ""): HTMLTableCellElement {
  const td = document.createElement("td");
  td.textContent = text;
  if (className) {
    td.className = className;
  }
  return td;
}

function button(label: string, className: string, onClick: () => void): HTMLButtonElement {
  const el = document.createElement("button");
  el.type = "button";
  el.className = className;
  el.textContent = label;
  el.addEventListener("click", onClick);
  return el;
}

export function openFontsDialog(options: FontsDialogOptions): void {
  const dialog = document.createElement("dialog");
  dialog.className = "pw-fonts-dialog";
  dialog.setAttribute("aria-labelledby", "pw-fonts-title");

  const heading = document.createElement("h2");
  heading.id = "pw-fonts-title";
  heading.textContent = "Fonts";

  const status = document.createElement("p");
  status.className = "pw-fonts-status";
  status.setAttribute("role", "status");

  const researchHeading = document.createElement("h3");
  researchHeading.textContent = "Fonts to research";
  const researchHint = document.createElement("p");
  researchHint.className = "pw-hint";
  researchHint.textContent =
    "Fonts that edits could not match exactly. Add one to your library and later edits use it exactly.";
  const researchTable = document.createElement("table");
  researchTable.className = "pw-fonts-research";

  const libraryHeading = document.createElement("h3");
  libraryHeading.textContent = "Your font library";
  const libraryHint = document.createElement("p");
  libraryHint.className = "pw-hint";
  libraryHint.textContent =
    "Kept on this computer only, never shared. For fonts that can't ship with PDFWorkerz, such as licensed ones.";
  const libraryTable = document.createElement("table");
  libraryTable.className = "pw-fonts-library";

  const addFile = button("Add font file…", "pw-fonts-add-file", () => {
    const path = window.prompt("Path to a .ttf or .otf font file on this computer:");
    if (path?.trim()) {
      void run(`Adding ${path.trim()}…`, () => options.api.addFontFile(path.trim()));
    }
  });

  const close = document.createElement("form");
  close.method = "dialog";
  const closeButton = document.createElement("button");
  closeButton.type = "submit";
  closeButton.textContent = "Close";
  close.appendChild(closeButton);

  dialog.append(
    heading,
    status,
    researchHeading,
    researchHint,
    researchTable,
    libraryHeading,
    libraryHint,
    libraryTable,
    addFile,
    close,
  );
  document.body.appendChild(dialog);
  dialog.addEventListener("close", () => dialog.remove());

  async function run(message: string, action: () => Promise<LibraryFont>): Promise<void> {
    status.textContent = message;
    try {
      const added = await action();
      status.textContent = `Added ${added.postscript_name} (${added.family} ${added.style}) to your library.`;
      options.onLibraryChanged();
    } catch (error) {
      status.textContent = error instanceof Error ? error.message : "The font could not be added.";
    }
    await refresh();
  }

  function researchRow(row: FontToResearch): HTMLTableRowElement {
    const tr = document.createElement("tr");
    tr.dataset.font = row.font;
    const actions = document.createElement("td");
    if (row.harvestable_here) {
      actions.appendChild(
        button("Add from this document", "pw-fonts-harvest", () => {
          void run(`Adding ${row.font} from this document…`, () =>
            options.api.harvestFont(options.documentId, row.font),
          );
        }),
      );
    } else if (row.harvest_problem) {
      actions.textContent = "Not in this document completely";
      actions.title = row.harvest_problem;
    }
    tr.append(
      cell(row.font, "pw-fonts-name"),
      cell(`${TIER_WORDS[row.best_tier] ?? row.best_tier}, ${row.times_seen}×`),
      cell(row.note),
      actions,
    );
    return tr;
  }

  function libraryRow(font: LibraryFont): HTMLTableRowElement {
    const tr = document.createElement("tr");
    tr.dataset.font = font.postscript_name;
    tr.title = font.copyright;
    tr.append(
      cell(font.postscript_name, "pw-fonts-name"),
      cell(`${font.family} ${font.style}`),
      cell(`from ${font.source}`),
    );
    return tr;
  }

  function emptyRow(text: string): HTMLTableRowElement {
    const tr = document.createElement("tr");
    tr.appendChild(cell(text, "pw-hint"));
    return tr;
  }

  async function refresh(): Promise<void> {
    try {
      const [research, library] = await Promise.all([
        options.api.fontsToResearch(options.documentId),
        options.api.fontLibrary(),
      ]);
      researchTable.replaceChildren(...(research.length ? research.map(researchRow) : [emptyRow("Nothing to research.")]));
      libraryTable.replaceChildren(
        ...(library.length ? library.map(libraryRow) : [emptyRow("No fonts added yet.")]),
      );
    } catch (error) {
      status.textContent = error instanceof Error ? error.message : "The font lists could not be loaded.";
    }
  }

  dialog.showModal();
  void refresh();
}
