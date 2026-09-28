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

import type { Api, SpanTrace } from "./api";
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
  update(pageIndex: number, spans: SpanTrace[], viewport: pdfjsLib.PageViewport): void;
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

function bboxToRect(
  viewport: pdfjsLib.PageViewport,
  bbox: readonly [number, number, number, number],
): { left: number; top: number; width: number; height: number } {
  // No convertToViewportRectangle on this PageViewport (confirmed against
  // the installed pdfjs-dist's own .d.ts before relying on it -- only
  // point conversion exists), so both corners are converted by hand. The
  // PDF/CSS y-axis flip this accounts for is exactly why the two y's can't
  // just be assumed to already be in top-to-bottom order.
  const [x0, y0] = viewport.convertToViewportPoint(bbox[0], bbox[1]);
  const [x1, y1] = viewport.convertToViewportPoint(bbox[2], bbox[3]);
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
      await options.api.replaceSpanText(options.documentId, pageIndex, spanIndex, newText, "fallback");
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
    options.onCommitted();
  }

  function startEdit(box: HTMLElement, pageIndex: number, spanIndex: number, text: string, span: SpanTrace): void {
    if (activeBox && activeBox !== box) {
      cancelEdit();
    }
    activeBox = box;
    originalText = text;

    box.contentEditable = "true";
    box.classList.add("pw-span-editing");
    box.focus();
    selectAllContents(box);

    options.inspector.showSpan(span);
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

  function update(pageIndex: number, spans: SpanTrace[], viewport: pdfjsLib.PageViewport): void {
    cancelEdit();
    layer.innerHTML = "";

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
      box.addEventListener("click", () => startEdit(box, pageIndex, spanIndex, span.style.text, span));
      layer.appendChild(box);
    }
  }

  return { update };
}
