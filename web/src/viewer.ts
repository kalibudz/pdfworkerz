/**
 * UI-01: page canvas with thumbnail rail. Also owns the layout UI-02 (the
 * click-to-edit overlay) and UI-03 (the inspector panel) hang off of --
 * this is the one place that knows the current page, its render scale and
 * its viewport, which both of those need.
 *
 * Loads the open document's current bytes once (GET .../file) and does all
 * rendering client-side through pdf.js (pdf.ts) -- the main canvas and every
 * thumbnail are the same `PdfDocument`, just rendered at different scales,
 * so they always agree with each other and with what the server actually
 * holds open. Thumbnails render lazily (IntersectionObserver) so a
 * thousand-page document (SPEC.md §6) doesn't pay for a thousand
 * up-front renders it may never scroll to.
 */

import type { Api } from "./api";
import { createCommandBar } from "./commandbar";
import { createComparePanel } from "./compare";
import { createHistoryPanel } from "./history";
import { createImageTool } from "./images";
import { createInspector } from "./inspector";
import { createOverlay, viewportToMupdfPoint } from "./overlay";
import { applyWithApproval, confirmVerified } from "./approval";
import { openStyleDialog } from "./styledialog";
import { createShapeTool, type DrawKind } from "./shapes";
import { createSpellTool } from "./spelling";
import { loadPdf, PageRenderer, thumbnailViewport, type PdfDocument } from "./pdf";

export interface ViewerOptions {
  api: Api;
  documentId: string;
  pageCount: number;
  title: string;
}

const THUMBNAIL_WIDTH = 108;
const MIN_SCALE = 0.25;
const MAX_SCALE = 4;
const ZOOM_STEP = 1.15;

/** UI-08: a real, pre-existing bug found while adding more global shortcuts
 * below -- this used to check only INPUT/TEXTAREA tag names, missing that
 * overlay.ts's click-to-edit boxes are `contenteditable` `<div>`s, not
 * INPUTs. That gap meant pressing ArrowLeft/ArrowRight to move the caret
 * while typing inside a span *also* navigated pages and (via
 * preventDefault in the handler below) silently broke caret movement
 * entirely. Text is now edited in the inspector's TEXTAREA (UI-03), which
 * the tag check covers; `isContentEditable` stays for any editable element
 * added later. */
function isTypingTarget(target: EventTarget | null): boolean {
  return (
    target instanceof HTMLElement &&
    (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable)
  );
}

export async function renderViewer(container: HTMLElement, options: ViewerOptions): Promise<void> {
  container.innerHTML = "";

  const root = document.createElement("div");
  root.className = "pw-viewer";

  const toolbar = document.createElement("div");
  toolbar.className = "pw-toolbar";
  const title = document.createElement("span");
  title.className = "pw-title";
  title.textContent = options.title;
  title.title = options.title;
  const prevButton = document.createElement("button");
  prevButton.type = "button";
  prevButton.textContent = "‹ Prev";
  prevButton.title = "Previous page (← or Page Up)";
  const pageIndicator = document.createElement("span");
  pageIndicator.className = "pw-page-indicator";
  const nextButton = document.createElement("button");
  nextButton.type = "button";
  nextButton.textContent = "Next ›";
  nextButton.title = "Next page (→ or Page Down)";
  const zoomOutButton = document.createElement("button");
  zoomOutButton.type = "button";
  zoomOutButton.textContent = "−";
  zoomOutButton.title = "Zoom out (-)";
  zoomOutButton.setAttribute("aria-label", "Zoom out");
  const zoomIndicator = document.createElement("span");
  zoomIndicator.className = "pw-page-indicator";
  const zoomInButton = document.createElement("button");
  zoomInButton.type = "button";
  zoomInButton.textContent = "+";
  zoomInButton.title = "Zoom in (+)";
  zoomInButton.setAttribute("aria-label", "Zoom in");
  const compareButton = document.createElement("button");
  compareButton.type = "button";
  compareButton.textContent = "Compare";
  compareButton.title = "Before/after split view (c)";
  compareButton.setAttribute("aria-pressed", "false");
  const addTextButton = document.createElement("button");
  addTextButton.type = "button";
  addTextButton.className = "pw-add-text";
  addTextButton.textContent = "Text…";
  addTextButton.title = "Add new text: click this, then click where the text should start";
  addTextButton.setAttribute("aria-pressed", "false");
  const insertImageButton = document.createElement("button");
  insertImageButton.type = "button";
  insertImageButton.className = "pw-insert-image";
  insertImageButton.textContent = "Image…";
  insertImageButton.title = "Insert an image on this page";
  const fontsButton = document.createElement("button");
  fontsButton.type = "button";
  fontsButton.className = "pw-fonts-toggle";
  fontsButton.textContent = "Fonts";
  fontsButton.title = "Fonts to research, and your own font library";
  const spellButton = document.createElement("button");
  spellButton.type = "button";
  spellButton.className = "pw-spell-toggle";
  spellButton.textContent = "Spelling";
  spellButton.title = "Underline misspelled words on this page";
  spellButton.setAttribute("aria-pressed", "false");
  const drawSelect = document.createElement("select");
  const saveButton = document.createElement("button");
  saveButton.type = "button";
  saveButton.className = "pw-save";
  saveButton.textContent = "Save";
  saveButton.title = "Save as a new file next to the original (Ctrl+S); the original is never overwritten";
  const downloadButton = document.createElement("button");
  downloadButton.type = "button";
  downloadButton.className = "pw-download";
  downloadButton.textContent = "Download";
  downloadButton.title = "Download the edited PDF through the browser";
  const saveStatus = document.createElement("span");
  saveStatus.className = "pw-save-status";
  saveStatus.setAttribute("role", "status");
  const shortcutsButton = document.createElement("button");
  shortcutsButton.type = "button";
  shortcutsButton.className = "pw-shortcuts-toggle";
  shortcutsButton.textContent = "?";
  shortcutsButton.title = "Keyboard shortcuts (?)";
  shortcutsButton.setAttribute("aria-label", "Keyboard shortcuts");
  const shortcutsDialog = createShortcutsDialog();
  drawSelect.className = "pw-draw-select";
  drawSelect.title = "Draw a shape: pick one, then drag on the page";
  drawSelect.setAttribute("aria-label", "Draw a shape");
  for (const [value, label] of [
    ["", "Draw…"],
    ["line", "Line"],
    ["rect", "Rectangle"],
    ["ellipse", "Ellipse"],
  ]) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    drawSelect.appendChild(option);
  }
  toolbar.append(
    title,
    prevButton,
    pageIndicator,
    nextButton,
    zoomOutButton,
    zoomIndicator,
    zoomInButton,
    addTextButton,
    insertImageButton,
    drawSelect,
    spellButton,
    fontsButton,
    compareButton,
    saveButton,
    downloadButton,
    shortcutsButton,
    saveStatus,
  );

  // Disabled until the document has actually loaded below -- otherwise
  // these are clickable (and, for the keyboard shortcuts, always "live")
  // the instant .pw-viewer appears, before there's a `pdf` for their
  // handlers to act on. Confirmed the hard way: an automated click landing
  // in that window was simply lost, no error, because no listener existed
  // yet when the awaits below were sequenced *after* attaching them.
  // Listeners are attached now, up front, and stay safe to fire early
  // because every handler checks `pdf` before doing anything.
  prevButton.disabled = true;
  nextButton.disabled = true;
  zoomOutButton.disabled = true;
  zoomInButton.disabled = true;
  compareButton.disabled = true;
  insertImageButton.disabled = true;
  addTextButton.disabled = true;
  drawSelect.disabled = true;
  spellButton.disabled = true;

  const body = document.createElement("div");
  body.className = "pw-body";

  const thumbRail = document.createElement("div");
  thumbRail.className = "pw-thumbnails";

  const pageArea = document.createElement("div");
  pageArea.className = "pw-page-area";
  const canvasWrap = document.createElement("div");
  canvasWrap.className = "pw-canvas-wrap";
  const mainCanvas = document.createElement("canvas");
  const editLayer = document.createElement("div");
  editLayer.className = "pw-edit-layer";
  canvasWrap.append(mainCanvas, editLayer);

  const inspectorPanel = document.createElement("div");

  const historyPanel = document.createElement("div");

  body.append(thumbRail, pageArea, inspectorPanel);
  const commandBar = createCommandBar({
    api: options.api,
    documentId: options.documentId,
    currentPage: () => currentPage - 1,
    onApplied: () => reloadDocument(),
    title: options.title,
  });
  root.append(toolbar, commandBar.element, body, historyPanel, shortcutsDialog);
  container.appendChild(root);

  const loadingNotice = document.createElement("p");
  loadingNotice.className = "pw-hint";
  loadingNotice.style.padding = "24px";
  loadingNotice.textContent = "Loading document…";
  pageArea.replaceChildren(loadingNotice);

  const inspector = createInspector(inspectorPanel, {
    onCopyStyle: () => overlay.armPainter(),
    onDraftChange: (draft) => overlay.draftChanged(draft),
    onApply: (draft) => overlay.apply(draft),
    loadFamilies: () => options.api.fonts(),
    onAddLink: () => overlay.addLink(),
    onEditLink: (link) => overlay.editLink(link),
    onRemoveLink: (link) => overlay.removeLink(link),
    onImageAction: (action) => imageTool.act(action),
    onShapeStyle: (style) => shapeTool.restyle(style),
    onShapeDelete: () => shapeTool.deleteSelected(),
  });
  const overlay = createOverlay(editLayer, {
    api: options.api,
    documentId: options.documentId,
    inspector,
    onCommitted: () => void reloadDocument(),
  });
  const spellTool = createSpellTool(editLayer, {
    api: options.api,
    documentId: options.documentId,
    onCommitted: () => void reloadDocument(),
    onCount: (count) => {
      spellButton.textContent = count === null ? "Spelling" : `Spelling (${count})`;
    },
  });
  const shapeTool = createShapeTool(editLayer, {
    api: options.api,
    documentId: options.documentId,
    inspector,
    onCommitted: () => void reloadDocument(),
  });
  const imageTool = createImageTool(editLayer, {
    api: options.api,
    documentId: options.documentId,
    inspector,
    onCommitted: () => void reloadDocument(),
  });
  const history = createHistoryPanel(historyPanel, {
    api: options.api,
    documentId: options.documentId,
    onChanged: () => void reloadDocument(),
  });
  const compare = createComparePanel({ api: options.api, documentId: options.documentId });

  const renderer = new PageRenderer();
  let pdf: PdfDocument | null = null;
  let currentPage = 1;
  let scale = 1;
  let comparing = false;
  let currentViewport: Awaited<ReturnType<PageRenderer["render"]>> = null;
  let addingText = false;
  // Page operations (delete, insert, merge, ...) change the page count, so it is state, not an option.
  let pageCount = options.pageCount;
  let thumbnails = createThumbnailRail(thumbRail, pageCount, (pageNumber) => void goToPage(pageNumber), movePage);
  let thumbButtons = thumbnails.buttons;

  function updateToolbar(): void {
    pageIndicator.textContent = `${currentPage} / ${pageCount}`;
    zoomIndicator.textContent = `${Math.round(scale * 100)}%`;
    prevButton.disabled = currentPage <= 1;
    nextButton.disabled = currentPage >= pageCount;
  }

  function updateActiveThumbnail(): void {
    for (const [index, button] of thumbButtons.entries()) {
      button.classList.toggle("pw-active", index + 1 === currentPage);
    }
    const active = thumbButtons[currentPage - 1];
    active?.scrollIntoView({ block: "nearest" });
  }

  async function renderCurrentPage(): Promise<void> {
    if (!pdf) {
      return;
    }
    const viewport = await renderer.render(pdf, currentPage, mainCanvas, scale);
    currentViewport = viewport ?? currentViewport;
    updateToolbar();
    updateActiveThumbnail();
    if (viewport) {
      const pageIndex = currentPage - 1; // pdf.js pages are 1-based; the API's page_index is 0-based
      const [spans, links, images, shapes] = await Promise.all([
        options.api.pageSpans(options.documentId, pageIndex),
        options.api.pageLinks(options.documentId, pageIndex),
        options.api.pageImages(options.documentId, pageIndex),
        options.api.pageShapes(options.documentId, pageIndex),
      ]);
      editLayer.style.width = `${mainCanvas.width}px`;
      editLayer.style.height = `${mainCanvas.height}px`;
      overlay.update(pageIndex, spans, viewport, links);
      // After overlay.update, which clears the layer; shapes before images so
      // images stack above shapes, and both stay under the text (style.css).
      shapeTool.update(pageIndex, shapes, viewport);
      imageTool.update(pageIndex, images, viewport);
      await spellTool.update(pageIndex, viewport);
    }
  }

  /** The document changed server-side -- a committed edit (UI-02) or an
   * undo/redo (UI-04) -- so span indices and this page's rendered content
   * are both stale now; everything that depends on either is reloaded from
   * scratch rather than guessed at. Also the one place that refreshes the
   * history panel, so every caller gets it for free. */
  async function reloadDocument(): Promise<void> {
    overlay.cancelPainter();
    const bytes = await options.api.documentFile(options.documentId);
    pdf = await loadPdf(bytes);
    if (pdf.numPages !== pageCount) {
      pageCount = pdf.numPages;
      currentPage = Math.min(currentPage, pageCount);
      thumbRail.replaceChildren();
      thumbnails = createThumbnailRail(thumbRail, pageCount, (pageNumber) => void goToPage(pageNumber), movePage);
      thumbButtons = thumbnails.buttons;
    }
    thumbnails.setDocument(pdf);
    await renderCurrentPage();
    await history.refresh();
    if (comparing) {
      await compare.show(currentPage - 1);
    }
  }

  /** UI-07: move one page (0-based `from`) so it ends up at 0-based position `to`, through the
   * journaled move_pages op, so it undoes like any edit. The moved page stays selected. */
  async function movePage(from: number, to: number): Promise<void> {
    if (from === to || to < 0 || to >= pageCount) {
      return;
    }
    try {
      await options.api.applyOp(options.documentId, { op: "move_pages", page_indices: [from], to });
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "The page could not be moved.");
      return;
    }
    currentPage = to + 1;
    await reloadDocument();
    thumbButtons[to]?.focus();
  }

  async function goToPage(pageNumber: number): Promise<void> {
    if (!pdf) {
      return;
    }
    const clamped = Math.min(Math.max(pageNumber, 1), pageCount);
    if (clamped === currentPage) {
      return;
    }
    currentPage = clamped;
    await renderCurrentPage();
    if (comparing) {
      await compare.show(currentPage - 1);
    }
  }

  /** UI-05: toggles between the normal click-to-edit canvas and the
   * before/after split view for whatever page is currently shown. The two
   * are mutually exclusive rather than layered -- editing while comparing
   * (or vice versa) isn't a combination SPEC.md's mockup asks for, and
   * keeping them exclusive avoids overlay.ts and compare.ts having to
   * coordinate shared state neither of them otherwise needs to know about. */
  async function setComparing(next: boolean): Promise<void> {
    comparing = next;
    compareButton.classList.toggle("pw-active", comparing);
    compareButton.setAttribute("aria-pressed", String(comparing));
    if (comparing) {
      pageArea.replaceChildren(compare.element);
      await compare.show(currentPage - 1);
    } else {
      pageArea.replaceChildren(canvasWrap);
    }
  }

  async function setScale(newScale: number): Promise<void> {
    if (!pdf) {
      return;
    }
    scale = Math.min(Math.max(newScale, MIN_SCALE), MAX_SCALE);
    await renderCurrentPage();
  }

  prevButton.addEventListener("click", () => void goToPage(currentPage - 1));
  nextButton.addEventListener("click", () => void goToPage(currentPage + 1));
  zoomOutButton.addEventListener("click", () => void setScale(scale / ZOOM_STEP));
  zoomInButton.addEventListener("click", () => void setScale(scale * ZOOM_STEP));
  compareButton.addEventListener("click", () => void setComparing(!comparing));
  insertImageButton.addEventListener("click", () => imageTool.insert());
  spellButton.addEventListener("click", () => {
    const next = spellButton.getAttribute("aria-pressed") !== "true";
    spellButton.setAttribute("aria-pressed", String(next));
    spellButton.classList.toggle("pw-active", next);
    void spellTool.setEnabled(next);
  });
  drawSelect.addEventListener("change", () => shapeTool.setDrawMode((drawSelect.value || null) as DrawKind | null));
  shortcutsButton.addEventListener("click", () => shortcutsDialog.showModal());
  // Loaded on first use: it's rarely opened, and keeps the main bundle small.
  fontsButton.addEventListener("click", () => {
    void import("./fontsdialog").then(({ openFontsDialog }) =>
      openFontsDialog({
        api: options.api,
        documentId: options.documentId,
        onLibraryChanged: () => inspector.reloadFamilies(),
      }),
    );
  });

  /** EDT-03: arm, then the next click on the page picks the new text's baseline start. */
  function setAddingText(next: boolean): void {
    addingText = next;
    editLayer.classList.toggle("pw-adding-text", next);
    addTextButton.classList.toggle("pw-active", next);
    addTextButton.setAttribute("aria-pressed", String(next));
  }

  async function addTextAt(x: number, y: number): Promise<void> {
    const viewport = currentViewport;
    if (!viewport) {
      return;
    }
    const [px, py] = viewportToMupdfPoint(viewport, x, y);
    const choice = await openStyleDialog({
      title: "Add text",
      families: await options.api.fonts(),
      initial: { font: "Helvetica", size: 12, color: [0, 0, 0], bold: false, italic: false },
      withText: true,
      submitLabel: "Add",
    });
    if (!choice || !choice.text) {
      return;
    }
    try {
      const applied = await applyWithApproval(options.api, options.documentId, {
        op: "insert_text",
        page_index: currentPage - 1,
        text: choice.text,
        position: [px, py],
        font: choice.font,
        size: choice.size,
        color: choice.color,
        bold: choice.bold,
        italic: choice.italic,
      });
      if (!applied) {
        return;
      }
      await confirmVerified(options.api, options.documentId, applied.result);
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "The text could not be added.");
      return;
    }
    await reloadDocument();
  }

  addTextButton.addEventListener("click", () => setAddingText(!addingText));
  // Capture phase: while armed, a click anywhere on the page places text instead of
  // selecting whatever span, image or shape is underneath.
  // On the wrapper, not the edit layer: the layer itself doesn't take pointer events (only
  // the boxes inside it do), so a click on empty page area reaches the canvas beneath it.
  canvasWrap.addEventListener(
    "mousedown",
    (event) => {
      if (!addingText) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      const rect = editLayer.getBoundingClientRect();
      setAddingText(false);
      void addTextAt(event.clientX - rect.left, event.clientY - rect.top);
    },
    true,
  );

  async function save(): Promise<void> {
    saveButton.disabled = true;
    saveStatus.textContent = "Saving…";
    try {
      const result = await options.api.save(options.documentId);
      saveStatus.textContent = `Saved to ${result.path}`;
      saveStatus.title = result.path;
      if (result.note) {
        window.alert(`Saved to ${result.path}.\n\nNote: ${result.note}.`);
      }
    } catch (error) {
      saveStatus.textContent = "";
      window.alert(error instanceof Error ? error.message : "The document could not be saved.");
    } finally {
      saveButton.disabled = false;
    }
  }

  async function download(): Promise<void> {
    const bytes = await options.api.documentDownload(options.documentId);
    const url = URL.createObjectURL(new Blob([bytes], { type: "application/pdf" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = options.title.replace(/\.pdf$/i, "") + ".edited.pdf";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  saveButton.addEventListener("click", () => void save());
  downloadButton.addEventListener("click", () => void download());

  // UI-08: every shortcut below is a keyboard path to an action the toolbar
  // or history panel already exposes by mouse -- none of them do anything
  // a button click couldn't already do, so there's nothing here for
  // reloadDocument/goToPage/setScale/history's own handlers to coordinate
  // with beyond what they already do. Guarded on `pdf` up front: the
  // buttons these mirror are themselves disabled until the document has
  // loaded, and a keyboard shortcut shouldn't be able to act sooner than a
  // click could.
  const keyHandler = (event: KeyboardEvent): void => {
    // While the shortcuts dialog is open it owns the keyboard (Escape closes it natively).
    if (isTypingTarget(event.target) || !pdf || shortcutsDialog.open) {
      return;
    }
    if (event.key === "?") {
      event.preventDefault();
      shortcutsDialog.showModal();
      return;
    }
    if (event.key === "/") {
      event.preventDefault();
      commandBar.focus();
      return;
    }
    if (event.key === "Escape" && addingText) {
      setAddingText(false);
      return;
    }
    if (event.key === "Escape" && drawSelect.value) {
      drawSelect.value = "";
      shapeTool.setDrawMode(null);
      return;
    }
    const modifier = event.ctrlKey || event.metaKey;
    if (modifier && event.key.toLowerCase() === "s") {
      event.preventDefault(); // not the browser's "save page as"
      void save();
    } else if (modifier && event.key.toLowerCase() === "z") {
      event.preventDefault();
      if (event.shiftKey) {
        history.triggerRedo();
      } else {
        history.triggerUndo();
      }
    } else if (modifier && event.key.toLowerCase() === "y") {
      event.preventDefault();
      history.triggerRedo();
    } else if (event.key === "ArrowRight" || event.key === "PageDown") {
      event.preventDefault();
      void goToPage(currentPage + 1);
    } else if (event.key === "ArrowLeft" || event.key === "PageUp") {
      event.preventDefault();
      void goToPage(currentPage - 1);
    } else if (event.key === "Home") {
      event.preventDefault();
      void goToPage(1);
    } else if (event.key === "End") {
      event.preventDefault();
      void goToPage(pageCount);
    } else if (event.key === "+" || event.key === "=") {
      event.preventDefault();
      void setScale(scale * ZOOM_STEP);
    } else if (event.key === "-" || event.key === "_") {
      event.preventDefault();
      void setScale(scale / ZOOM_STEP);
    } else if (!modifier && event.key.toLowerCase() === "c") {
      event.preventDefault();
      void setComparing(!comparing);
    }
  };
  window.addEventListener("keydown", keyHandler);

  const bytes = await options.api.documentFile(options.documentId);
  pdf = await loadPdf(bytes);
  pageArea.replaceChildren(canvasWrap);
  scale = await fitWidthScale(pdf, pageArea.clientWidth);
  zoomOutButton.disabled = false;
  zoomInButton.disabled = false;
  compareButton.disabled = false;
  insertImageButton.disabled = false;
  addTextButton.disabled = false;
  drawSelect.disabled = false;
  spellButton.disabled = false;

  thumbnails.setDocument(pdf);

  await renderCurrentPage();
  await history.refresh();
}

const SHORTCUTS: readonly [string, string][] = [
  ["→ / Page Down", "Next page"],
  ["← / Page Up", "Previous page"],
  ["Home / End", "First / last page"],
  ["+ / −", "Zoom in / out"],
  ["Ctrl+S", "Save as a new file next to the original"],
  ["Ctrl+Z", "Undo"],
  ["Ctrl+Shift+Z / Ctrl+Y", "Redo"],
  ["C", "Before/after compare view"],
  ["Enter", "Commit the text being edited"],
  ["Esc", "Discard an edit, cancel a tool, close a menu"],
  ["/", "Type a command (Enter previews, Enter again applies)"],
  ["?", "Show this list"],
];

function createShortcutsDialog(): HTMLDialogElement {
  const dialog = document.createElement("dialog");
  dialog.className = "pw-shortcuts";
  dialog.setAttribute("aria-labelledby", "pw-shortcuts-title");
  const heading = document.createElement("h2");
  heading.id = "pw-shortcuts-title";
  heading.textContent = "Keyboard shortcuts";
  const list = document.createElement("dl");
  for (const [keys, action] of SHORTCUTS) {
    const dt = document.createElement("dt");
    const kbd = document.createElement("kbd");
    kbd.textContent = keys;
    dt.appendChild(kbd);
    const dd = document.createElement("dd");
    dd.textContent = action;
    list.append(dt, dd);
  }
  const form = document.createElement("form");
  form.method = "dialog";
  const close = document.createElement("button");
  close.type = "submit";
  close.textContent = "Close";
  form.appendChild(close);
  dialog.append(heading, list, form);
  return dialog;
}

interface ThumbnailRail {
  buttons: HTMLButtonElement[];
  /** Point every thumbnail at a (re)loaded document. Buttons are kept, so the
   * rail doesn't flicker or lose its scroll position; each keeps its old image
   * until its lazy re-render (only visible ones re-render right away). */
  setDocument(pdf: PdfDocument): void;
}

function createThumbnailRail(
  rail: HTMLElement,
  pageCount: number,
  onSelect: (pageNumber: number) => void,
  onMove: (from: number, to: number) => Promise<void>,
): ThumbnailRail {
  const buttons: HTMLButtonElement[] = [];
  let observer: IntersectionObserver | null = null;

  for (let pageNumber = 1; pageNumber <= pageCount; pageNumber++) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "pw-thumb";
    button.dataset.page = String(pageNumber);

    const canvas = document.createElement("canvas");
    canvas.width = THUMBNAIL_WIDTH;
    canvas.height = Math.round(THUMBNAIL_WIDTH * 1.29); // a plausible height until the real render sizes it
    const numberLabel = document.createElement("span");
    numberLabel.className = "pw-thumb-number";
    numberLabel.textContent = String(pageNumber);
    button.append(canvas, numberLabel);

    button.addEventListener("click", () => onSelect(pageNumber));
    // UI-07: drag a thumbnail onto another to move the page there; Alt+Up/Down does the same by keyboard.
    button.draggable = true;
    button.title = `Page ${pageNumber}: drag, or Alt+Up/Down, to move it`;
    button.addEventListener("dragstart", (event) => {
      event.dataTransfer?.setData("application/x-pw-page", String(pageNumber - 1));
      button.classList.add("pw-dragging");
    });
    button.addEventListener("dragend", () => button.classList.remove("pw-dragging"));
    button.addEventListener("dragover", (event) => {
      if (event.dataTransfer?.types.includes("application/x-pw-page")) {
        event.preventDefault();
        button.classList.add("pw-drop-target");
      }
    });
    button.addEventListener("dragleave", () => button.classList.remove("pw-drop-target"));
    button.addEventListener("drop", (event) => {
      event.preventDefault();
      button.classList.remove("pw-drop-target");
      const from = Number(event.dataTransfer?.getData("application/x-pw-page"));
      if (Number.isInteger(from)) {
        void onMove(from, pageNumber - 1); // the dragged page takes this page's place
      }
    });
    button.addEventListener("keydown", (event) => {
      if (event.altKey && (event.key === "ArrowUp" || event.key === "ArrowDown")) {
        event.preventDefault();
        void onMove(pageNumber - 1, pageNumber - 1 + (event.key === "ArrowUp" ? -1 : 1));
      }
    });
    rail.appendChild(button);
    buttons.push(button);
  }

  return {
    buttons,
    setDocument(pdf) {
      observer?.disconnect();
      const renderedPages = new Set<number>();
      const current = new IntersectionObserver(
        (entries) => {
          for (const entry of entries) {
            if (!entry.isIntersecting) {
              continue;
            }
            const pageNumber = Number((entry.target as HTMLElement).dataset.page);
            current.unobserve(entry.target);
            void renderThumbnail(pdf, pageNumber, entry.target as HTMLElement, renderedPages);
          }
        },
        { root: rail, rootMargin: "200px 0px" },
      );
      observer = current;
      for (const button of buttons) {
        current.observe(button);
      }
    },
  };
}

async function renderThumbnail(
  pdf: PdfDocument,
  pageNumber: number,
  buttonEl: HTMLElement,
  renderedPages: Set<number>,
): Promise<void> {
  if (renderedPages.has(pageNumber)) {
    return;
  }
  renderedPages.add(pageNumber);
  const canvas = buttonEl.querySelector("canvas");
  if (!canvas) {
    return;
  }
  const page = await pdf.getPage(pageNumber);
  const viewport = thumbnailViewport(page, THUMBNAIL_WIDTH);
  const context = canvas.getContext("2d");
  if (!context) {
    return;
  }
  canvas.width = viewport.width;
  canvas.height = viewport.height;
  await page.render({ canvasContext: context, viewport, canvas }).promise;
}

async function fitWidthScale(pdf: PdfDocument, availableWidth: number): Promise<number> {
  const firstPage = await pdf.getPage(1);
  const unscaledWidth = firstPage.getViewport({ scale: 1 }).width;
  const usableWidth = Math.max(availableWidth - 48, 200);
  return Math.min(Math.max(usableWidth / unscaledWidth, MIN_SCALE), MAX_SCALE);
}
