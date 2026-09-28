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
import { createComparePanel } from "./compare";
import { createHistoryPanel } from "./history";
import { createInspector } from "./inspector";
import { createOverlay } from "./overlay";
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

function isTypingTarget(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && (target.tagName === "INPUT" || target.tagName === "TEXTAREA");
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
  const pageIndicator = document.createElement("span");
  pageIndicator.className = "pw-page-indicator";
  const nextButton = document.createElement("button");
  nextButton.type = "button";
  nextButton.textContent = "Next ›";
  const zoomOutButton = document.createElement("button");
  zoomOutButton.type = "button";
  zoomOutButton.textContent = "−";
  zoomOutButton.title = "Zoom out";
  const zoomIndicator = document.createElement("span");
  zoomIndicator.className = "pw-page-indicator";
  const zoomInButton = document.createElement("button");
  zoomInButton.type = "button";
  zoomInButton.textContent = "+";
  zoomInButton.title = "Zoom in";
  const compareButton = document.createElement("button");
  compareButton.type = "button";
  compareButton.textContent = "Compare";
  compareButton.title = "Before/after split view (UI-05)";
  compareButton.setAttribute("aria-pressed", "false");
  toolbar.append(
    title,
    prevButton,
    pageIndicator,
    nextButton,
    zoomOutButton,
    zoomIndicator,
    zoomInButton,
    compareButton,
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
  root.append(toolbar, body, historyPanel);
  container.appendChild(root);

  const loadingNotice = document.createElement("p");
  loadingNotice.className = "pw-hint";
  loadingNotice.style.padding = "24px";
  loadingNotice.textContent = "Loading document…";
  pageArea.replaceChildren(loadingNotice);

  const inspector = createInspector(inspectorPanel);
  const overlay = createOverlay(editLayer, {
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
  const thumbButtons: HTMLButtonElement[] = [];

  function updateToolbar(): void {
    pageIndicator.textContent = `${currentPage} / ${options.pageCount}`;
    zoomIndicator.textContent = `${Math.round(scale * 100)}%`;
    prevButton.disabled = currentPage <= 1;
    nextButton.disabled = currentPage >= options.pageCount;
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
    updateToolbar();
    updateActiveThumbnail();
    if (viewport) {
      const pageIndex = currentPage - 1; // pdf.js pages are 1-based; the API's page_index is 0-based
      const spans = await options.api.pageSpans(options.documentId, pageIndex);
      editLayer.style.width = `${mainCanvas.width}px`;
      editLayer.style.height = `${mainCanvas.height}px`;
      overlay.update(pageIndex, spans, viewport);
    }
  }

  /** The document changed server-side -- a committed edit (UI-02) or an
   * undo/redo (UI-04) -- so span indices and this page's rendered content
   * are both stale now; everything that depends on either is reloaded from
   * scratch rather than guessed at. Also the one place that refreshes the
   * history panel, so every caller gets it for free. */
  async function reloadDocument(): Promise<void> {
    const bytes = await options.api.documentFile(options.documentId);
    pdf = await loadPdf(bytes);
    thumbRail.innerHTML = "";
    thumbButtons.length = 0;
    buildThumbnailRail(thumbRail, pdf, options.pageCount, thumbButtons, (pageNumber) => void goToPage(pageNumber));
    await renderCurrentPage();
    await history.refresh();
    if (comparing) {
      await compare.show(currentPage - 1);
    }
  }

  async function goToPage(pageNumber: number): Promise<void> {
    if (!pdf) {
      return;
    }
    const clamped = Math.min(Math.max(pageNumber, 1), options.pageCount);
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

  const keyHandler = (event: KeyboardEvent): void => {
    if (isTypingTarget(event.target)) {
      return;
    }
    if (event.key === "ArrowRight" || event.key === "PageDown") {
      event.preventDefault();
      void goToPage(currentPage + 1);
    } else if (event.key === "ArrowLeft" || event.key === "PageUp") {
      event.preventDefault();
      void goToPage(currentPage - 1);
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

  buildThumbnailRail(thumbRail, pdf, options.pageCount, thumbButtons, (pageNumber) => void goToPage(pageNumber));

  await renderCurrentPage();
  await history.refresh();
}

function buildThumbnailRail(
  rail: HTMLElement,
  pdf: PdfDocument,
  pageCount: number,
  thumbButtons: HTMLButtonElement[],
  onSelect: (pageNumber: number) => void,
): void {
  const renderedPages = new Set<number>();
  const observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) {
          continue;
        }
        const pageNumber = Number((entry.target as HTMLElement).dataset.page);
        observer.unobserve(entry.target);
        void renderThumbnail(pdf, pageNumber, entry.target as HTMLElement, renderedPages);
      }
    },
    { root: rail, rootMargin: "200px 0px" },
  );

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
    rail.appendChild(button);
    thumbButtons.push(button);
    observer.observe(button);
  }
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
