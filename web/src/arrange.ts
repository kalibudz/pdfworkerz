/**
 * EDT-13..EDT-15: one selection across text blocks, images and shapes, and what
 * can be done with it, the way Nitro does it:
 *
 * - Click an object to select it; Shift+click adds or removes; drag on empty page
 *   to select everything inside a rectangle. Esc clears.
 * - Drag any selected object to move the whole selection; smart guides snap it to
 *   other objects and to the page (snap.ts). Hold Alt to drag without snapping.
 * - The Align menu aligns (left/center/right/top/middle/bottom) and distributes;
 *   one object is aligned to the page.
 * - Arrow keys nudge 1pt (Shift: 10pt); presses in quick succession are one move.
 * - Ctrl+C / Ctrl+V copy and paste (onto the page shown), Ctrl+D duplicates,
 *   Delete removes.
 *
 * Text is selected as whole blocks (paragraphs): the unit a move redraws. Every
 * change is one move_objects / duplicate_objects / delete_objects Op -- one undo --
 * and afterwards the same objects, at their new places, are selected again.
 */

import type * as pdfjsLib from "pdfjs-dist";

import type { Api, BlockInfo, HistoryOp, ImageInfo, ShapeInfo } from "./api";
import { applyWithApproval, confirmVerified } from "./approval";
import { bboxToRect, viewportToMupdfPoint } from "./overlay";
import { alignOffsets, snap, type AlignAction, type Box } from "./snap";

export type ObjectKind = "text" | "image" | "shape";

interface PageObject {
  kind: ObjectKind;
  /** For text: the block's first span index; images and shapes: their own index. */
  index: number;
  spanIndices: number[];
  /** Page points, MuPDF space (y down). */
  pageRect: [number, number, number, number];
  /** Layer pixels. */
  box: Box;
}

export interface ArrangeOptions {
  api: Api;
  documentId: string;
  layer: HTMLElement;
  /** 0-based page shown in the viewer: where Ctrl+V pastes. */
  currentPage: () => number;
  onCommitted: () => void;
  /** The selection changed (count), for the toolbar's Align menu. */
  onSelectionChange: (count: number) => void;
}

export interface ArrangeHandle {
  /** Call after the text, image and shape tools have drawn their boxes. */
  update(
    pageIndex: number,
    viewport: pdfjsLib.PageViewport,
    blocks: BlockInfo[],
    images: ImageInfo[],
    shapes: ShapeInfo[],
  ): void;
  /** A tool's box got a mousedown. Handles Shift+click and dragging a multi-object
   * selection; returns true when it did, so the tool does nothing more. */
  pointerDown(kind: ObjectKind, index: number, event: MouseEvent): boolean;
  /** A plain click selected this object through its own tool. */
  selectOnly(kind: ObjectKind, index: number): void;
  /** For a single object's own drag: snap its (dx, dy) and draw guides. */
  snapper(kind: ObjectKind, index: number): DragSnapper;
  count(): number;
  clear(): void;
  align(action: AlignAction): void;
  /** Arrow keys, Delete, Ctrl+C/V/D, Esc. Returns true when it used the key. */
  handleKey(event: KeyboardEvent): boolean;
}

export interface DragSnapper {
  adjust(dx: number, dy: number, event: MouseEvent): [number, number];
  end(): void;
}

const NUDGE_IDLE_MS = 400;
const PASTE_OFFSET_PT = 10;
const PAGE_MARGIN_PT = 36;
const REFIND_TOLERANCE_PT = 2.5;
const MARQUEE_THRESHOLD_PX = 4;

function unionBox(boxes: Box[]): Box {
  return {
    left: Math.min(...boxes.map((b) => b.left)),
    top: Math.min(...boxes.map((b) => b.top)),
    right: Math.max(...boxes.map((b) => b.right)),
    bottom: Math.max(...boxes.map((b) => b.bottom)),
  };
}

export function createArrange(options: ArrangeOptions): ArrangeHandle {
  const { layer } = options;
  let pageIndex = 0;
  let viewport: pdfjsLib.PageViewport | null = null;
  let objects: PageObject[] = [];
  let selected: PageObject[] = [];
  let clipboard: { pageIndex: number; items: { kind: ObjectKind; index: number }[] } | null = null;
  /** After a change: the objects to select again, by kind and where they should now be. */
  let reselect: { kind: ObjectKind; pageRect: [number, number, number, number] }[] | null = null;
  let busy = false;
  let nudge = { dx: 0, dy: 0, timer: null as ReturnType<typeof setTimeout> | null };

  function key(object: { kind: ObjectKind; index: number }): string {
    return `${object.kind}:${object.index}`;
  }

  function find(kind: ObjectKind, index: number): PageObject | undefined {
    return objects.find((o) => o.kind === kind && (o.index === index || (kind === "text" && o.spanIndices.includes(index))));
  }

  function isSelected(object: PageObject): boolean {
    return selected.some((s) => key(s) === key(object));
  }

  /** Every element on the layer that shows `object` (a text block has one per line). */
  function elementsOf(object: PageObject): HTMLElement[] {
    if (object.kind === "text") {
      return object.spanIndices
        .map((i) => layer.querySelector<HTMLElement>(`.pw-span-box[data-span-index="${i}"]`))
        .filter((el): el is HTMLElement => el !== null);
    }
    const box = layer.querySelector<HTMLElement>(`.pw-${object.kind}-box[data-index="${object.index}"]`);
    return box ? [box] : [];
  }

  function drawOutlines(): void {
    layer.querySelectorAll(".pw-arrange-outline").forEach((el) => el.remove());
    if (selected.length < 2 && !selected.some((s) => s.kind === "text")) {
      options.onSelectionChange(selected.length);
      return; // an image or shape alone shows its own tool's selection
    }
    for (const object of selected) {
      const outline = document.createElement("div");
      outline.className = "pw-arrange-outline";
      outline.dataset.key = key(object);
      outline.style.left = `${object.box.left}px`;
      outline.style.top = `${object.box.top}px`;
      outline.style.width = `${object.box.right - object.box.left}px`;
      outline.style.height = `${object.box.bottom - object.box.top}px`;
      layer.appendChild(outline);
    }
    options.onSelectionChange(selected.length);
  }

  function setSelection(next: PageObject[]): void {
    selected = next;
    drawOutlines();
  }

  /** Show the selection shifted by (dx, dy) layer pixels, before anything is sent. */
  function previewShift(dx: number, dy: number): void {
    const shift = dx || dy ? `translate(${dx}px, ${dy}px)` : "";
    for (const object of selected) {
      for (const element of elementsOf(object)) {
        element.style.transform = shift;
      }
    }
    layer.querySelectorAll<HTMLElement>(".pw-arrange-outline").forEach((el) => (el.style.transform = shift));
  }

  function drawGuides(guidesX: number[], guidesY: number[]): void {
    clearGuides();
    for (const x of guidesX) {
      const guide = document.createElement("div");
      guide.className = "pw-guide pw-guide-v";
      guide.style.left = `${x}px`;
      layer.appendChild(guide);
    }
    for (const y of guidesY) {
      const guide = document.createElement("div");
      guide.className = "pw-guide pw-guide-h";
      guide.style.top = `${y}px`;
      layer.appendChild(guide);
    }
  }

  function clearGuides(): void {
    layer.querySelectorAll(".pw-guide").forEach((el) => el.remove());
  }

  function pageBox(): Box {
    const [x0, y0, x1, y1] = (viewport?.viewBox as number[] | undefined) ?? [0, 0, 0, 0];
    const topLeft = viewport ? bboxToRect(viewport, [0, 0, x1 - x0, y1 - y0]) : { left: 0, top: 0, width: 0, height: 0 };
    return { left: topLeft.left, top: topLeft.top, right: topLeft.left + topLeft.width, bottom: topLeft.top + topLeft.height };
  }

  function marginPx(): number {
    return PAGE_MARGIN_PT * (viewport?.scale ?? 1);
  }

  /** Layer pixels -> page points, through the viewport (rotation-safe). */
  function toPoints(dx: number, dy: number): [number, number] {
    if (!viewport) {
      return [0, 0];
    }
    const [x0, y0] = viewportToMupdfPoint(viewport, 0, 0);
    const [x1, y1] = viewportToMupdfPoint(viewport, dx, dy);
    return [x1 - x0, y1 - y0];
  }

  function snapFor(moving: PageObject[], dx: number, dy: number, event: MouseEvent): [number, number] {
    if (event.altKey || moving.length === 0) {
      clearGuides();
      return [dx, dy];
    }
    const movingKeys = new Set(moving.map(key));
    const others = objects.filter((o) => !movingKeys.has(key(o))).map((o) => o.box);
    const result = snap(unionBox(moving.map((o) => o.box)), dx, dy, others, pageBox(), marginPx());
    drawGuides(result.guidesX, result.guidesY);
    return [result.dx, result.dy];
  }

  function refs(items: PageObject[], offsets?: [number, number][]): Record<string, unknown>[] {
    return items.map((object, i) => ({
      kind: object.kind,
      page_index: pageIndex,
      index: object.index,
      ...(offsets ? { dx: offsets[i][0], dy: offsets[i][1] } : {}),
    }));
  }

  async function send(op: HistoryOp, next: { kind: ObjectKind; pageRect: [number, number, number, number] }[]): Promise<void> {
    if (busy) {
      return;
    }
    busy = true;
    try {
      const applied = await applyWithApproval(options.api, options.documentId, op);
      if (!applied) {
        return;
      }
      await confirmVerified(options.api, options.documentId, applied.result);
      reselect = next;
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "The change could not be made.");
      return;
    } finally {
      busy = false;
      previewShift(0, 0);
      clearGuides();
    }
    options.onCommitted();
  }

  function shifted(object: PageObject, dx: number, dy: number): { kind: ObjectKind; pageRect: [number, number, number, number] } {
    const [x0, y0, x1, y1] = object.pageRect;
    return { kind: object.kind, pageRect: [x0 + dx, y0 + dy, x1 + dx, y1 + dy] };
  }

  function moveSelection(dxPt: number, dyPt: number): void {
    if (selected.length === 0 || (dxPt === 0 && dyPt === 0)) {
      return;
    }
    const items = [...selected];
    void send(
      { op: "move_objects", items: refs(items), dx: dxPt, dy: dyPt },
      items.map((o) => shifted(o, dxPt, dyPt)),
    );
  }

  function groupDrag(event: MouseEvent): void {
    event.preventDefault();
    event.stopPropagation();
    const startX = event.clientX;
    const startY = event.clientY;
    const moving = [...selected];
    const onMove = (e: MouseEvent): void => {
      const [dx, dy] = snapFor(moving, e.clientX - startX, e.clientY - startY, e);
      previewShift(dx, dy);
    };
    const onUp = (e: MouseEvent): void => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      const [dx, dy] = snapFor(moving, e.clientX - startX, e.clientY - startY, e);
      clearGuides();
      if (Math.hypot(dx, dy) < 3) {
        previewShift(0, 0);
        return;
      }
      const [dxPt, dyPt] = toPoints(dx, dy);
      moveSelection(dxPt, dyPt);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  }

  function pointerDown(kind: ObjectKind, index: number, event: MouseEvent): boolean {
    const object = find(kind, index);
    if (!object || event.button !== 0) {
      return false;
    }
    if (event.shiftKey) {
      event.preventDefault();
      event.stopPropagation();
      setSelection(isSelected(object) ? selected.filter((s) => key(s) !== key(object)) : [...selected, object]);
      return true;
    }
    if (selected.length > 1 && isSelected(object)) {
      groupDrag(event);
      return true;
    }
    return false;
  }

  function selectOnly(kind: ObjectKind, index: number): void {
    const object = find(kind, index);
    setSelection(object ? [object] : []);
  }

  function snapper(kind: ObjectKind, index: number): DragSnapper {
    const object = find(kind, index);
    return {
      adjust: (dx, dy, event) => (object ? snapFor([object], dx, dy, event) : [dx, dy]),
      end: clearGuides,
    };
  }

  function align(action: AlignAction): void {
    if (selected.length === 0 || (action.startsWith("distribute") && selected.length < 3)) {
      return;
    }
    const items = [...selected];
    // Page points throughout, so what is sent is exactly what was computed.
    const toBox = (r: [number, number, number, number]): Box => ({ left: r[0], top: r[1], right: r[2], bottom: r[3] });
    const [vx0, vy0, vx1, vy1] = (viewport?.viewBox as number[] | undefined) ?? [0, 0, 0, 0];
    const page: Box = { left: 0, top: 0, right: vx1 - vx0, bottom: vy1 - vy0 };
    const offsets = alignOffsets(items.map((o) => toBox(o.pageRect)), action, page, PAGE_MARGIN_PT);
    const moving = items
      .map((object, i) => ({ object, offset: offsets[i] }))
      .filter(({ offset }) => Math.abs(offset[0]) > 0.05 || Math.abs(offset[1]) > 0.05);
    if (moving.length === 0) {
      return;
    }
    void send(
      {
        op: "move_objects",
        items: refs(
          moving.map((m) => m.object),
          moving.map((m) => m.offset),
        ),
      },
      items.map((o, i) => shifted(o, offsets[i][0], offsets[i][1])),
    );
  }

  function flushNudge(): void {
    const { dx, dy } = nudge;
    nudge = { dx: 0, dy: 0, timer: null };
    moveSelection(dx, dy);
  }

  function handleKey(event: KeyboardEvent): boolean {
    const modifier = event.ctrlKey || event.metaKey;
    const lower = event.key.toLowerCase();
    if (modifier && lower === "c" && selected.length) {
      clipboard = { pageIndex, items: selected.map((o) => ({ kind: o.kind, index: o.index })) };
      return true;
    }
    if (modifier && (lower === "v" || lower === "d")) {
      const source = lower === "d" ? (selected.length ? { pageIndex, items: selected.map((o) => ({ kind: o.kind, index: o.index })) } : null) : clipboard;
      if (!source || busy) {
        return Boolean(source);
      }
      const target = lower === "d" ? pageIndex : options.currentPage();
      const offset = target === source.pageIndex ? PASTE_OFFSET_PT : 0;
      const originals = source.pageIndex === pageIndex ? source.items.map((i) => find(i.kind, i.index)) : [];
      void send(
        {
          op: "duplicate_objects",
          items: source.items.map((i) => ({ kind: i.kind, page_index: source.pageIndex, index: i.index })),
          dx: offset,
          dy: offset,
          target_page_index: target,
        },
        originals.filter((o): o is PageObject => o !== undefined).map((o) => shifted(o, offset, offset)),
      );
      return true;
    }
    if (selected.length === 0) {
      return false;
    }
    if (event.key === "Delete" || event.key === "Backspace") {
      void send({ op: "delete_objects", items: refs(selected) }, []);
      setSelection([]);
      return true;
    }
    if (event.key === "Escape") {
      setSelection([]);
      return true;
    }
    const step = event.shiftKey ? 10 : 1;
    const arrows: Record<string, [number, number]> = {
      ArrowLeft: [-step, 0],
      ArrowRight: [step, 0],
      ArrowUp: [0, -step],
      ArrowDown: [0, step],
    };
    const arrow = arrows[event.key];
    if (!arrow || busy) {
      return Boolean(arrow);
    }
    nudge.dx += arrow[0];
    nudge.dy += arrow[1];
    const scale = viewport?.scale ?? 1;
    previewShift(nudge.dx * scale, nudge.dy * scale);
    if (nudge.timer) {
      clearTimeout(nudge.timer);
    }
    nudge.timer = setTimeout(flushNudge, NUDGE_IDLE_MS);
    return true;
  }

  // Marquee: a drag that starts on empty page (not on any object box) selects
  // every object entirely inside the dragged rectangle.
  layer.parentElement?.addEventListener("mousedown", (event) => {
    const target = event.target as HTMLElement;
    const onEmptyPage = target === layer || target.tagName === "CANVAS";
    if (!onEmptyPage || event.button !== 0 || layer.classList.contains("pw-adding-text") || layer.querySelector(".pw-draw-surface")) {
      return;
    }
    const layerRect = layer.getBoundingClientRect();
    const ax = event.clientX - layerRect.left;
    const ay = event.clientY - layerRect.top;
    let band: HTMLElement | null = null;
    const onMove = (e: MouseEvent): void => {
      const bx = e.clientX - layerRect.left;
      const by = e.clientY - layerRect.top;
      if (!band && Math.hypot(bx - ax, by - ay) < MARQUEE_THRESHOLD_PX) {
        return;
      }
      band ??= layer.appendChild(Object.assign(document.createElement("div"), { className: "pw-marquee" }));
      band.style.left = `${Math.min(ax, bx)}px`;
      band.style.top = `${Math.min(ay, by)}px`;
      band.style.width = `${Math.abs(bx - ax)}px`;
      band.style.height = `${Math.abs(by - ay)}px`;
    };
    const onUp = (e: MouseEvent): void => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      if (!band) {
        if (!e.shiftKey) {
          setSelection([]); // a plain click on empty page
        }
        return;
      }
      band.remove();
      const bx = e.clientX - layerRect.left;
      const by = e.clientY - layerRect.top;
      const area: Box = { left: Math.min(ax, bx), top: Math.min(ay, by), right: Math.max(ax, bx), bottom: Math.max(ay, by) };
      const inside = objects.filter(
        (o) => o.box.left >= area.left && o.box.right <= area.right && o.box.top >= area.top && o.box.bottom <= area.bottom,
      );
      setSelection(e.shiftKey ? [...selected, ...inside.filter((o) => !isSelected(o))] : inside);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  });

  function update(
    nextPageIndex: number,
    nextViewport: pdfjsLib.PageViewport,
    blocks: BlockInfo[],
    images: ImageInfo[],
    shapes: ShapeInfo[],
  ): void {
    const samePage = nextPageIndex === pageIndex;
    const previous = samePage ? selected.map((o) => ({ kind: o.kind, pageRect: o.pageRect })) : [];
    pageIndex = nextPageIndex;
    viewport = nextViewport;
    const make = (kind: ObjectKind, index: number, spanIndices: number[], rect: [number, number, number, number]): PageObject => {
      const r = bboxToRect(nextViewport, rect);
      return { kind, index, spanIndices, pageRect: rect, box: { left: r.left, top: r.top, right: r.left + r.width, bottom: r.top + r.height } };
    };
    const shapeBoxes = new Set(
      [...layer.querySelectorAll<HTMLElement>(".pw-shape-box")].map((el) => Number(el.dataset.index)),
    );
    objects = [
      ...blocks.map((b) => make("text", b.span_indices[0], b.span_indices, b.bbox)),
      ...images.map((i) => make("image", i.index, [], i.rect)),
      // Only shapes the shape tool made selectable (not page backgrounds).
      ...shapes.filter((s) => shapeBoxes.has(s.index)).map((s) => make("shape", s.index, [], s.rect)),
    ];
    // Select again: the objects just changed, where they should be now; otherwise the
    // same objects as before, if they are still where they were (a zoom, say).
    const wanted = reselect ?? previous;
    reselect = null;
    const near = (a: number[], b: number[]): boolean => a.every((v, i) => Math.abs(v - b[i]) <= REFIND_TOLERANCE_PT);
    const found = wanted
      .map((w) => objects.find((o) => o.kind === w.kind && near(o.pageRect, w.pageRect)))
      .filter((o): o is PageObject => o !== undefined);
    setSelection([...new Map(found.map((o) => [key(o), o])).values()]);
  }

  return {
    update,
    pointerDown,
    selectOnly,
    snapper,
    count: () => selected.length,
    clear: () => setSelection([]),
    align,
    handleKey,
  };
}
