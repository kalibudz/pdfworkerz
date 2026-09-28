/**
 * UI-05: the before/after split view (SPEC.md section 8.4 -- "synchronized
 * scrolling, and a diff overlay toggle"). Both sides are the server's own
 * authoritative PNG render (`GET .../render` and its `/original` sibling,
 * UI-05's one backend addition) -- deliberately not a second pdf.js
 * instance like the main canvas uses, since a before/after comparison
 * wants the exact render that was (or would be) saved, not the browser's
 * own approximation of it.
 *
 * The diff overlay is computed client-side (draw both PNGs to canvases,
 * compare pixels, highlight what differs) rather than by reusing
 * engine/verify.py's numpy-based pixel-diff harness -- that module is
 * Python-only and built for FNT-12's per-edit verification and the P1
 * regression suite, not reachable from the browser without a new
 * endpoint. A same-size, same-DPI image comparison is simple enough to do
 * directly in JS without one.
 */

import type { Api } from "./api";

export interface ComparePanelOptions {
  api: Api;
  documentId: string;
}

export interface ComparePanelHandle {
  /** The panel's root element -- mount this in place of the normal canvas
   * when comparing is toggled on. */
  readonly element: HTMLElement;
  /** Fetches and displays both renders of one page (0-based), replacing
   * whatever was shown before. */
  show(pageIndex: number): Promise<void>;
}

interface DiffSummary {
  matches: boolean;
  changedFraction: number;
}

// A per-channel step tolerance, not 0: PNG re-encoding and anti-aliasing
// introduce a little noise even between two genuinely identical renders,
// and without this a "Pages are identical" comparison would falsely
// report a diff on every single load.
const CHANNEL_TOLERANCE = 24;
const OVERLAY_COLOR = [255, 0, 0] as const;
const OVERLAY_ALPHA = 140;

function loadImage(img: HTMLImageElement, url: string): Promise<void> {
  return new Promise((resolve, reject) => {
    img.onload = () => resolve();
    img.onerror = () => reject(new Error(`failed to load rendered page image: ${url}`));
    img.src = url;
  });
}

function imageData(img: HTMLImageElement): ImageData {
  const canvas = document.createElement("canvas");
  canvas.width = img.naturalWidth;
  canvas.height = img.naturalHeight;
  const context = canvas.getContext("2d");
  if (!context) {
    throw new Error("2d canvas context unavailable");
  }
  context.drawImage(img, 0, 0);
  return context.getImageData(0, 0, canvas.width, canvas.height);
}

/** Compares two same-size renders pixel by pixel, returning a translucent
 * red overlay (opaque only where they differ) plus a summary usable
 * without ever showing that overlay. Returns null if the two images
 * aren't the same size -- nothing in this app resizes a page today, but
 * this never assumes that stays true. */
function diffImages(before: HTMLImageElement, after: HTMLImageElement): { overlay: HTMLCanvasElement; summary: DiffSummary } | null {
  if (before.naturalWidth !== after.naturalWidth || before.naturalHeight !== after.naturalHeight) {
    return null;
  }
  const beforeData = imageData(before);
  const afterData = imageData(after);
  const overlay = document.createElement("canvas");
  overlay.width = beforeData.width;
  overlay.height = beforeData.height;
  const overlayContext = overlay.getContext("2d");
  if (!overlayContext) {
    throw new Error("2d canvas context unavailable");
  }
  const overlayData = overlayContext.createImageData(beforeData.width, beforeData.height);

  let changedPixels = 0;
  const totalPixels = beforeData.width * beforeData.height;
  for (let i = 0; i < beforeData.data.length; i += 4) {
    const dr = Math.abs(beforeData.data[i] - afterData.data[i]);
    const dg = Math.abs(beforeData.data[i + 1] - afterData.data[i + 1]);
    const db = Math.abs(beforeData.data[i + 2] - afterData.data[i + 2]);
    if (dr > CHANNEL_TOLERANCE || dg > CHANNEL_TOLERANCE || db > CHANNEL_TOLERANCE) {
      changedPixels++;
      overlayData.data[i] = OVERLAY_COLOR[0];
      overlayData.data[i + 1] = OVERLAY_COLOR[1];
      overlayData.data[i + 2] = OVERLAY_COLOR[2];
      overlayData.data[i + 3] = OVERLAY_ALPHA;
    }
  }
  overlayContext.putImageData(overlayData, 0, 0);

  return {
    overlay,
    summary: { matches: changedPixels === 0, changedFraction: changedPixels / totalPixels },
  };
}

export function createComparePanel(options: ComparePanelOptions): ComparePanelHandle {
  const element = document.createElement("div");
  element.className = "pw-compare";

  const header = document.createElement("div");
  header.className = "pw-compare-header";
  const status = document.createElement("span");
  status.className = "pw-compare-status";
  const diffLabel = document.createElement("label");
  diffLabel.className = "pw-compare-diff-label";
  const diffToggle = document.createElement("input");
  diffToggle.type = "checkbox";
  diffToggle.disabled = true;
  diffLabel.append(diffToggle, document.createTextNode(" Show diff"));
  header.append(status, diffLabel);

  const panes = document.createElement("div");
  panes.className = "pw-compare-panes";

  function buildPane(label: string): { pane: HTMLElement; scroll: HTMLElement; img: HTMLImageElement } {
    const pane = document.createElement("div");
    pane.className = "pw-compare-pane";
    const heading = document.createElement("div");
    heading.className = "pw-compare-label";
    heading.textContent = label;
    const scroll = document.createElement("div");
    scroll.className = "pw-compare-scroll";
    const img = document.createElement("img");
    img.className = "pw-compare-img";
    scroll.appendChild(img);
    pane.append(heading, scroll);
    return { pane, scroll, img };
  }

  const before = buildPane("Before");
  const after = buildPane("After");

  const afterStack = document.createElement("div");
  afterStack.className = "pw-compare-after-stack";
  const overlayCanvas = document.createElement("canvas");
  overlayCanvas.className = "pw-compare-diff-overlay";
  overlayCanvas.hidden = true;
  after.img.replaceWith(afterStack);
  afterStack.append(after.img, overlayCanvas);

  panes.append(before.pane, after.pane);
  element.append(header, panes);

  // Synchronized scrolling: mirror one pane's scroll position onto the
  // other. The `syncing` flag stops the mirrored scroll event from
  // bouncing straight back and forth between the two listeners.
  let syncing = false;
  function linkScroll(a: HTMLElement, b: HTMLElement): void {
    a.addEventListener("scroll", () => {
      if (syncing) {
        return;
      }
      syncing = true;
      b.scrollTop = a.scrollTop;
      b.scrollLeft = a.scrollLeft;
      syncing = false;
    });
  }
  linkScroll(before.scroll, after.scroll);
  linkScroll(after.scroll, before.scroll);

  diffToggle.addEventListener("change", () => {
    overlayCanvas.hidden = !diffToggle.checked;
  });

  let activeUrls: string[] = [];
  function revokeActiveUrls(): void {
    for (const url of activeUrls) {
      URL.revokeObjectURL(url);
    }
    activeUrls = [];
  }

  async function show(pageIndex: number): Promise<void> {
    status.textContent = "Comparing…";
    diffToggle.checked = false;
    diffToggle.disabled = true;
    overlayCanvas.hidden = true;

    const [beforeBlob, afterBlob] = await Promise.all([
      options.api.renderPage(options.documentId, pageIndex, { original: true }),
      options.api.renderPage(options.documentId, pageIndex),
    ]);

    revokeActiveUrls();
    const beforeUrl = URL.createObjectURL(beforeBlob);
    const afterUrl = URL.createObjectURL(afterBlob);
    activeUrls = [beforeUrl, afterUrl];

    await Promise.all([loadImage(before.img, beforeUrl), loadImage(after.img, afterUrl)]);

    const diff = diffImages(before.img, after.img);
    if (diff === null) {
      status.textContent = "Comparison unavailable (page size changed)";
      return;
    }

    status.textContent = diff.summary.matches
      ? "Pages are identical"
      : `Pages differ (${(diff.summary.changedFraction * 100).toFixed(1)}% of pixels changed)`;
    overlayCanvas.width = diff.overlay.width;
    overlayCanvas.height = diff.overlay.height;
    const overlayContext = overlayCanvas.getContext("2d");
    overlayContext?.clearRect(0, 0, overlayCanvas.width, overlayCanvas.height);
    overlayContext?.drawImage(diff.overlay, 0, 0);
    diffToggle.disabled = false;
  }

  return { element, show };
}
