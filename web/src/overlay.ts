/**
 * UI-02: the click-to-edit overlay (SPEC.md section 8.2).
 *
 * One absolutely-positioned box per span, sized and positioned from the
 * span's own PDF-space bbox converted through pdf.js's own viewport
 * transform (`convertToViewportRectangle`) rather than hand-rolled math --
 * that transform already accounts for the page's rotation and the PDF/CSS
 * y-axis flip pdf.js itself renders through. Clicking a box turns it
 * `contenteditable`, styled with the detected size and color exactly and
 * an *approximated* font family/weight/style from the font's name (the
 * exact embedded typeface isn't loaded as a web font here -- a real,
 * intentional simplification, not a silent one: see README.md).
 *
 * Typing debounces into PreviewTextOp calls that drive the inspector's
 * live "Match" field; Enter commits through ReplaceSpanTextOp (asking for
 * confirmation first when the preview says the match needs approval,
 * exactly what `requires_approval` already means -- SPEC.md section 5.3);
 * Escape discards. A committed edit's exact new state (including how the
 * commit rearranges span indices) is never guessed at here -- the caller
 * just reloads everything (`onCommitted`).
 */

import type * as pdfjsLib from "pdfjs-dist";

import type { Api, HistoryOp, LinkInfo, SpanTrace } from "./api";
import { applyWithApproval, confirmVerified } from "./approval";
import { attachBlockHandles } from "./blockdrag";
import type { InspectorHandle } from "./inspector";
import { toHexColor } from "./inspector";

export interface OverlayOptions {
  api: Api;
  documentId: string;
  inspector: InspectorHandle;
  /** The document changed server-side (an edit committed) -- reload
   * everything: document bytes, this page's spans, the render, the
   * thumbnail. Span indices are not assumed stable across this. */
  onCommitted: () => void;
}

export interface OverlayHandle {
  /** Rebuilds every span's hit box for a freshly (re)rendered page.
   * Discards any edit in progress -- its span index belongs to the page
   * state before this render, which by the time a caller has a new
   * viewport to hand over is already gone. */
  update(pageIndex: number, spans: SpanTrace[], viewport: pdfjsLib.PageViewport, links?: LinkInfo[]): void;
  /** EDT-07: arm the format painter with the span currently being edited
   * as its source; the next span clicked (on any page) gets its style.
   * A no-op when nothing is selected. */
  armPainter(): void;
  /** EDT-07: drop an armed painter -- the document changed underneath it. */
  cancelPainter(): void;
  /** EDT-10: prompt for a target and link the selected span to it. */
  addLink(): void;
  /** EDT-10: prompt for a new target for `link`. */
  editLink(link: LinkInfo): void;
  removeLink(link: LinkInfo): void;
}

interface SpanRef {
  pageIndex: number;
  spanIndex: number;
  text: string;
  bbox: [number, number, number, number];
}

type LinkTarget = { uri: string } | { target_page: number };

/** "https://…" / "mailto:…" -> a URI link; a bare number -> that (1-based)
 * page of this document. Anything else is passed through as a URI and
 * left to the engine's scheme allowlist to accept or refuse. */
export function parseLinkTarget(value: string): LinkTarget | null {
  const trimmed = value.trim();
  if (!trimmed) {
    return null;
  }
  const pageMatch = /^(?:page\s*)?(\d+)$/i.exec(trimmed);
  if (pageMatch) {
    return { target_page: Number(pageMatch[1]) - 1 };
  }
  return { uri: trimmed };
}

function overlaps(a: readonly number[], b: readonly number[]): boolean {
  return a[0] < b[2] && b[0] < a[2] && a[1] < b[3] && b[1] < a[3];
}

const PREVIEW_DEBOUNCE_MS = 300;

function approximateFontFamily(fontName: string): string {
  const lower = fontName.toLowerCase();
  if (/courier|consolas|mono|typewriter/.test(lower)) {
    return "monospace";
  }
  if (/times|georgia|garamond|cambria|palatino|minion|serif/.test(lower)) {
    return "serif";
  }
  return "sans-serif";
}

function approximateFontWeight(fontName: string): string {
  return /bold|black|heavy|semibold/i.test(fontName) ? "bold" : "normal";
}

function approximateFontStyle(fontName: string): string {
  return /italic|oblique/i.test(fontName) ? "italic" : "normal";
}

/** MuPDF (texttrace, get_links, ...) reports page coordinates y-down,
 * unrotated, relative to the crop box's top-left corner; pdf.js's viewport
 * converts from y-up PDF user space. `viewBox` is that crop box in PDF
 * space, so this is the one translation between the two -- rotation and
 * scale are then left to pdf.js. Confirmed against pymupdf 1.28.2 for
 * rotated and cropped pages. (Feeding MuPDF's y-down values straight in,
 * as this once did, mirrored every box vertically: top-of-page text got a
 * click target near the bottom.) */
export function mupdfToPdfPoint(viewport: pdfjsLib.PageViewport, x: number, y: number): [number, number] {
  const viewBox = viewport.viewBox as number[];
  return [viewBox[0] + x, viewBox[3] - y];
}

/** The inverse of mupdfToPdfPoint composed with pdf.js's own conversion:
 * a CSS-pixel point on the page back to MuPDF page coordinates. */
export function viewportToMupdfPoint(viewport: pdfjsLib.PageViewport, x: number, y: number): [number, number] {
  const [pdfX, pdfY] = viewport.convertToPdfPoint(x, y) as [number, number];
  const viewBox = viewport.viewBox as number[];
  return [pdfX - viewBox[0], viewBox[3] - pdfY];
}

export function bboxToRect(
  viewport: pdfjsLib.PageViewport,
  bbox: readonly [number, number, number, number],
): { left: number; top: number; width: number; height: number } {
  // No convertToViewportRectangle on this PageViewport (confirmed against
  // the installed pdfjs-dist's own .d.ts before relying on it -- only
  // point conversion exists), so both corners are converted by hand, and
  // min/abs below because rotation can swap which corner ends up where.
  const [x0, y0] = viewport.convertToViewportPoint(...mupdfToPdfPoint(viewport, bbox[0], bbox[1]));
  const [x1, y1] = viewport.convertToViewportPoint(...mupdfToPdfPoint(viewport, bbox[2], bbox[3]));
  const left = Math.min(x0, x1);
  const top = Math.min(y0, y1);
  return { left, top, width: Math.abs(x1 - x0), height: Math.abs(y1 - y0) };
}

function selectAllContents(el: HTMLElement): void {
  const range = document.createRange();
  range.selectNodeContents(el);
  const selection = window.getSelection();
  selection?.removeAllRanges();
  selection?.addRange(range);
}

export function createOverlay(layer: HTMLElement, options: OverlayOptions): OverlayHandle {
  let activeBox: HTMLElement | null = null;
  let originalText = "";
  let debounceTimer: ReturnType<typeof setTimeout> | null = null;
  let latestRequestId = 0;
  // Guards against a real, confirmed browser behavior: toggling
  // contentEditable off on a focused element can itself fire `blur` --
  // without this, that blur's handler would call cancelEdit() and revert
  // the box's text back to originalText while commitEdit's own save is
  // still in flight, discarding the edit before its result is even back.
  let committing = false;
  /** The span being edited right now -- what "Copy style" copies from. */
  let selected: SpanRef | null = null;
  /** EDT-07: armed format painter source. Survives page changes and
   * update() (cross-page painting is supported); cleared once applied or
   * cancelled, and on any document change, which makes its index stale. */
  let painterSource: SpanRef | null = null;
  let pageLinks: LinkInfo[] = [];
  let currentPageIndex = 0;
  let currentViewport: pdfjsLib.PageViewport | null = null;
  let detachHandles: (() => void) | null = null;

  function removeHandles(): void {
    detachHandles?.();
    detachHandles = null;
  }

  /** EDT-05: the handle drag is in layer pixels; the Op wants page points. */
  async function moveBlock(span: SpanRef, fields: { dx?: number; dy?: number; width?: number }): Promise<void> {
    try {
      const applied = await applyWithApproval(options.api, options.documentId, {
        op: "move_text_block",
        page_index: span.pageIndex,
        span_index: span.spanIndex,
        ...fields,
      });
      if (!applied) {
        return;
      }
      await confirmVerified(options.api, options.documentId, applied.result);
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "The paragraph could not be moved.");
      return;
    }
    options.onCommitted();
  }

  function attachHandlesFor(box: HTMLElement, span: SpanRef): void {
    removeHandles();
    detachHandles = attachBlockHandles(layer, box, {
      onMove: (dxPx, dyPx) => {
        const viewport = currentViewport;
        if (!viewport) {
          return;
        }
        const origin = { x: parseFloat(box.style.left), y: parseFloat(box.style.top) };
        const [x0, y0] = viewportToMupdfPoint(viewport, origin.x, origin.y);
        const [x1, y1] = viewportToMupdfPoint(viewport, origin.x + dxPx, origin.y + dyPx);
        void moveBlock(span, { dx: x1 - x0, dy: y1 - y0 });
      },
      onResize: (rightEdgePx) => {
        const viewport = currentViewport;
        if (!viewport) {
          return;
        }
        const [rightX] = viewportToMupdfPoint(viewport, rightEdgePx, parseFloat(box.style.top));
        void moveBlock(span, { width: rightX - span.bbox[0] });
      },
    });
  }

  async function applyLinkOp(op: HistoryOp): Promise<void> {
    try {
      await options.api.applyOp(options.documentId, op);
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "The link could not be saved.");
      return;
    }
    options.onCommitted();
  }

  function addLink(): void {
    const span = selected; // captured first: the prompt blurs the box, which clears `selected`
    if (!span) {
      return;
    }
    const target = parseLinkTarget(window.prompt("Link to a URL (https://…, mailto:…) or a page number:") ?? "");
    if (target) {
      void applyLinkOp({ op: "add_link", page_index: span.pageIndex, rect: span.bbox, ...target });
    }
  }

  function editLink(link: LinkInfo): void {
    const current = link.kind === "uri" ? (link.uri ?? "") : String((link.target_page ?? 0) + 1);
    const target = parseLinkTarget(window.prompt("New link target (URL or page number):", current) ?? "");
    if (target) {
      void applyLinkOp({ op: "update_link", page_index: currentPageIndex, index: link.index, ...target });
    }
  }

  function removeLink(link: LinkInfo): void {
    void applyLinkOp({ op: "remove_link", page_index: currentPageIndex, index: link.index });
  }

  function disarmPainter(): void {
    painterSource = null;
    layer.classList.remove("pw-painting");
    options.inspector.setPainter(null);
  }

  function armPainter(): void {
    if (!selected) {
      return;
    }
    const source = selected;
    cancelEdit();
    painterSource = source;
    layer.classList.add("pw-painting");
    options.inspector.setPainter(source.text);
  }

  async function applyPainter(target: SpanRef): Promise<void> {
    const source = painterSource;
    if (!source) {
      return;
    }
    if (source.pageIndex === target.pageIndex && source.spanIndex === target.spanIndex) {
      disarmPainter(); // clicking the source again just cancels
      return;
    }
    disarmPainter();
    try {
      const applied = await applyWithApproval(options.api, options.documentId, {
        op: "copy_style",
        page_index: source.pageIndex,
        span_index: source.spanIndex,
        target_page_index: target.pageIndex,
        target_span_index: target.spanIndex,
      });
      if (!applied) {
        return;
      }
      await confirmVerified(options.api, options.documentId, applied.result);
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "The style could not be applied.");
      return;
    }
    options.onCommitted();
  }

  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && painterSource) {
      event.preventDefault();
      disarmPainter();
    }
  });

  function cancelEdit(): void {
    if (committing) {
      return;
    }
    if (debounceTimer !== null) {
      clearTimeout(debounceTimer);
      debounceTimer = null;
    }
    // Confirmed the hard way: setting contentEditable to false on a
    // focused element can itself fire a synchronous `blur`, which re-enters
    // this same function through box.onblur below *before* this call
    // finishes -- that reentrant call sees (and nulls out) the shared
    // activeBox first, so reading it again afterwards would crash on a
    // null box. Taking a local copy and clearing activeBox immediately,
    // before touching the box at all, makes a reentrant call a harmless
    // no-op instead.
    const box = activeBox;
    activeBox = null;
    selected = null;
    removeHandles();
    if (box) {
      box.contentEditable = "false";
      box.textContent = originalText;
      box.classList.remove("pw-span-editing");
    }
    options.inspector.showEmpty();
  }

  function queuePreview(pageIndex: number, spanIndex: number, text: string): void {
    if (debounceTimer !== null) {
      clearTimeout(debounceTimer);
    }
    const requestId = ++latestRequestId;
    debounceTimer = setTimeout(() => {
      void options.api
        .previewText(options.documentId, pageIndex, spanIndex, text)
        .then((preview) => {
          if (requestId === latestRequestId) {
            options.inspector.setPreview(preview);
          }
        })
        .catch(() => {
          if (requestId === latestRequestId) {
            options.inspector.setPreview(null);
          }
        });
    }, PREVIEW_DEBOUNCE_MS);
  }

  async function commitEdit(box: HTMLElement, pageIndex: number, spanIndex: number): Promise<void> {
    const newText = box.textContent ?? "";
    if (newText === originalText) {
      cancelEdit();
      return;
    }

    const preview = await options.api.previewText(options.documentId, pageIndex, spanIndex, newText);
    if (preview.requires_approval) {
      const proceed = window.confirm(
        `This edit will use a ${preview.tier} font match rather than the original font exactly ` +
          `(${preview.note}). Continue?`,
      );
      if (!proceed) {
        return;
      }
    }

    committing = true;
    box.contentEditable = "false";
    box.classList.remove("pw-span-editing");
    try {
      const result = await options.api.replaceSpanText(options.documentId, pageIndex, spanIndex, newText, "fallback");
      await confirmVerified(options.api, options.documentId, result);
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "The edit could not be saved.");
      committing = false;
      box.contentEditable = "true";
      box.classList.add("pw-span-editing");
      box.focus();
      return;
    }
    committing = false;
    activeBox = null;
    selected = null;
    removeHandles();
    options.onCommitted();
  }

  function startEdit(box: HTMLElement, pageIndex: number, spanIndex: number, text: string, span: SpanTrace): void {
    if (activeBox && activeBox !== box) {
      cancelEdit();
    }
    activeBox = box;
    originalText = text;
    selected = { pageIndex, spanIndex, text, bbox: span.style.bbox };
    attachHandlesFor(box, selected);

    box.contentEditable = "true";
    box.classList.add("pw-span-editing");
    box.focus();
    selectAllContents(box);

    options.inspector.showSpan(
      span,
      pageLinks.filter((link) => overlaps(link.rect, span.style.bbox)),
    );
    queuePreview(pageIndex, spanIndex, text);

    box.oninput = () => queuePreview(pageIndex, spanIndex, box.textContent ?? "");
    box.onkeydown = (event: KeyboardEvent) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        void commitEdit(box, pageIndex, spanIndex);
      } else if (event.key === "Escape") {
        event.preventDefault();
        cancelEdit();
      }
    };
    box.onblur = () => {
      // A click landing outside every span box (blurring without a new one
      // taking over -- startEdit above already handles switching boxes)
      // discards the edit rather than leaving an orphaned editable box
      // with no visible way to commit or cancel it.
      if (activeBox === box) {
        cancelEdit();
      }
    };
  }

  function update(
    pageIndex: number,
    spans: SpanTrace[],
    viewport: pdfjsLib.PageViewport,
    links: LinkInfo[] = [],
  ): void {
    cancelEdit();
    removeHandles();
    layer.innerHTML = "";
    currentPageIndex = pageIndex;
    currentViewport = viewport;
    pageLinks = links;

    // EDT-10: link areas, drawn under the span boxes and never clickable
    // themselves -- editing a link goes through the span it covers.
    for (const link of links) {
      const rect = bboxToRect(viewport, link.rect);
      const outline = document.createElement("div");
      outline.className = "pw-link-box";
      outline.style.left = `${rect.left}px`;
      outline.style.top = `${rect.top}px`;
      outline.style.width = `${rect.width}px`;
      outline.style.height = `${rect.height}px`;
      outline.title = link.kind === "uri" ? (link.uri ?? "") : `page ${(link.target_page ?? 0) + 1}`;
      layer.appendChild(outline);
    }

    for (const [spanIndex, span] of spans.entries()) {
      const rect = bboxToRect(viewport, span.style.bbox);
      const box = document.createElement("div");
      box.className = "pw-span-box";
      box.style.left = `${rect.left}px`;
      box.style.top = `${rect.top}px`;
      box.style.width = `${rect.width}px`;
      box.style.height = `${rect.height}px`;
      box.style.fontSize = `${span.style.size * viewport.scale}px`;
      box.style.color = toHexColor(span.style.color);
      box.style.fontFamily = approximateFontFamily(span.style.font);
      box.style.fontWeight = approximateFontWeight(span.style.font);
      box.style.fontStyle = approximateFontStyle(span.style.font);
      box.textContent = span.style.text;
      box.addEventListener("click", () => {
        if (painterSource) {
          void applyPainter({ pageIndex, spanIndex, text: span.style.text, bbox: span.style.bbox });
        } else {
          startEdit(box, pageIndex, spanIndex, span.style.text, span);
        }
      });
      layer.appendChild(box);
    }
  }

  return { update, armPainter, cancelPainter: disarmPainter, addLink, editLink, removeLink };
}
