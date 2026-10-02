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

import type { Api, SelectMode } from "./api";
import { createCommandBar } from "./commandbar";
import { createComparePanel } from "./compare";
import { createHistoryPanel } from "./history";
import { createImageTool } from "./images";
import { createArrange } from "./arrange";
import { createInspector } from "./inspector";
import type { AlignAction } from "./snap";
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

/** EDT-16: what one click on text selects. The last choice is remembered per browser. */
const SELECT_MODE_KEY = "pdfworkerz.selectMode";
const SELECT_MODES: readonly { mode: SelectMode; label: string; key: string; title: string }[] = [
  { mode: "block", label: "Block", key: "b", title: "Select whole paragraphs (B)" },
  { mode: "line", label: "Line", key: "l", title: "Select single lines (L)" },
  { mode: "word", label: "Word", key: "w", title: "Select single words (W)" },
];

function isSelectMode(value: string | null): value is SelectMode {
  return value === "block" || value === "line" || value === "word";
}

/** Defaults to "line" when no choice was ever made or storage isn't available. */
function loadSelectMode(): SelectMode {
  try {
    const stored = window.localStorage.getItem(SELECT_MODE_KEY);
    return isSelectMode(stored) ? stored : "line";
  } catch {
    return "line";
  }
}

function storeSelectMode(mode: SelectMode): void {
  try {
    window.localStorage.setItem(SELECT_MODE_KEY, mode);
  } catch {
    // Storage unavailable: the choice won't survive a reload, but still works for this session.
  }
}

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

/** Any form control -- a <select> included, where letter keys choose an option. */
function isFormControl(target: EventTarget | null): boolean {
  return (
    isTypingTarget(target) ||
    (target instanceof HTMLElement && ["SELECT", "OPTION"].includes(target.tagName))
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
  // EDT-16: Block | Line | Word, a radio group: one is always chosen.
  const selectModeGroup = document.createElement("div");
  selectModeGroup.className = "pw-select-mode";
  selectModeGroup.setAttribute("role", "radiogroup");
  selectModeGroup.setAttribute("aria-label", "What a click on text selects");
  const selectModeButtons = new Map<SelectMode, HTMLButtonElement>();
  for (const { mode, label, title: hint } of SELECT_MODES) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.title = hint;
    button.dataset.mode = mode;
    button.setAttribute("role", "radio");
    button.addEventListener("click", () => void setSelectMode(mode));
    selectModeButtons.set(mode, button);
    selectModeGroup.appendChild(button);
  }
  // Arrow keys move within the group, as in any radio group.
  selectModeGroup.addEventListener("keydown", (event) => {
    const step = event.key === "ArrowRight" || event.key === "ArrowDown" ? 1 : event.key === "ArrowLeft" || event.key === "ArrowUp" ? -1 : 0;
    if (!step) {
      return;
    }
    event.preventDefault();
    event.stopPropagation(); // not a page turn or a nudge
    const order = SELECT_MODES.map((m) => m.mode);
    const next = order[(order.indexOf(selectMode) + step + order.length) % order.length];
    void setSelectMode(next).then(() => selectModeButtons.get(selectMode)?.focus());
  });
  const alignSelect = document.createElement("select");
  alignSelect.className = "pw-align-select";
  alignSelect.title = "Align or distribute the selected objects (Shift+click or drag a box to select several)";
  alignSelect.setAttribute("aria-label", "Align objects");
  for (const [value, label] of [
    ["", "Align…"],
    ["left", "Align left"],
    ["center", "Align center"],
    ["right", "Align right"],
    ["top", "Align top"],
    ["middle", "Align middle"],
    ["bottom", "Align bottom"],
    ["distribute-h", "Distribute horizontally"],
    ["distribute-v", "Distribute vertically"],
  ]) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    alignSelect.appendChild(option);
  }
  alignSelect.disabled = true;
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
    selectModeGroup,
    alignSelect,
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
    onApplied: async () => {
      // A command-bar edit can touch arbitrary content elsewhere on the page:
      // whatever was selected before it may no longer mean the same thing.
      deselectAll({ ask: false });
      await reloadDocument();
    },
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
  const arrange = createArrange({
    api: options.api,
    documentId: options.documentId,
    layer: editLayer,
    currentPage: () => currentPage - 1,
    onCommitted: () => void reloadDocument(),
    onSelectionChange: (count) => {
      alignSelect.disabled = count === 0;
      alignSelect.title =
        count === 0
          ? "Align or distribute the selected objects (Shift+click or drag a box to select several)"
          : count === 1
            ? "Align the selected object to the page"
            : `Align or distribute the ${count} selected objects`;
      for (const option of alignSelect.options) {
        option.disabled = option.value.startsWith("distribute") && count < 3;
      }
    },
    onEmptyClick: () => deselectAll({ ask: true }),
    // A plain click through one tool's own box replaces the selection with just that
    // object: the other two kinds' tool-level highlight, handles and inspector are no
    // longer part of what's selected, so they drop (owner, 2026-10-01 -- previously a
    // shape or image left selected this way stayed invisibly selected, and a later
    // Shift+click, marquee or Ctrl+D could sweep it into a move or copy with it).
    onSelectOnly: (kind) => {
      if (kind !== "text") overlay.deselect();
      if (kind !== "image") imageTool.deselect();
      if (kind !== "shape") shapeTool.deselect();
    },
    // A plain click narrowed a multi-object selection down to one member: that one
    // object's own tool shows its highlight, handles and inspector for it.
    onActivate: (kind, index) => {
      if (kind === "text") {
        overlay.selectIndex(index);
      } else if (kind === "shape") {
        shapeTool.selectIndex(index);
      } else {
        imageTool.selectIndex(index);
      }
    },
  });
  const overlay = createOverlay(editLayer, {
    arrange,
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
    arrange,
    api: options.api,
    documentId: options.documentId,
    inspector,
    onCommitted: () => void reloadDocument(),
    confirmDiscard: () => overlay.confirmDiscard(),
  });
  const imageTool = createImageTool(editLayer, {
    arrange,
    api: options.api,
    documentId: options.documentId,
    inspector,
    onCommitted: () => void reloadDocument(),
    confirmDiscard: () => overlay.confirmDiscard(),
  });
  const history = createHistoryPanel(historyPanel, {
    api: options.api,
    documentId: options.documentId,
    onChanged: () => {
      // UI-04: jumping through history doesn't try to carry a selection along --
      // whatever was selected before the jump may no longer mean the same thing.
      deselectAll({ ask: false });
      void reloadDocument();
    },
  });
  const compare = createComparePanel({ api: options.api, documentId: options.documentId });

  /** Clear every selection -- the shared one and each tool's own -- in one go, so
   * nothing is ever left selected in one place and not another. With `ask`, an
   * unapplied text draft is confirmed first (and the editor refocused on "no"). */
  function deselectAll(opts: { ask: boolean }): void {
    if (opts.ask && !overlay.confirmDiscard()) {
      return;
    }
    overlay.deselect();
    shapeTool.deselect();
    imageTool.deselect();
    arrange.clear();
    inspector.showEmpty();
  }

  // Clicking the grey area around the page (inside .pw-page-area but outside the
  // canvas it wraps) deselects, the same as a plain click on empty page itself.
  pageArea.addEventListener("mousedown", (event) => {
    if (event.target === pageArea) {
      deselectAll({ ask: true });
    }
  });

  const renderer = new PageRenderer();
  let pdf: PdfDocument | null = null;
  let currentPage = 1;
  let scale = 1;
  let comparing = false;
  let currentViewport: Awaited<ReturnType<PageRenderer["render"]>> = null;
  let addingText = false;
  let selectMode: SelectMode = loadSelectMode();

  function showSelectMode(): void {
    for (const [mode, button] of selectModeButtons) {
      const chosen = mode === selectMode;
      button.setAttribute("aria-checked", String(chosen));
      button.classList.toggle("pw-active", chosen);
      button.tabIndex = chosen ? 0 : -1;
    }
  }

  /** EDT-16: changing the mode clears the selection and redraws the page's text boxes. */
  async function setSelectMode(next: SelectMode): Promise<void> {
    if (next === selectMode) {
      return;
    }
    if (inspector.isDirty() && !window.confirm("Discard your unapplied text change and switch selection mode?")) {
      return;
    }
    selectMode = next;
    storeSelectMode(next);
    showSelectMode();
    overlay.setMode(next);
    // EDT-16: changing mode drops every kind of selection, not just text's -- none of
    // them mean the same thing once the page redraws in the new granularity.
    shapeTool.deselect();
    imageTool.deselect();
    arrange.clear();
    inspector.showEmpty();
    await renderCurrentPage();
  }
  showSelectMode();
  overlay.setMode(selectMode);
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
      const mode = selectMode;
      // Spans as well as units: the units say what a click selects, the spans carry the styles.
      const [spans, links, images, shapes, units] = await Promise.all([
        options.api.pageSpans(options.documentId, pageIndex),
        options.api.pageLinks(options.documentId, pageIndex),
        options.api.pageImages(options.documentId, pageIndex),
        options.api.pageShapes(options.documentId, pageIndex),
        options.api.pageTextUnits(options.documentId, pageIndex, mode),
      ]);
      if (mode !== selectMode || pageIndex !== currentPage - 1) {
        return; // the mode or the page changed meanwhile: that change's own render draws the page
      }
      editLayer.style.width = `${mainCanvas.width}px`;
      editLayer.style.height = `${mainCanvas.height}px`;
      overlay.update(pageIndex, units, spans, viewport, links);
      // After overlay.update, which clears the layer; shapes before images so
      // images stack above shapes, and both stay under the text (style.css).
      shapeTool.update(pageIndex, shapes, viewport);
      imageTool.update(pageIndex, images, viewport);
      arrange.update(pageIndex, viewport, units, images, shapes);
      // Nothing is ever selected without being highlighted: whatever arrange.update just
      // found again (a moved or copied object answers to the arrow keys and Delete) gets
      // its own tool's highlight, handles and inspector too, not just the shared outline.
      const sole = arrange.sole();
      if (sole?.kind === "text") {
        if (overlay.selectedIndex() !== sole.index) {
          overlay.selectIndex(sole.index);
        }
      } else if (sole?.kind === "shape") {
        shapeTool.selectIndex(sole.index);
      } else if (sole?.kind === "image") {
        imageTool.selectIndex(sole.index);
      } else if (arrange.count() === 0 && overlay.selectedIndex() !== null && !inspector.isDirty()) {
        overlay.deselect();
        inspector.showEmpty();
      }
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
  alignSelect.addEventListener("change", () => {
    if (alignSelect.value) {
      arrange.align(alignSelect.value as AlignAction);
    }
    alignSelect.value = "";
  });
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
    // The format painter's own Escape (overlay.ts, registered before this handler)
    // already ran and called preventDefault: this Escape cancels the painter, not
    // the selection. Otherwise, with focus on the page (isTypingTarget above has
    // already ruled out the text editor), Escape always deselects -- nothing to
    // revert or hand the keyboard back from, since that already happened in the
    // editor's own Escape handling before focus could get here (inspector.ts).
    if (event.key === "Escape" && !event.defaultPrevented) {
      event.preventDefault();
      deselectAll({ ask: false });
      return;
    }
    if (arrange.handleKey(event)) {
      event.preventDefault();
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
    } else if (!modifier && !event.altKey && !event.shiftKey && !isFormControl(event.target)) {
      // EDT-16: B / L / W choose what a click on text selects -- but not while a menu
      // (Align, Draw) has focus, where a letter picks an option.
      const choice = SELECT_MODES.find((m) => m.key === event.key.toLowerCase());
      if (choice) {
        event.preventDefault();
        void setSelectMode(choice.mode);
      }
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
  ["B / L / W", "Click selects a whole block / a line / a word"],
  ["Enter", "Commit the text being edited"],
  ["Esc", "Discard an edit, cancel a tool, close a menu; in an unchanged text box, return the keyboard to the page; press again to deselect"],
  ["Shift+click / drag on empty page", "Select several objects"],
  ["Arrows / Shift+Arrows", "Nudge the selection 1pt / 10pt (page navigation when nothing is selected)"],
  ["Ctrl+C / Ctrl+V / Ctrl+D", "Copy / paste onto this page / duplicate the selection"],
  ["Delete", "Delete the selection"],
  ["Alt while dragging", "Move without snapping to guides"],
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
