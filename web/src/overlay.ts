/**
 * UI-02: the click-to-edit overlay (SPEC.md section 8.2).
 *
 * One absolutely-positioned box per text unit -- a block, a line or a word,
 * whichever the toolbar's selection mode says (EDT-16); the server computes
 * the units (GET .../text_units), this module never groups text itself. Each
 * box is sized and positioned from the unit's own PDF-space bbox converted
 * through pdf.js's own viewport transform rather than hand-rolled math --
 * that transform already accounts for the page's rotation and the PDF/CSS
 * y-axis flip pdf.js itself renders through. The boxes are only hit
 * targets: their text is invisible (style.css), since the rendered page
 * already shows the real glyphs.
 *
 * Clicking a box selects that unit: it is outlined, gets its drag handles
 * (move and resize for a block, move only for a line or word), and the
 * inspector's text editor (UI-03) takes the cursor, prefilled with the
 * unit's text and detected style. All typing happens there; clicking
 * elsewhere asks first when there's an unapplied draft to lose (owner's
 * decision, 2026-10-01 -- this replaces the 2026-09-29 rule that clicking
 * elsewhere always kept it), then deselects. Edits to the draft debounce
 * into PreviewTextOp calls for the live "Match" field; Apply (or Enter)
 * commits text and style together as one edit_text_unit Op (EDT-17)
 * carrying `expect_text` and only the fields that changed -- tried with the
 * exact font first, asking before anything weaker (approval.ts). The caller
 * reloads everything after a commit (`onCommitted`); the selection clears
 * rather than following the edited unit, so an "Applied" note takes the
 * inspector's empty state instead.
 */

import type * as pdfjsLib from "pdfjs-dist";

import type { Api, HistoryOp, LinkInfo, SelectMode, SpanTrace, TextUnit } from "./api";
import { applyWithApproval, confirmVerified } from "./approval";
import { attachBlockHandles } from "./blockdrag";
import type { ArrangeHandle } from "./arrange";
import type { InspectorHandle, StyleField, TextDraft } from "./inspector";
import { mixedStyleFields, toHexColor } from "./inspector";
import { styleFromFontName } from "./styledialog";

export interface OverlayOptions {
  /** EDT-13..15: the shared selection (Shift+click, group drag, snapping). */
  arrange?: ArrangeHandle;
  api: Api;
  documentId: string;
  inspector: InspectorHandle;
  /** The document changed server-side (an edit committed) -- reload
   * everything: document bytes, this page's units and spans, the render, the
   * thumbnail. Indices are not assumed stable across this. */
  onCommitted: () => void;
}

export interface OverlayHandle {
  /** Rebuilds every unit's hit box for a freshly (re)rendered page. `units`
   * are in the current selection mode; `spans` supply their styles. The
   * selection (and its unapplied draft) survives when the same unit is
   * still there unchanged -- a zoom, say; otherwise it is cleared. */
  update(
    pageIndex: number,
    units: TextUnit[],
    spans: SpanTrace[],
    viewport: pdfjsLib.PageViewport,
    links?: LinkInfo[],
  ): void;
  /** EDT-16: the selection mode changed: the selection is dropped, and Word
   * mode turns the (span-level) format painter off. */
  setMode(mode: SelectMode): void;
  /** The selected unit's index, or null. */
  selectedIndex(): number | null;
  /** Show the unit with this index as selected (the shared selection landed
   * on it after a move or a copy). The keyboard stays where it is. */
  selectIndex(index: number): void;
  /** Nothing selected without being highlighted: clear this unit's selection,
   * its handles and the inspector. */
  deselect(): void;
  /** True at once when there's nothing to lose. With an unapplied draft, asks
   * "Discard your unapplied change to…?" first and returns whether to proceed --
   * on "no", the editor is refocused and false comes back. */
  confirmDiscard(): boolean;
  /** EDT-07: arm the format painter with the selected unit's first span as
   * its source; the next text clicked (on any page) gets its style.
   * A no-op when nothing is selected, and in Word mode. */
  armPainter(): void;
  /** EDT-07: drop an armed painter -- the document changed underneath it. */
  cancelPainter(): void;
  /** EDT-10: prompt for a target and link the selected unit to it. */
  addLink(): void;
  /** EDT-10: prompt for a new target for `link`. */
  editLink(link: LinkInfo): void;
  removeLink(link: LinkInfo): void;
  /** UI-03: commit the inspector editor's draft to the selected unit. */
  apply(draft: TextDraft): void;
  /** UI-03: the inspector editor's draft changed -- refresh the Match preview. */
  draftChanged(draft: TextDraft): void;
}

/** The selected unit, as it was when selected. */
interface UnitRef {
  pageIndex: number;
  unit: SelectMode;
  index: number;
  text: string;
  origin: [number, number];
  bbox: [number, number, number, number];
  /** The unit's first span: where its style, and the Match preview, come from. */
  firstSpan: number;
  style: SpanTrace["style"];
  /** Style fields that differ across the unit's spans. */
  mixed: StyleField[];
}

/** EDT-07: the format painter works on one style run (span). */
interface SpanRef {
  pageIndex: number;
  spanIndex: number;
  text: string;
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
const UNIT_NOUNS: Record<SelectMode, string> = { block: "paragraph", line: "line", word: "word" };

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
  /** The box on the page for the selected unit. */
  let selectedBox: HTMLElement | null = null;
  /** The unit being edited (in the inspector) right now. */
  let selected: UnitRef | null = null;
  let debounceTimer: ReturnType<typeof setTimeout> | null = null;
  let latestRequestId = 0;
  let committing = false;
  /** A text/style edit just committed: the next update() clears the selection
   * instead of trying to keep it, and shows an "Applied" note in the empty panel. */
  let justApplied = false;
  /** EDT-07: armed format painter source. Survives page changes and
   * update() (cross-page painting is supported); cleared once applied or
   * cancelled, and on any document change, which makes its index stale. */
  let painterSource: SpanRef | null = null;
  let mode: SelectMode = "line";
  let pageLinks: LinkInfo[] = [];
  let pageUnits: TextUnit[] = [];
  let pageSpans: SpanTrace[] = [];
  let boxes: HTMLElement[] = [];
  let currentPageIndex = 0;
  let currentViewport: pdfjsLib.PageViewport | null = null;
  let detachHandles: (() => void) | null = null;

  function removeHandles(): void {
    detachHandles?.();
    detachHandles = null;
  }

  function spansOf(unit: TextUnit): SpanTrace[] {
    return unit.span_indices.map((i) => pageSpans[i]).filter((span): span is SpanTrace => span !== undefined);
  }

  /** EDT-05: the handle drag is in layer pixels; the Op wants page points. */
  async function moveBlock(unit: UnitRef, fields: { dx?: number; dy?: number; width?: number }): Promise<void> {
    try {
      const applied = await applyWithApproval(options.api, options.documentId, {
        op: "move_text_block",
        page_index: unit.pageIndex,
        span_index: unit.firstSpan,
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

  /** EDT-18: a line or a word moves as a single-item move_objects, guarded by expect_text. */
  async function moveUnit(unit: UnitRef, dx: number, dy: number): Promise<void> {
    if (options.arrange) {
      options.arrange.move("text", unit.index, dx, dy); // also selects it again where it lands
      return;
    }
    try {
      const applied = await applyWithApproval(options.api, options.documentId, {
        op: "move_objects",
        items: [{ kind: "text", page_index: unit.pageIndex, index: unit.index, unit: unit.unit, expect_text: unit.text }],
        dx,
        dy,
      });
      if (!applied) {
        return;
      }
      await confirmVerified(options.api, options.documentId, applied.result);
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "The text could not be moved.");
      return;
    }
    options.onCommitted();
  }

  function attachHandlesFor(box: HTMLElement, unit: UnitRef): void {
    removeHandles();
    const isBlock = unit.unit === "block";
    detachHandles = attachBlockHandles(layer, box, {
      snapper: options.arrange?.snapper("text", unit.index),
      resizable: isBlock,
      noun: UNIT_NOUNS[unit.unit],
      onMove: (dxPx, dyPx) => {
        const viewport = currentViewport;
        if (!viewport) {
          return;
        }
        const origin = { x: parseFloat(box.style.left), y: parseFloat(box.style.top) };
        const [x0, y0] = viewportToMupdfPoint(viewport, origin.x, origin.y);
        const [x1, y1] = viewportToMupdfPoint(viewport, origin.x + dxPx, origin.y + dyPx);
        if (isBlock) {
          void moveBlock(unit, { dx: x1 - x0, dy: y1 - y0 });
        } else {
          void moveUnit(unit, x1 - x0, y1 - y0);
        }
      },
      onResize: (rightEdgePx) => {
        const viewport = currentViewport;
        if (!viewport) {
          return;
        }
        const [rightX] = viewportToMupdfPoint(viewport, rightEdgePx, parseFloat(box.style.top));
        void moveBlock(unit, { width: rightX - unit.bbox[0] });
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
    const unit = selected;
    if (!unit) {
      return;
    }
    const target = parseLinkTarget(window.prompt("Link to a URL (https://…, mailto:…) or a page number:") ?? "");
    if (target) {
      void applyLinkOp({ op: "add_link", page_index: unit.pageIndex, rect: unit.bbox, ...target });
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
    if (!selected || mode === "word") {
      return;
    }
    const source: SpanRef = { pageIndex: selected.pageIndex, spanIndex: selected.firstSpan, text: selected.style.text };
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

  /** The style run of `unit` under a click: the painter's target. */
  function spanUnder(unit: TextUnit, event: MouseEvent): number {
    const viewport = currentViewport;
    if (viewport && unit.span_indices.length > 1) {
      const layerRect = layer.getBoundingClientRect();
      const [x, y] = viewportToMupdfPoint(viewport, event.clientX - layerRect.left, event.clientY - layerRect.top);
      const hit = unit.span_indices.find((i) => {
        const bbox = pageSpans[i]?.style.bbox;
        return bbox !== undefined && x >= bbox[0] && x <= bbox[2] && y >= bbox[1] && y <= bbox[3];
      });
      if (hit !== undefined) {
        return hit;
      }
    }
    return unit.span_indices[0];
  }

  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && painterSource) {
      event.preventDefault();
      disarmPainter();
    }
  });

  /** Clears only this tool's own highlight and handles -- never the inspector, since a
   * caller may invoke this right after another tool (image, shape) has already put its
   * own panel up, which must survive. Whoever wants the resting empty state calls
   * `options.inspector.showEmpty()` itself (update()'s own "nothing kept" path does,
   * and so does every caller below that isn't immediately replacing this with a
   * different panel of its own, such as the format painter's). */
  function clearSelection(): void {
    if (debounceTimer !== null) {
      clearTimeout(debounceTimer);
      debounceTimer = null;
    }
    selectedBox?.classList.remove("pw-span-selected");
    selectedBox = null;
    selected = null;
    removeHandles();
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

  /** The Match row for a draft: the engine's own preview (in the unit's first font) while
   * the original font is kept; a font the user chose is exactly that font, by definition. */
  function previewDraft(draft: TextDraft): void {
    const unit = selected;
    if (!unit) {
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
    queuePreview(unit.pageIndex, unit.firstSpan, draft.text);
  }

  /** The edit_text_unit Op for a draft: only what actually changed is sent. A field
   * that is mixed across the unit is sent only once the user has touched it. */
  function editOpFor(unit: UnitRef, draft: TextDraft): HistoryOp {
    const op: HistoryOp = {
      op: "edit_text_unit",
      page_index: unit.pageIndex,
      unit: unit.unit,
      index: unit.index,
      expect_text: unit.text,
    };
    const changed = (field: StyleField, differs: boolean): boolean =>
      !draft.mixed.includes(field) && (differs || unit.mixed.includes(field));
    if (draft.text !== unit.text) op.new_text = draft.text;
    if (draft.font) op.font = draft.font;
    if (changed("size", Math.abs(draft.size - unit.style.size) > 0.05)) op.size = draft.size;
    if (changed("color", toHexColor(draft.color) !== toHexColor(unit.style.color))) op.color = draft.color;
    const current = styleFromFontName(unit.style.font);
    if (changed("bold", draft.bold !== current.bold)) op.bold = draft.bold;
    if (changed("italic", draft.italic !== current.italic)) op.italic = draft.italic;
    return op;
  }

  async function commit(draft: TextDraft): Promise<void> {
    const unit = selected;
    if (!unit || committing) {
      return;
    }
    if (!draft.text.trim()) {
      options.inspector.setStatus("The text can't be empty. To remove it, press Esc, then Delete.");
      return;
    }
    committing = true;
    options.inspector.setStatus("Applying…", true);
    let applied: { result: unknown } | null = null;
    try {
      applied = await applyWithApproval(options.api, options.documentId, editOpFor(unit, draft));
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
    justApplied = true;
    options.arrange?.clear();
    options.onCommitted();
  }

  function select(position: number, keepDraft: boolean): void {
    const unit = pageUnits[position];
    const box = boxes[position];
    const spans = spansOf(unit);
    if (!box || spans.length === 0) {
      return;
    }
    selectedBox?.classList.remove("pw-span-selected");
    selectedBox = box;
    box.classList.add("pw-span-selected");
    selected = {
      pageIndex: currentPageIndex,
      unit: unit.granularity,
      index: unit.index,
      text: unit.text,
      origin: unit.origin,
      bbox: unit.bbox,
      firstSpan: unit.span_indices[0],
      style: spans[0].style,
      mixed: mixedStyleFields(spans),
    };
    attachHandlesFor(box, selected);
    options.inspector.showUnit(
      unit,
      spans,
      pageLinks.filter((link) => overlaps(link.rect, unit.bbox)),
      keepDraft,
    );
    previewDraft(options.inspector.draft());
  }

  function confirmDiscard(): boolean {
    if (!selected || !options.inspector.isDirty()) {
      return true;
    }
    if (!window.confirm(`Discard your unapplied change to “${selected.text}”?`)) {
      options.inspector.focusEditor();
      return false;
    }
    return true;
  }

  function onUnitClick(position: number, event: MouseEvent): void {
    const unit = pageUnits[position];
    if (painterSource) {
      const spanIndex = spanUnder(unit, event);
      void applyPainter({ pageIndex: currentPageIndex, spanIndex, text: pageSpans[spanIndex]?.style.text ?? unit.text });
      return;
    }
    if (committing) {
      return;
    }
    const same =
      selected?.pageIndex === currentPageIndex && selected.unit === unit.granularity && selected.index === unit.index;
    if (!same) {
      if (!confirmDiscard()) {
        return;
      }
      select(position, false);
    }
    options.inspector.focusEditor();
  }

  function update(
    pageIndex: number,
    units: TextUnit[],
    spans: SpanTrace[],
    viewport: pdfjsLib.PageViewport,
    links: LinkInfo[] = [],
  ): void {
    // A zoom or re-render of the same page keeps the selection and its draft when the
    // unit is still there, unchanged. A just-applied edit clears it instead, even when
    // the same text is still there (a style-only change): the edit is done, and an
    // "Applied" note takes the empty panel's place. Anything else (another page, the
    // text changed underneath) also clears it.
    const previous = selected;
    const applied = justApplied;
    justApplied = false;
    removeHandles();
    selectedBox = null;
    selected = null;
    layer.innerHTML = "";
    currentPageIndex = pageIndex;
    currentViewport = viewport;
    pageLinks = links;
    pageUnits = units;
    pageSpans = spans;

    // EDT-10: link areas, drawn under the text boxes and never clickable
    // themselves -- editing a link goes through the text it covers.
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

    boxes = [];
    for (const [position, unit] of units.entries()) {
      const rect = bboxToRect(viewport, unit.bbox);
      const style = spans[unit.span_indices[0]]?.style;
      const box = document.createElement("div");
      // .pw-span-box on every box, whatever the unit: the one class for "text hit target".
      box.className = `pw-span-box pw-unit-${unit.granularity}`;
      box.style.left = `${rect.left}px`;
      box.style.top = `${rect.top}px`;
      box.style.width = `${rect.width}px`;
      box.style.height = `${rect.height}px`;
      if (style) {
        box.style.fontSize = `${style.size * viewport.scale}px`;
        // Invisible (style.css), but sized and weighted like the real text, so the
        // box's own text matches what it covers (and tools can read its style).
        box.style.fontFamily = approximateFontFamily(style.font);
        box.style.fontWeight = approximateFontWeight(style.font);
        box.style.fontStyle = approximateFontStyle(style.font);
      }
      box.textContent = unit.text;
      box.title = `Click to edit this ${UNIT_NOUNS[unit.granularity]} in the inspector`;
      box.dataset.unitIndex = String(unit.index);
      box.dataset.spanIndex = String(unit.span_indices[0]);
      // Shift+click, or dragging one object of a multi-object selection, belongs to
      // the shared selection (arrange.ts); the click that follows is then ignored.
      let consumed = false;
      box.addEventListener("mousedown", (event) => {
        consumed = !painterSource && Boolean(options.arrange?.pointerDown("text", unit.index, event));
      });
      box.addEventListener("click", (event) => {
        if (!consumed) {
          const painting = painterSource !== null;
          onUnitClick(position, event);
          if (!painting) {
            options.arrange?.selectOnly("text", unit.index);
          }
        }
        consumed = false;
      });
      layer.appendChild(box);
      boxes.push(box);
    }

    const kept =
      !applied && previous && previous.pageIndex === pageIndex
        ? units.findIndex((u) => u.granularity === previous.unit && u.index === previous.index && u.text === previous.text)
        : -1;
    if (kept >= 0) {
      select(kept, true);
    } else {
      options.inspector.showEmpty(applied ? "Applied. Undo with Ctrl+Z." : undefined);
    }
  }

  function setMode(next: SelectMode): void {
    mode = next;
    clearSelection();
    options.inspector.setPainterEnabled(next !== "word");
    if (next === "word" && painterSource) {
      disarmPainter();
    }
  }

  function selectIndex(index: number): void {
    const position = pageUnits.findIndex((u) => u.index === index);
    if (position >= 0 && !committing) {
      select(position, false);
    }
  }

  return {
    update,
    setMode,
    selectedIndex: () => selected?.index ?? null,
    selectIndex,
    deselect: clearSelection,
    confirmDiscard,
    armPainter,
    cancelPainter: disarmPainter,
    addLink,
    editLink,
    removeLink,
    apply: (draft) => void commit(draft),
    draftChanged: previewDraft,
  };
}
