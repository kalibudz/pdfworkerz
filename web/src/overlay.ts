/**
 * UI-02: the click-to-edit overlay (SPEC.md section 8.2).
 *
 * One absolutely-positioned box per span, sized and positioned from the
 * span's own PDF-space bbox converted through pdf.js's own viewport
 * transform (`convertToViewportRectangle`) rather than hand-rolled math --
 * that transform already accounts for the page's rotation and the PDF/CSS
 * y-axis flip pdf.js itself renders through. The boxes are only hit
 * targets: their text is invisible (style.css), since the rendered page
 * already shows the real glyphs.
 *
 * Clicking a box selects that span: it is outlined, gets the paragraph
 * move/resize handles, and the inspector's text editor (UI-03) takes the
 * cursor, prefilled with the span's text and detected style. All typing
 * happens there; clicking elsewhere never discards it. Edits to the draft
 * debounce into PreviewTextOp calls for the live "Match" field; Apply (or
 * Enter) commits text and style together as one edit_span Op -- tried with
 * the exact font first, asking before anything weaker (approval.ts). The
 * caller reloads everything after a commit (`onCommitted`), and the edited
 * span is selected again so edits can follow one another.
 */

import type * as pdfjsLib from "pdfjs-dist";

import type { Api, HistoryOp, LinkInfo, SpanTrace } from "./api";
import { applyWithApproval, confirmVerified } from "./approval";
import { attachBlockHandles } from "./blockdrag";
import type { ArrangeHandle } from "./arrange";
import type { InspectorHandle, TextDraft } from "./inspector";
import { toHexColor } from "./inspector";
import { styleFromFontName } from "./styledialog";

export interface OverlayOptions {
  /** EDT-13..15: the shared selection (Shift+click, group drag, snapping). */
  arrange?: ArrangeHandle;
  api: Api;
  documentId: string;
  inspector: InspectorHandle;
  /** The document changed server-side (an edit committed) -- reload
   * everything: document bytes, this page's spans, the render, the
   * thumbnail. Span indices are not assumed stable across this. */
  onCommitted: () => void;
}

export interface OverlayHandle {
  /** Rebuilds every span's hit box for a freshly (re)rendered page. The
   * selection (and its unapplied draft) survives when the same span is
   * still there unchanged -- a zoom, say; otherwise it is cleared. */
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
  /** UI-03: commit the inspector editor's draft to the selected span. */
  apply(draft: TextDraft): void;
  /** UI-03: the inspector editor's draft changed -- refresh the Match preview. */
  draftChanged(draft: TextDraft): void;
}

interface SpanRef {
  pageIndex: number;
  spanIndex: number;
  text: string;
  bbox: [number, number, number, number];
  style: SpanTrace["style"];
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

export function createOverlay(layer: HTMLElement, options: OverlayOptions): OverlayHandle {
  /** The box on the page for the selected span. */
  let selectedBox: HTMLElement | null = null;
  /** The span being edited (in the inspector) right now -- also what "Copy style" copies from. */
  let selected: SpanRef | null = null;
  let debounceTimer: ReturnType<typeof setTimeout> | null = null;
  let latestRequestId = 0;
  let committing = false;
  /** After a commit reloads the page: select the edited span again, found by its new text. */
  let reselectAfterCommit: { pageIndex: number; spanIndex: number; text: string } | null = null;
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
      snapper: options.arrange?.snapper("text", span.spanIndex),
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
    const span = selected;
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
    clearSelection();
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

  function clearSelection(): void {
    if (debounceTimer !== null) {
      clearTimeout(debounceTimer);
      debounceTimer = null;
    }
    selectedBox?.classList.remove("pw-span-selected");
    selectedBox = null;
    selected = null;
    removeHandles();
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

  /** The Match row for a draft: the engine's own preview while the original font is
   * kept; a font the user chose is exactly that font, by definition. */
  function previewDraft(draft: TextDraft): void {
    const span = selected;
    if (!span) {
      return;
    }
    if (draft.font) {
      latestRequestId++; // any preview still in flight is for the old choice
      options.inspector.setPreview({
        tier: "exact",
        confidence: 1,
        requires_approval: false,
        note: `${draft.font}, chosen by you`,
      });
      return;
    }
    queuePreview(span.pageIndex, span.spanIndex, draft.text);
  }

  /** The edit_span Op for a draft: only what actually changed is sent. */
  function editOpFor(span: SpanRef, draft: TextDraft): HistoryOp {
    const op: HistoryOp = { op: "edit_span", page_index: span.pageIndex, span_index: span.spanIndex };
    if (draft.text !== span.text) op.new_text = draft.text;
    if (draft.font) op.font = draft.font;
    if (Math.abs(draft.size - span.style.size) > 0.05) op.size = draft.size;
    if (toHexColor(draft.color) !== toHexColor(span.style.color)) op.color = draft.color;
    const current = styleFromFontName(span.style.font);
    if (draft.bold !== current.bold) op.bold = draft.bold;
    if (draft.italic !== current.italic) op.italic = draft.italic;
    return op;
  }

  async function commit(draft: TextDraft): Promise<void> {
    const span = selected;
    if (!span || committing) {
      return;
    }
    if (!draft.text.trim()) {
      options.inspector.setStatus("The text can't be empty. To remove it, use: delete \"…\" in the command bar.");
      return;
    }
    committing = true;
    options.inspector.setStatus("Applying…", true);
    let applied: { result: unknown } | null = null;
    try {
      applied = await applyWithApproval(options.api, options.documentId, editOpFor(span, draft));
      if (applied) {
        await confirmVerified(options.api, options.documentId, applied.result);
      }
    } catch (error) {
      committing = false;
      options.inspector.setStatus(error instanceof Error ? error.message : "The edit could not be saved.");
      options.inspector.focusEditor();
      return;
    }
    committing = false;
    if (!applied) {
      options.inspector.setStatus(null); // the user declined the weaker font: the draft stays
      options.inspector.focusEditor();
      return;
    }
    reselectAfterCommit = { pageIndex: span.pageIndex, spanIndex: span.spanIndex, text: draft.text };
    options.onCommitted();
  }

  function select(box: HTMLElement, pageIndex: number, spanIndex: number, span: SpanTrace, keepDraft: boolean): void {
    selectedBox?.classList.remove("pw-span-selected");
    selectedBox = box;
    box.classList.add("pw-span-selected");
    selected = { pageIndex, spanIndex, text: span.style.text, bbox: span.style.bbox, style: span.style };
    attachHandlesFor(box, selected);
    options.inspector.showSpan(
      span,
      pageLinks.filter((link) => overlaps(link.rect, span.style.bbox)),
      keepDraft,
    );
    previewDraft(options.inspector.draft());
  }

  function onSpanClick(box: HTMLElement, pageIndex: number, spanIndex: number, span: SpanTrace): void {
    if (painterSource) {
      void applyPainter({ pageIndex, spanIndex, text: span.style.text, bbox: span.style.bbox, style: span.style });
      return;
    }
    if (committing) {
      return;
    }
    const same = selected?.pageIndex === pageIndex && selected.spanIndex === spanIndex;
    if (!same && options.inspector.isDirty() && selected) {
      if (!window.confirm(`Discard your unapplied change to “${selected.text}”?`)) {
        options.inspector.focusEditor();
        return;
      }
    }
    if (!same) {
      select(box, pageIndex, spanIndex, span, false);
    }
    options.inspector.focusEditor();
  }

  function update(
    pageIndex: number,
    spans: SpanTrace[],
    viewport: pdfjsLib.PageViewport,
    links: LinkInfo[] = [],
  ): void {
    // A zoom or re-render of the same page keeps the selection and its draft when the
    // span is still there, unchanged; after a commit the edited span is found by its
    // new text. Anything else (another page, the text changed underneath) clears it.
    const previous = selected;
    const reselect = reselectAfterCommit;
    reselectAfterCommit = null;
    removeHandles();
    selectedBox = null;
    selected = null;
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

    const boxes: HTMLElement[] = [];
    for (const [spanIndex, span] of spans.entries()) {
      const rect = bboxToRect(viewport, span.style.bbox);
      const box = document.createElement("div");
      box.className = "pw-span-box";
      box.style.left = `${rect.left}px`;
      box.style.top = `${rect.top}px`;
      box.style.width = `${rect.width}px`;
      box.style.height = `${rect.height}px`;
      box.style.fontSize = `${span.style.size * viewport.scale}px`;
      // Invisible (style.css), but sized and weighted like the real text, so the
      // box's own text matches what it covers (and tools can read its style).
      box.style.fontFamily = approximateFontFamily(span.style.font);
      box.style.fontWeight = approximateFontWeight(span.style.font);
      box.style.fontStyle = approximateFontStyle(span.style.font);
      box.textContent = span.style.text;
      box.title = "Click to edit this text in the inspector";
      box.dataset.spanIndex = String(spanIndex);
      // Shift+click, or dragging one object of a multi-object selection, belongs to
      // the shared selection (arrange.ts); the click that follows is then ignored.
      let consumed = false;
      box.addEventListener("mousedown", (event) => {
        consumed = !painterSource && Boolean(options.arrange?.pointerDown("text", spanIndex, event));
      });
      box.addEventListener("click", () => {
        if (!consumed) {
          onSpanClick(box, pageIndex, spanIndex, span);
          options.arrange?.selectOnly("text", spanIndex);
        }
        consumed = false;
      });
      layer.appendChild(box);
      boxes.push(box);
    }

    const keep =
      previous &&
      previous.pageIndex === pageIndex &&
      spans[previous.spanIndex]?.style.text === previous.text &&
      !reselect;
    if (keep) {
      select(boxes[previous.spanIndex], pageIndex, previous.spanIndex, spans[previous.spanIndex], true);
    } else if (reselect && reselect.pageIndex === pageIndex) {
      // The edited span usually keeps its index; if the redraw moved it, find it by text.
      const found =
        spans[reselect.spanIndex]?.style.text === reselect.text
          ? reselect.spanIndex
          : spans.findIndex((span) => span.style.text === reselect.text);
      if (found >= 0) {
        select(boxes[found], pageIndex, found, spans[found], false);
        options.inspector.setStatus("Applied. Undo with Ctrl+Z.");
      } else {
        options.inspector.showEmpty();
      }
    } else {
      options.inspector.showEmpty();
    }
  }

  return {
    update,
    armPainter,
    cancelPainter: disarmPainter,
    addLink,
    editLink,
    removeLink,
    apply: (draft) => void commit(draft),
    draftChanged: previewDraft,
  };
}
